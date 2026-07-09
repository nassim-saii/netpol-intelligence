import os
import time
import pickle
import logging
import numpy as np
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, timedelta
from sklearn.ensemble import IsolationForest
from rules import evaluate_rules, FlowEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_URL               = os.environ["DATABASE_URL"]
POLL_INTERVAL        = int(os.environ.get("POLL_INTERVAL", "60"))
ZSCORE_THRESHOLD     = float(os.environ.get("ZSCORE_THRESHOLD", "3.0"))
MIN_SAMPLES          = 3
MODEL_PATH           = os.environ.get("IFOREST_MODEL_PATH", "/tmp/iforest_model.pkl")
IFOREST_THRESHOLD    = float(os.environ.get("IFOREST_THRESHOLD", "-0.55"))
IFOREST_MIN_TRAIN    = int(os.environ.get("IFOREST_MIN_TRAIN", "50"))
IFOREST_RETRAIN_EVERY = int(os.environ.get("IFOREST_RETRAIN_EVERY", "30"))

# ── IForest state (module-level, survives across poll cycles) ─────────────────
_iforest_model       = None
_iforest_train_buf   = []   # accumulates (feature_row, label_hint) between retrains
_iforest_cycle       = 0


def get_conn():
    return psycopg2.connect(DB_URL)


def build_feature(count, dst_port, verdict, mean_rate, std_dev):
    """5-float feature vector from columns already in memory."""
    is_drop     = 1.0 if verdict == "drop" else 0.0
    count_ratio = count / max(mean_rate, 0.001)   # how far above mean
    return [float(count), float(dst_port), is_drop, float(count_ratio), float(std_dev)]


def maybe_retrain():
    """Retrain IForest when enough samples have accumulated."""
    global _iforest_model, _iforest_train_buf, _iforest_cycle
    _iforest_cycle += 1
    if (len(_iforest_train_buf) < IFOREST_MIN_TRAIN or
            _iforest_cycle % IFOREST_RETRAIN_EVERY != 0):
        return
    X = np.array(_iforest_train_buf)
    model = IsolationForest(
        n_estimators=100,
        max_samples="auto",
        contamination=0.01,
        random_state=42,
        n_jobs=1
    )
    model.fit(X)
    _iforest_model = model
    _iforest_train_buf = []   # reset buffer after retrain
    try:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
    except Exception as e:
        log.warning("Could not persist model to %s: %s", MODEL_PATH, e)
    log.info("IForest retrained on %d samples (cycle %d)", len(X), _iforest_cycle)


