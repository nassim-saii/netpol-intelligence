import os
import time
import json
import logging
import re
import psycopg2
import httpx
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flow-processor")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
LOKI_URL = os.environ.get("LOKI_URL",
    "http://loki.netpol-system.svc.cluster.local:3100")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

def get_db():
    return psycopg2.connect(DATABASE_URL)

def query_loki(start_ts, end_ts, limit=1000):
    """Query Loki for OVN ACL logs in time range."""
    try:
        resp = httpx.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": '{job="ovn-acl"} | json | verdict="drop"',
                "start": str(int(start_ts.timestamp() * 1e9)),
                "end":   str(int(end_ts.timestamp() * 1e9)),
                "limit": limit,
                "direction": "forward"
            },
            timeout=30.0
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Loki query error: {e}")
        return None

def parse_flow(log_line):
    """Extract src/dst namespace from policy name and flow fields."""
    try:
        data = json.loads(log_line) if isinstance(log_line, str) else log_line
        policy_name = data.get("policy_name", "")
        verdict     = data.get("verdict", "")
        flow        = data.get("flow", "")
        namespace   = ""
        if "NP:" in policy_name:
            parts = policy_name.split(":")
            if len(parts) >= 2:
                namespace = parts[1]
        src_ip, dst_ip, dst_port, protocol = "", "", None, "tcp"
        m = re.search(r"nw_src=([\d.]+)", flow)
        if m: src_ip = m.group(1)
        m = re.search(r"nw_dst=([\d.]+)", flow)
        if m: dst_ip = m.group(1)
        m = re.search(r"tp_dst=(\d+)", flow)
        if m: dst_port = int(m.group(1))
        if "udp" in flow: protocol = "udp"
        return {
            "src_namespace": namespace,
            "dst_namespace": namespace,
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "protocol": protocol,
            "verdict": verdict,
            "policy_name": policy_name
        }
    except Exception as e:
        log.debug(f"Parse error: {e}")
        return None

def store_flows(flows):
    """Batch insert flow events into TimescaleDB."""
    if not flows:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.executemany("""
            INSERT INTO flow_events
              (src_namespace, dst_namespace, src_ip, dst_ip,
               dst_port, protocol, verdict, policy_name)
            VALUES (%(src_namespace)s, %(dst_namespace)s, %(src_ip)s,
                    %(dst_ip)s, %(dst_port)s, %(protocol)s,
                    %(verdict)s, %(policy_name)s)
        """, flows)
        conn.commit()
        cur.close()
        conn.close()
        log.info(f"Stored {len(flows)} flow events")
    except Exception as e:
        log.error(f"DB error storing flows: {e}")

def update_baseline(flows):
    """Update flow_baseline with rolling stats."""
    if not flows:
        return
    counts = {}
    for f in flows:
        key = f"{f['src_namespace']}|{f['dst_namespace']}|{f['dst_port']}"
        counts[key] = counts.get(key, 0) + 1
    try:
        conn = get_db()
        cur = conn.cursor()
        for key, count in counts.items():
            cur.execute("""
                INSERT INTO flow_baseline
                  (flow_key, mean_rate, std_dev, sample_count, last_updated)
                VALUES (%s, %s, 0, 1, NOW())
                ON CONFLICT (flow_key) DO UPDATE SET
                  mean_rate = (flow_baseline.mean_rate *
                    flow_baseline.sample_count + %s) /
                    (flow_baseline.sample_count + 1),
                  sample_count = flow_baseline.sample_count + 1,
                  last_updated = NOW()
            """, (key, count, count))
        conn.commit()
        cur.close()
        conn.close()
        log.info(f"Updated baseline for {len(counts)} flow keys")
    except Exception as e:
        log.error(f"DB error updating baseline: {e}")

def process_window(start, end):
    """Process one time window of Loki logs."""
    log.info(f"Processing window {start} -> {end}")
    result = query_loki(start, end)
    if not result:
        return
    streams = result.get("data", {}).get("result", [])
    flows = []
    for stream in streams:
        for ts, line in stream.get("values", []):
            flow = parse_flow(line)
            if flow and flow["src_namespace"]:
                flows.append(flow)
    log.info(f"Parsed {len(flows)} flows from Loki")
    store_flows(flows)
    update_baseline(flows)

def run():
    log.info("Flow Processor starting...")
    last_run = datetime.utcnow() - timedelta(minutes=5)
    while True:
        now = datetime.utcnow()
        process_window(last_run, now)
        last_run = now
        log.info(f"Sleeping {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
