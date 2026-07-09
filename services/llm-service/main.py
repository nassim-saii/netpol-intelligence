import os
import time
import logging
import json
import psycopg2
import psycopg2.extras
from prompts import ANOMALY_PROMPT, AUDIT_PROMPT, ANOMALY_HASH_KEY, AUDIT_HASH_KEY
from client import call_ollama, check_ollama_health, make_prompt_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

DB_URL        = os.environ["DATABASE_URL"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
BATCH_SIZE    = int(os.environ.get("BATCH_SIZE", "3"))
MODEL         = "llama3.2:3b"
CACHE_TTL_H   = int(os.environ.get("CACHE_TTL_HOURS", "24"))


def get_conn():
    return psycopg2.connect(DB_URL)


# ── Cache helpers ──────────────────────────────────────────────────────────────

def get_cached_response(cur, prompt_hash: str) -> dict | None:
    """Return cached LLM response if the same prompt was analyzed within CACHE_TTL_H hours."""
    cur.execute("""
        SELECT response FROM llm_analyses
        WHERE prompt_hash = %s
          AND time > NOW() - INTERVAL '1 hour' * %s
          AND response IS NOT NULL
          AND response::text NOT LIKE '%%"error"%%'
        ORDER BY time DESC
        LIMIT 1
    """, (prompt_hash, CACHE_TTL_H))
    row = cur.fetchone()
    if row:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return None


def already_analyzed(cur, source_type: str, source_id: int) -> bool:
    cur.execute(
        "SELECT 1 FROM llm_analyses WHERE source_type=%s AND source_id=%s LIMIT 1",
        (source_type, source_id)
    )
    return cur.fetchone() is not None


def already_analyzed_similar(cur, row, window_hours: int = 168) -> bool:
    """Skip if same anomaly pattern was analyzed recently."""
    cur.execute("""
        SELECT 1 FROM llm_analyses la
        JOIN anomaly_events ae ON la.source_id = ae.id AND la.source_type = 'anomaly'
        WHERE ae.anomaly_type = %s
          AND ae.src_namespace = %s
          AND ae.dst_namespace = %s
          AND ae.dst_port = %s
          AND la.time > NOW() - INTERVAL '1 hour' * %s
        LIMIT 1
    """, (row["anomaly_type"], row["src_namespace"],
          row["dst_namespace"], row["dst_port"], window_hours))
    return cur.fetchone() is not None


def already_analyzed_similar_audit(cur, row, window_hours: int = 168) -> bool:
    """Skip if same audit pattern (rule+namespace+policy) was analyzed recently."""
    cur.execute("""
        SELECT 1 FROM llm_analyses la
        JOIN audit_findings af ON la.source_id = af.id AND la.source_type = 'audit'
        WHERE af.rule_id = %s
          AND af.namespace = %s
          AND af.policy_name = %s
          AND la.time > NOW() - INTERVAL '1 hour' * %s
        LIMIT 1
    """, (row["rule_id"], row["namespace"], row["policy_name"], window_hours))
    return cur.fetchone() is not None


def store_analysis(cur, source_type: str, source_id: int,
                   prompt_hash: str, response: dict, latency_ms: int):
    cur.execute("""
        INSERT INTO llm_analyses
          (time, source_type, source_id, model, prompt_hash, response, latency_ms)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s)
    """, (
        source_type, source_id, MODEL,
        prompt_hash, json.dumps(response), latency_ms
    ))


def store_cached_hit(cur, source_type: str, source_id: int,
                     prompt_hash: str, response: dict):
    """Store a cache-hit row — latency 0 signals it was served from cache."""
    cur.execute("""
        INSERT INTO llm_analyses
          (time, source_type, source_id, model, prompt_hash, response, latency_ms)
        VALUES (NOW(), %s, %s, %s, %s, %s, 0)
    """, (source_type, source_id, MODEL, prompt_hash, json.dumps(response)))


def find_existing_audit_analysis(cur, rule_id: str, namespace: str, policy_name: str):
    """Check if an analysis already exists for this rule+namespace+policy combo."""
    cur.execute("""
        SELECT la.id FROM llm_analyses la
        JOIN audit_findings af ON la.source_id = af.id AND la.source_type = 'audit'
        WHERE af.rule_id = %s AND af.namespace = %s AND af.policy_name = %s
        ORDER BY la.time DESC LIMIT 1
    """, (rule_id, namespace, policy_name))
    row = cur.fetchone()
    return row[0] if row else None


def update_analysis_timestamp(cur, analysis_id: int, new_source_id: int):
    cur.execute("""
        UPDATE llm_analyses
        SET time = NOW(), source_id = %s
        WHERE id = %s
    """, (new_source_id, analysis_id))


# ── Processors ────────────────────────────────────────────────────────────────

def process_anomalies(conn):
    """Analyze unprocessed anomaly_events with prompt-hash caching."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, anomaly_type, rule_id, src_namespace, dst_namespace,
                   dst_port, z_score, severity, description, time
            FROM anomaly_events
            ORDER BY time DESC
            LIMIT %s
        """, (BATCH_SIZE * 3,))
        rows = cur.fetchall()

    processed = 0
    with conn.cursor() as cur:
        for row in rows:
            if processed >= BATCH_SIZE:
                break
            if already_analyzed(cur, "anomaly", row["id"]):
                continue
            if already_analyzed_similar(cur, row, window_hours=168):
                log.debug("Skipping similar anomaly id=%d [%s] %s->%s:%s",
                          row["id"], row["severity"],
                          row["src_namespace"], row["dst_namespace"], row["dst_port"])
                continue

            prompt = ANOMALY_PROMPT.format(
                anomaly_type  = row["anomaly_type"] or "",
                rule_id       = row["rule_id"] or "",
                src_namespace = row["src_namespace"] or "",
                dst_namespace = row["dst_namespace"] or "",
                dst_port      = row["dst_port"] or 0,
                severity      = row["severity"] or "",
                description   = row["description"] or "",
                z_score       = row["z_score"] or "N/A",
                detected_at   = str(row["time"])
            )
            hash_key = ANOMALY_HASH_KEY.format(
                anomaly_type  = row["anomaly_type"] or "",
                rule_id       = row["rule_id"] or "",
                src_namespace = row["src_namespace"] or "",
                dst_namespace = row["dst_namespace"] or "",
                dst_port      = str(row["dst_port"] or 0),
                severity      = row["severity"] or "",
                description   = row["description"] or ""
            )
            prompt_hash = make_prompt_hash(hash_key)

            # ── Cache check ──
            cached = get_cached_response(cur, prompt_hash)
            if cached:
                log.info("CACHE HIT anomaly id=%d [%s] — skipping Ollama",
                         row["id"], row["severity"])
                store_cached_hit(cur, "anomaly", row["id"], prompt_hash, cached)
                conn.commit()
                processed += 1
                continue

            # ── Cache miss → call Ollama ──
            log.info("Analyzing anomaly id=%d [%s] %s->%s:%s",
                     row["id"], row["severity"],
                     row["src_namespace"], row["dst_namespace"], row["dst_port"])

            t0 = time.time()
            response = call_ollama(prompt)
            latency  = int((time.time() - t0) * 1000)

            store_analysis(cur, "anomaly", row["id"], prompt_hash, response, latency)
            conn.commit()
            processed += 1
            log.info("Anomaly id=%d analyzed in %dms — priority=%s",
                     row["id"], latency, response.get("priority", "?"))

    return processed