def poll_and_detect(conn):
    global _iforest_train_buf

    now          = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=POLL_INTERVAL)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT src_namespace, dst_namespace, dst_ip, dst_port, verdict,
                   SUM(count) AS total_count
            FROM flow_events
            WHERE time >= %s AND time < %s
            GROUP BY src_namespace, dst_namespace, dst_ip, dst_port, verdict
        """, (window_start, now))
        flows = cur.fetchall()

    if not flows:
        log.info("No flow events in window — skipping")
        maybe_retrain()
        return

    log.info("Processing %d flow tuples", len(flows))
    anomalies = 0

    with conn.cursor() as cur:
        for flow in flows:
            src_ns   = flow["src_namespace"]
            dst_ns   = flow["dst_namespace"]
            dst_port = flow["dst_port"] or 0
            verdict  = flow["verdict"]
            dst_ip   = flow["dst_ip"] or ""
            count    = float(flow["total_count"])
            flow_key = f"{src_ns}|{dst_ns}|{dst_port}"  # matches flow_baseline key format (no verdict)

            # ── Tier 1: Rule-based ────────────────────────────────────────
            fe = FlowEvent(
                src_namespace=src_ns, dst_namespace=dst_ns,
                src_ip="", dst_ip=dst_ip,
                dst_port=dst_port, verdict=verdict, timestamp=now
            )
            for hit in evaluate_rules(fe):
                cur.execute("""
                    SELECT 1 FROM anomaly_events
                    WHERE rule_id = %s AND src_namespace = %s AND dst_namespace = %s
                      AND dst_port = %s AND time > NOW() - INTERVAL '1 hour'
                    LIMIT 1
                """, (hit["rule"], src_ns, dst_ns, dst_port))
                if cur.fetchone():
                    log.debug("DEDUP SKIP %s %s->%s:%s", hit["rule"], src_ns, dst_ns, dst_port)
                    continue
                cur.execute("""
                    INSERT INTO anomaly_events
                      (time, anomaly_type, rule_id, src_namespace, dst_namespace,
                       dst_port, severity, description)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    now, "RULE_BASED", hit["rule"],
                    src_ns, dst_ns, dst_port,
                    hit["severity"], hit["name"]
                ))
                anomalies += 1
                log.warning("RULE HIT %s [%s] %s->%s:%s",
                            hit["rule"], hit["severity"], src_ns, dst_ns, dst_port)

            # ── Tier 2: Z-score ───────────────────────────────────────────
            cur.execute("""
                SELECT mean_rate, std_dev, sample_count
                FROM flow_baseline WHERE flow_key = %s
            """, (flow_key,))
            row = cur.fetchone()

            if row and row[2] >= MIN_SAMPLES:
                mean, std, n = row[0], row[1], row[2]
                if std > 0:
                    z = abs((count - mean) / std)
                    if z > ZSCORE_THRESHOLD:
                        severity = "HIGH" if z > 5 else "MEDIUM"
                        cur.execute("""
                            SELECT 1 FROM anomaly_events
                            WHERE rule_id = 'ZSCORE-001' AND src_namespace = %s
                              AND dst_namespace = %s AND dst_port = %s
                              AND time > NOW() - INTERVAL '1 hour'
                            LIMIT 1
                        """, (src_ns, dst_ns, dst_port))
                        if not cur.fetchone():
                            cur.execute("""
                                INSERT INTO anomaly_events
                                  (time, anomaly_type, rule_id, src_namespace, dst_namespace,
                                   dst_port, z_score, anomaly_score, severity, description)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                now, "TRAFFIC_SPIKE", "ZSCORE-001",
                                src_ns, dst_ns, dst_port,
                                round(z, 2), round(z, 2), severity,
                                f"Traffic spike: {count:.0f} drops (mean={mean:.1f}, std={std:.1f}, z={z:.2f})"
                            ))
                        anomalies += 1
                        log.warning("ZSCORE z=%.2f [%s] %s->%s:%s",
                                    z, severity, src_ns, dst_ns, dst_port)

            # ── Welford baseline update ───────────────────────────────────
            if row:
                mean_old, std_old, n = row[0], row[1], row[2]
                n_new    = n + 1
                mean_new = mean_old + (count - mean_old) / n_new
                m2_old   = (std_old ** 2) * n
                m2_new   = m2_old + (count - mean_old) * (count - mean_new)
                std_new  = (m2_new / n_new) ** 0.5 if n_new > 1 else 0.0
                cur.execute("""
                    UPDATE flow_baseline
                    SET mean_rate=%s, std_dev=%s, sample_count=%s, last_updated=%s
                    WHERE flow_key=%s
                """, (mean_new, std_new, n_new, now, flow_key))
            else:
                cur.execute("""
                    INSERT INTO flow_baseline
                      (flow_key, mean_rate, std_dev, sample_count, last_updated)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (flow_key) DO UPDATE
                    SET mean_rate=EXCLUDED.mean_rate,
                        last_updated=EXCLUDED.last_updated
                """, (flow_key, count, 0.0, 1, now))

            # ── Tier 3: Isolation Forest ──────────────────────────────────
            if row and row[2] >= MIN_SAMPLES:
                feat = build_feature(count, dst_port, verdict, row[0], row[1])
                _iforest_train_buf.append(feat)

                if _iforest_model is not None:
                    score = float(_iforest_model.score_samples([feat])[0])
                    if score < IFOREST_THRESHOLD:
                        cur.execute("""
                            SELECT 1 FROM anomaly_events
                            WHERE rule_id = 'IFOREST-001'
                              AND src_namespace = %s AND dst_namespace = %s
                              AND dst_port = %s
                              AND time > NOW() - INTERVAL '1 hour'
                            LIMIT 1
                        """, (src_ns, dst_ns, dst_port))
                        if not cur.fetchone():
                            cur.execute("""
                                INSERT INTO anomaly_events
                                  (time, anomaly_type, rule_id,
                                   src_namespace, dst_namespace, dst_port,
                                   anomaly_score, severity, description)
                                VALUES (%s,'ML_IFOREST','IFOREST-001',%s,%s,%s,%s,%s,%s)
                            """, (
                                now, src_ns, dst_ns, dst_port,
                                round(score, 4), "MEDIUM",
                                f"IForest anomaly: score={score:.4f} "                                f"count={count:.0f} mean={row[0]:.1f} std={row[1]:.1f}"
                            ))
                            anomalies += 1
                            log.warning("IFOREST score=%.4f [MEDIUM] %s->%s:%s",
                                        score, src_ns, dst_ns, dst_port)

        conn.commit()

    maybe_retrain()
    log.info("Cycle done: %d tuples, %d anomalies | iforest_buf=%d model=%s",
             len(flows), anomalies, len(_iforest_train_buf),
             "ready" if _iforest_model else "warming-up")


def main():
    log.info("Anomaly Detector starting — interval=%ds zscore_thr=%.1f "             "iforest_thr=%.2f min_train=%d retrain_every=%d cycles",
             POLL_INTERVAL, ZSCORE_THRESHOLD,
             IFOREST_THRESHOLD, IFOREST_MIN_TRAIN, IFOREST_RETRAIN_EVERY)

    # Try loading a persisted model from a previous pod run
    global _iforest_model
    if os.path.exists(MODEL_PATH):
        try:
            with open(MODEL_PATH, "rb") as f:
                _iforest_model = pickle.load(f)
            log.info("IForest model loaded from %s", MODEL_PATH)
        except Exception as e:
            log.warning("Could not load persisted model: %s — will retrain", e)

    conn = None
    while conn is None:
        try:
            conn = get_conn()
            log.info("DB connection OK")
        except Exception as e:
            log.warning("DB not ready: %s — retry in 10s", e)
            time.sleep(10)

    while True:
        try:
            poll_and_detect(conn)
        except psycopg2.OperationalError as e:
            log.error("DB connection lost: %s — reconnecting", e)
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            while conn is None:
                try:
                    conn = get_conn()
                except Exception as e2:
                    log.error("Reconnect failed: %s — retry in 10s", e2)
                    time.sleep(10)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
