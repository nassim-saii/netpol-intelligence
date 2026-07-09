import os
import time
import json
import logging
import threading
import psycopg2
from http.server import HTTPServer, BaseHTTPRequestHandler
from kubernetes import client, config, watch
from rules import audit_namespace

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit-engine")

WATCHED_NAMESPACES = ["online-boutique", "netpol-system", "llm-system"]
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    return psycopg2.connect(DATABASE_URL)

def store_finding(finding):
    try:
        conn = get_db(); cur = conn.cursor()
        # Check if this exact finding already exists as unresolved — skip if so
        cur.execute("""
            SELECT COUNT(*) FROM audit_findings
            WHERE namespace = %s
              AND policy_name = %s
              AND rule_id = %s
              AND resolved = false
        """, (finding.namespace, finding.policy_name, finding.rule_id))
        if cur.fetchone()[0] > 0:
            cur.close(); conn.close()
            return  # Already exists, no duplicate needed
        cur.execute("""
            INSERT INTO audit_findings
              (rule_id, namespace, policy_name, severity, message, policy_yaml)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (finding.rule_id, finding.namespace, finding.policy_name,
              finding.severity, finding.message,
              json.dumps(finding.policy_yaml) if finding.policy_yaml else None))
        conn.commit(); cur.close(); conn.close()
        log.info(f"Stored finding {finding.rule_id} for {finding.namespace}")
    except Exception as e:
        log.error(f"DB store error: {e}")

def resolve_fixed_findings(namespace, current_findings):
    """Mark findings as resolved if they no longer appear in current scan."""
    try:
        # Build set of currently active (rule_id, policy_name) pairs
        active = {(f.rule_id, f.policy_name) for f in current_findings}

        conn = get_db(); cur = conn.cursor()

        # Get all unresolved findings for this namespace
        cur.execute("""
            SELECT DISTINCT rule_id, policy_name
            FROM audit_findings
            WHERE namespace = %s AND resolved = false
        """, (namespace,))
        existing = cur.fetchall()

        resolved_count = 0
        for row in existing:
            rule_id, policy_name = row[0], row[1]
            if (rule_id, policy_name) not in active:
                cur.execute("""
                    UPDATE audit_findings
                    SET resolved = true, resolved_at = NOW()
                    WHERE namespace = %s
                      AND rule_id = %s
                      AND (policy_name = %s OR (policy_name IS NULL AND %s IS NULL))
                      AND resolved = false
                """, (namespace, rule_id, policy_name, policy_name))
                resolved_count += cur.rowcount
                log.info(f"Resolved {cur.rowcount} findings: {namespace}/{rule_id}/{policy_name}")

        conn.commit(); cur.close(); conn.close()
        if resolved_count:
            log.info(f"Auto-resolved {resolved_count} fixed findings in {namespace}")
    except Exception as e:
        log.error(f"Resolve error: {e}")

def audit_namespace_full(ns, v1net):
    """Audit a namespace: store new findings + resolve fixed ones."""
    policies = v1net.list_namespaced_network_policy(ns).items
    findings = audit_namespace(ns, policies)
    for f in findings:
        log.warning(f"[{f.rule_id}] {f.severity} - {f.message}")
        store_finding(f)
    resolve_fixed_findings(ns, findings)
    log.info(f"Namespace {ns}: {len(findings)} active violations")
    return findings

def audit_all():
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()
    v1net = client.NetworkingV1Api()
    for ns in WATCHED_NAMESPACES:
        try:
            audit_namespace_full(ns, v1net)
        except Exception as e:
            log.error(f"Error auditing {ns}: {e}")

# ── HTTP trigger server ────────────────────────────────────────────────────
RESCAN_REQUESTED = threading.Event()

class TriggerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/trigger-rescan":
            RESCAN_REQUESTED.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"rescan triggered"}')
            log.info("Rescan triggered via HTTP")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress access logs

def run_http_server():
    server = HTTPServer(("0.0.0.0", 8081), TriggerHandler)
    log.info("Trigger HTTP server listening on :8081")
    server.serve_forever()

# ── Watch loop ─────────────────────────────────────────────────────────────
LAST_AUDIT = {}
AUDIT_COOLDOWN = 300

def should_audit(namespace):
    now = time.time()
    if now - LAST_AUDIT.get(namespace, 0) > AUDIT_COOLDOWN:
        LAST_AUDIT[namespace] = now
        return True
    return False

def watch_networkpolicies():
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()
    v1net = client.NetworkingV1Api()
    w = watch.Watch()
    log.info("Starting NetworkPolicy watch...")
    while True:
        try:
            # Check for manual rescan BEFORE waiting for events
            if RESCAN_REQUESTED.is_set():
                RESCAN_REQUESTED.clear()
                log.info("Processing manual rescan request...")
                for ns in WATCHED_NAMESPACES:
                    LAST_AUDIT[ns] = 0  # reset cooldown
                for ns in WATCHED_NAMESPACES:
                    try:
                        audit_namespace_full(ns, v1net)
                    except Exception as e:
                        log.error(f"Rescan error {ns}: {e}")
                log.info("Manual rescan complete")

            for event in w.stream(
                v1net.list_network_policy_for_all_namespaces,
                timeout_seconds=10
            ):
                # Also check during event processing
                if RESCAN_REQUESTED.is_set():
                    RESCAN_REQUESTED.clear()
                    log.info("Processing manual rescan request...")
                    for ns in WATCHED_NAMESPACES:
                        LAST_AUDIT[ns] = 0
                    for ns in WATCHED_NAMESPACES:
                        try:
                            audit_namespace_full(ns, v1net)
                        except Exception as e:
                            log.error(f"Rescan error {ns}: {e}")
                    log.info("Manual rescan complete")

                ns = event["object"].metadata.namespace
                if ns not in WATCHED_NAMESPACES:
                    continue
                etype = event["type"]
                name = event["object"].metadata.name
                log.info(f"Event {etype}: {ns}/{name}")
                if not should_audit(ns):
                    continue
                audit_namespace_full(ns, v1net)
        except Exception as e:
            log.error(f"Watch error: {e} - retrying in 10s")
            time.sleep(10)

if __name__ == "__main__":
    # Start HTTP trigger server in background thread
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()

    log.info("Audit Engine starting - initial full audit...")
    audit_all()
    log.info("Starting watch loop...")
    watch_networkpolicies()