def process_audit_findings(conn):
    """Analyze unprocessed audit_findings with prompt-hash caching."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, rule_id, namespace, severity, policy_name, message, time
            FROM audit_findings
            WHERE severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')
            ORDER BY time DESC
            LIMIT %s
        """, (BATCH_SIZE * 3,))
        rows = cur.fetchall()

    processed = 0
    with conn.cursor() as cur:
        for row in rows:
            if processed >= BATCH_SIZE:
                break
            if already_analyzed(cur, "audit", row["id"]):
                continue
            if already_analyzed_similar_audit(cur, row, window_hours=168):
                log.debug("Skipping similar audit id=%d [%s] ns=%s rule=%s",
                          row["id"], row["severity"], row["namespace"], row["rule_id"])
                continue

            existing_id = find_existing_audit_analysis(
                cur, row["rule_id"], row["namespace"], row["policy_name"])
            if existing_id:
                update_analysis_timestamp(cur, existing_id, row["id"])
                conn.commit()
                log.info("Audit id=%d — updated existing analysis #%d timestamp",
                         row["id"], existing_id)
                processed += 1
                continue

            prompt = AUDIT_PROMPT.format(
                rule_id     = row["rule_id"] or "",
                namespace   = row["namespace"] or "",
                severity    = row["severity"] or "",
                policy_name = row["policy_name"] or "N/A",
                message     = row["message"] or "",
                detected_at = str(row["time"])
            )
            hash_key = AUDIT_HASH_KEY.format(
                rule_id     = row["rule_id"] or "",
                namespace   = row["namespace"] or "",
                severity    = row["severity"] or "",
                policy_name = row["policy_name"] or "N/A",
                message     = row["message"] or ""
            )
            prompt_hash = make_prompt_hash(hash_key)

            # ── Cache check ──
            cached = get_cached_response(cur, prompt_hash)
            if cached:
                log.info("CACHE HIT audit id=%d [%s] ns=%s rule=%s — skipping Ollama",
                         row["id"], row["severity"], row["namespace"], row["rule_id"])
                store_cached_hit(cur, "audit", row["id"], prompt_hash, cached)
                conn.commit()
                processed += 1
                continue

            # ── Cache miss → call Ollama ──
            log.info("Analyzing audit finding id=%d [%s] ns=%s rule=%s",
                     row["id"], row["severity"], row["namespace"], row["rule_id"])

            t0 = time.time()
            response = call_ollama(prompt)
            latency  = int((time.time() - t0) * 1000)

            store_analysis(cur, "audit", row["id"], prompt_hash, response, latency)
            conn.commit()
            processed += 1
            log.info("Audit id=%d analyzed in %dms — priority=%s",
                     row["id"], latency, response.get("priority", "?"))

    return processed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("LLM Analysis Service starting — poll=%ds batch=%d model=%s cache_ttl=%dh",
             POLL_INTERVAL, BATCH_SIZE, MODEL, CACHE_TTL_H)

    conn = None
    while conn is None:
        try:
            conn = get_conn()
            log.info("DB connection OK")
        except Exception as e:
            log.warning("DB not ready: %s — retry in 10s", e)
            time.sleep(10)

    log.info("Waiting for Ollama...")
    while not check_ollama_health():
        log.warning("Ollama not ready — retry in 15s")
        time.sleep(15)
    log.info("Ollama is ready")

    while True:
        try:
            a = process_anomalies(conn)
            f = process_audit_findings(conn)
            log.info("Cycle complete: %d anomalies + %d audit findings analyzed", a, f)
        except psycopg2.OperationalError as e:
            log.error("DB lost: %s — reconnecting", e)
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            while conn is None:
                try:
                    conn = get_conn()
                except Exception as e2:
                    log.error("Reconnect failed: %s", e2)
                    time.sleep(10)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
