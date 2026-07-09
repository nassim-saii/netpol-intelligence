import os, logging, json, threading, time as _time
from collections import defaultdict
from typing import Optional
import psycopg2, psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("api-gateway")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

app = FastAPI(title="NetPol Intelligence API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── API Key Auth ───────────────────────────────────────────────────────────────
from fastapi import Security, Depends
from fastapi.security import APIKeyHeader

API_KEY = os.environ.get("API_KEY", "netpol-demo-2026")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_key(
    header_key: str = Security(_api_key_header),
    query_key: Optional[str] = Query(default=None, alias="api_key")
):
    key = header_key or query_key
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return key

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


WATCHED_NS = os.environ.get("WATCHED_NAMESPACES", "online-boutique,netpol-system,llm-system").split(",")
_pod_cache = defaultdict(list)
_netpol_cache = defaultdict(list)
_cache_lock = threading.Lock()
_cache_ready = threading.Event()

def _init_k8s():
    from kubernetes import client, config, watch as kw
    try: config.load_incluster_config()
    except: config.load_kube_config()
    return client, kw

def _pod_info(p):
    cs = p.status.container_statuses or []
    return {"name": p.metadata.name, "namespace": p.metadata.namespace,
        "status": p.status.phase or "Unknown",
        "ready": all(c.ready for c in cs) if cs else False,
        "restarts": sum(c.restart_count for c in cs),
        "labels": dict(p.metadata.labels or {}),
        "node": p.spec.node_name or ""}

def _np_info(n):
    s = n.spec; ps = s.pod_selector
    return {"name": n.metadata.name, "namespace": n.metadata.namespace,
        "pod_selector": dict(ps.match_labels) if ps and ps.match_labels else {},
        "policy_types": list(s.policy_types or []),
        "ingress_rules": len(s.ingress or []), "egress_rules": len(s.egress or [])}

def _watch_pods():
    kc, kw = _init_k8s(); v1 = kc.CoreV1Api()
    log.info("Pod watcher starting: %s", WATCHED_NS)
    while True:
        try:
            for ns in WATCHED_NS:
                with _cache_lock:
                    _pod_cache[ns] = [_pod_info(p) for p in v1.list_namespaced_pod(ns).items]
            _cache_ready.set()
            log.info("Pod cache ready: %s", {n: len(_pod_cache[n]) for n in WATCHED_NS})
            w = kw.Watch()
            for ev in w.stream(v1.list_pod_for_all_namespaces, timeout_seconds=300):
                p = ev["object"]; ns = p.metadata.namespace
                if ns not in WATCHED_NS: continue
                with _cache_lock:
                    _pod_cache[ns][:] = [x for x in _pod_cache[ns] if x["name"] != p.metadata.name]
                    if ev["type"] in ("ADDED","MODIFIED"):
                        _pod_cache[ns].append(_pod_info(p))
        except Exception as e:
            log.warning("Pod watcher: %s — retry 5s", e); _time.sleep(5)

def _watch_netpols():
    kc, kw = _init_k8s(); v1n = kc.NetworkingV1Api()
    log.info("NetPol watcher starting: %s", WATCHED_NS)
    while True:
        try:
            for ns in WATCHED_NS:
                with _cache_lock:
                    _netpol_cache[ns] = [_np_info(n) for n in v1n.list_namespaced_network_policy(ns).items]
            log.info("NetPol cache ready: %s", {n: len(_netpol_cache[n]) for n in WATCHED_NS})
            w = kw.Watch()
            for ev in w.stream(v1n.list_network_policy_for_all_namespaces, timeout_seconds=300):
                n = ev["object"]; ns = n.metadata.namespace
                if ns not in WATCHED_NS: continue
                with _cache_lock:
                    _netpol_cache[ns][:] = [x for x in _netpol_cache[ns] if x["name"] != n.metadata.name]
                    if ev["type"] in ("ADDED","MODIFIED"):
                        _netpol_cache[ns].append(_np_info(n))
                log.info("NetPol %s: %s/%s", ev["type"], ns, n.metadata.name)
        except Exception as e:
            log.warning("NetPol watcher: %s — retry 5s", e); _time.sleep(5)

threading.Thread(target=_watch_pods, daemon=True).start()
threading.Thread(target=_watch_netpols, daemon=True).start()

@app.get("/health")
def health():
    try:
        conn = get_conn(); conn.cursor().execute("SELECT 1"); conn.close()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/api/health/deep")
def health_deep(auth: str = Depends(verify_key)):
    import httpx as _httpx
    checks = {}
    overall = "ok"

    # TimescaleDB
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM flow_events WHERE time > NOW() - INTERVAL '5 minutes'")
        recent_flows = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM llm_analyses")
        llm_count = cur.fetchone()["cnt"]
        conn.close()
        checks["timescaledb"] = {"status": "ok", "recent_flows_5m": recent_flows, "llm_analyses_total": llm_count}
    except Exception as e:
        checks["timescaledb"] = {"status": "error", "detail": str(e)}
        overall = "degraded"

    # Ollama
    try:
        r = _httpx.get("http://ollama.llm-system.svc.cluster.local:11434/api/tags", timeout=3.0)
        if r.status_code == 200:
            models = [m.get("name") for m in r.json().get("models", [])]
            checks["ollama"] = {"status": "ok", "models": models}
        else:
            checks["ollama"] = {"status": "degraded", "http_status": r.status_code}
            overall = "degraded"
    except Exception as e:
        checks["ollama"] = {"status": "unreachable", "detail": str(e)}
        overall = "degraded"

    # Loki
    try:
        r = _httpx.get("http://loki.netpol-system.svc.cluster.local:3100/ready", timeout=3.0)
        if r.status_code == 200:
            checks["loki"] = {"status": "ok"}
        else:
            checks["loki"] = {"status": "degraded", "http_status": r.status_code}
            overall = "degraded"
    except Exception as e:
        checks["loki"] = {"status": "unreachable", "detail": str(e)}
        overall = "degraded"

    # Pipeline freshness — detect silent failures
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""
            SELECT
              MAX(time) as last_flow,
              EXTRACT(EPOCH FROM (NOW() - MAX(time)))/60 as flow_lag_min
            FROM flow_events
        """)
        row = cur.fetchone()
        cur.execute("SELECT MAX(time) as last_anomaly FROM anomaly_events")
        last_anomaly = cur.fetchone()["last_anomaly"]
        cur.execute("SELECT MAX(time) as last_llm FROM llm_analyses")
        last_llm = cur.fetchone()["last_llm"]
        conn.close()
        flow_lag = float(row["flow_lag_min"]) if row["flow_lag_min"] else None
        checks["pipeline"] = {
            "status": "ok" if (flow_lag is not None and flow_lag < 10) else "stale",
            "last_flow_event": row["last_flow"].isoformat() if row["last_flow"] else None,
            "flow_lag_minutes": round(flow_lag, 1) if flow_lag else None,
            "last_anomaly": last_anomaly.isoformat() if last_anomaly else None,
            "last_llm_analysis": last_llm.isoformat() if last_llm else None,
        }
        if flow_lag and flow_lag > 10:
            overall = "degraded"
    except Exception as e:
        checks["pipeline"] = {"status": "error", "detail": str(e)}
        overall = "degraded"

    return {"status": overall, "components": checks}

@app.get("/api/stats")
def get_stats(auth: str = Depends(verify_key)):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM audit_findings WHERE time > NOW() - INTERVAL '24 hours'")
        findings = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM anomaly_events WHERE time > NOW() - INTERVAL '24 hours'")
        anomalies = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM llm_analyses WHERE time > NOW() - INTERVAL '24 hours'")
        llm = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM flow_events WHERE time > NOW() - INTERVAL '1 hour'")
        flows = cur.fetchone()["cnt"]
        conn.close()
        return {"findings_24h": findings, "anomalies_24h": anomalies, "llm_analyses_24h": llm, "flow_drops_1h": flows}
    except Exception as e:
        log.error(f"stats error: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/compliance")
def get_compliance(auth: str = Depends(verify_key)):
    try:
        conn = get_conn(); cur = conn.cursor()
        # DISTINCT ON: latest finding per (namespace, policy_name, rule_id)
        # Exclude known exemptions from scoring
        cur.execute("""
            SELECT severity, COUNT(*) as cnt FROM (
                SELECT DISTINCT ON (f.namespace, f.policy_name, f.rule_id)
                    f.severity, f.resolved
                FROM audit_findings f
                ORDER BY f.namespace, f.policy_name, f.rule_id, f.time DESC
            ) latest WHERE resolved = false GROUP BY severity
        """)
        counts = {r["severity"]: r["cnt"] for r in cur.fetchall()}
        cur.execute("""
            SELECT COUNT(DISTINCT namespace) as n FROM (
                SELECT DISTINCT ON (f.namespace, f.policy_name, f.rule_id)
                    f.namespace, f.resolved
                FROM audit_findings f
                ORDER BY f.namespace, f.policy_name, f.rule_id, f.time DESC
            ) latest WHERE resolved = false
        """)
        ns_viol = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(DISTINCT namespace) as n FROM audit_findings")
        total_ns = max(cur.fetchone()["n"], 1)
        conn.close()
        c = counts.get("CRITICAL", 0); h = counts.get("HIGH", 0)
        m = counts.get("MEDIUM", 0);   l = counts.get("LOW", 0)
        penalty = min(c*20 + h*10 + m*5 + l*2, 100)
        score = max(100 - penalty, 0)
        grade = "A" if score>=90 else "B" if score>=80 else "C" if score>=70 else "D" if score>=60 else "F"
        return {"score": score, "grade": grade, "critical": c, "high": h, "medium": m, "low": l,
                "total_findings": c+h+m+l, "namespaces_with_violations": ns_viol, "total_namespaces": total_ns}
    except Exception as e:
        log.error(f"compliance error: {e}"); raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/findings", dependencies=[Depends(verify_key)])
def get_findings(limit: int=Query(50,ge=1,le=200), namespace: Optional[str]=None,
                 severity: Optional[str]=None, hours: int=Query(720,ge=1,le=8760),
                 include_resolved: bool=Query(False)):
    try:
        conn = get_conn(); cur = conn.cursor()
        conds = []; params = []
        if not include_resolved:
            conds.append("resolved = false")
            # Active findings: no time filter — matches /api/compliance
        else:
            # Show all active + resolved within time window
            conds.append(f"(resolved = false OR time > NOW() - INTERVAL '{hours} hours')")
        if namespace and namespace != "all": conds.append("namespace = %s"); params.append(namespace)
        if severity and severity != "all": conds.append("severity = %s"); params.append(severity.upper())
        cur.execute(f"""
            SELECT f.time, f.rule_id, f.namespace, f.severity, f.message, f.policy_name,
                f.resolved, f.resolved_at
            FROM audit_findings f
            WHERE {' AND '.join(conds)}
            ORDER BY f.resolved ASC, f.time DESC LIMIT %s""", params + [limit])
        rows = cur.fetchall(); conn.close()
        findings_list = []
        for r in rows:
            findings_list.append({
                "time": r["time"].isoformat() if r["time"] else None,
                "rule_id": r["rule_id"], "namespace": r["namespace"],
                "severity": r["severity"], "message": r["message"],
                "policy_name": r["policy_name"],
                "resolved": r["resolved"] or False,
                "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
                "exempted": False,
                "exemption_reason": None
            })
        return {"findings": findings_list, "count": len(findings_list)}
    except Exception as e:
        log.error(f"findings error: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/anomalies", dependencies=[Depends(verify_key)])
def get_anomalies(limit: int=Query(30,ge=1,le=100), hours: int=Query(720,ge=1,le=8760)):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(f"""
            SELECT anomaly_type, rule_id, src_namespace, dst_namespace,
                dst_port, severity,
                (array_agg(description ORDER BY time DESC))[1] as description,
                COUNT(*) as occurrences,
                MIN(time) as first_seen,
                MAX(time) as last_seen,
                MAX(z_score) as max_z_score
            FROM anomaly_events
            WHERE time > NOW() - INTERVAL '{hours} hours'
            GROUP BY anomaly_type, rule_id, src_namespace, dst_namespace,
                     dst_port, severity
            ORDER BY last_seen DESC, occurrences DESC
            LIMIT {limit}""")
        rows = cur.fetchall(); conn.close()
        return {"anomalies": [{
            "anomaly_type": r["anomaly_type"], "rule_id": r["rule_id"],
            "src_namespace": r["src_namespace"], "dst_namespace": r["dst_namespace"],
            "dst_port": r["dst_port"], "severity": r["severity"],
            "description": r["description"],
            "occurrences": r["occurrences"],
            "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            "z_score": float(r["max_z_score"]) if r["max_z_score"] else None
        } for r in rows], "count": len(rows)}
    except Exception as e:
        log.error(f"anomalies error: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/llm-analyses", dependencies=[Depends(verify_key)])
def get_llm_analyses(limit: int=Query(10,ge=1,le=50), source_type: Optional[str]=None):
    try:
        conn = get_conn(); cur = conn.cursor()
        conds = []; params = []
        if source_type and source_type != "all": conds.append("source_type = %s"); params.append(source_type)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        cur.execute(f"""
            SELECT la.id, la.time, la.source_type, la.source_id, la.model, la.response, la.latency_ms,
                ae.anomaly_type, ae.rule_id as ae_rule_id, ae.src_namespace, ae.dst_namespace,
                ae.dst_port, ae.severity as ae_severity, ae.description as ae_description, ae.z_score,
                af.rule_id as af_rule_id, af.namespace as af_namespace, af.severity as af_severity,
                af.policy_name, af.message as af_message, af.resolved, af.time as finding_time
            FROM llm_analyses la
            LEFT JOIN anomaly_events ae ON la.source_id = ae.id AND la.source_type = 'anomaly'
            LEFT JOIN audit_findings af ON la.source_id = af.id AND la.source_type = 'audit'
            {where} ORDER BY la.time DESC LIMIT %s
        """, params + [limit])
        rows = cur.fetchall(); conn.close()
        result = []
        for r in rows:
            resp = r["response"]
            if isinstance(resp, str):
                try: resp = json.loads(resp)
                except: resp = {"raw": resp}
            item = {"id": r["id"],
                "created_at": r["time"].isoformat() if r["time"] else None,
                "source_type": r["source_type"], "source_id": r["source_id"],
                "model": r["model"], "response": resp, "latency_ms": r["latency_ms"]}
            if r["source_type"] == "anomaly" and r["ae_rule_id"]:
                item["event"] = {
                    "anomaly_type": r["anomaly_type"], "rule_id": r["ae_rule_id"],
                    "src_namespace": r["src_namespace"], "dst_namespace": r["dst_namespace"],
                    "dst_port": r["dst_port"], "severity": r["ae_severity"],
                    "description": r["ae_description"], "z_score": float(r["z_score"]) if r["z_score"] else None
                }
            if r["source_type"] == "audit" and r["af_rule_id"]:
                item["finding"] = {
                    "rule_id": r["af_rule_id"], "namespace": r["af_namespace"],
                    "severity": r["af_severity"], "policy_name": r["policy_name"],
                    "message": r["af_message"], "resolved": r["resolved"] or False,
                    "detected_at": r["finding_time"].isoformat() if r["finding_time"] else None
                }
            result.append(item)
        return {"analyses": result, "count": len(result)}
    except Exception as e:
        log.error(f"llm-analyses error: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/flow-baseline")
def get_flow_baseline(auth: str = Depends(verify_key)):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT flow_key, mean_rate, std_dev, sample_count, last_updated FROM flow_baseline ORDER BY mean_rate DESC LIMIT 30")
        rows = cur.fetchall(); conn.close()
        return {"baseline": [{"flow_key": r["flow_key"],
            "mean_rate": float(r["mean_rate"]) if r["mean_rate"] else 0,
            "std_dev": float(r["std_dev"]) if r["std_dev"] else 0,
            "sample_count": r["sample_count"],
            "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None} for r in rows]}
    except Exception as e:
        log.error(f"flow-baseline error: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/policy-graph")
def get_policy_graph(auth: str = Depends(verify_key)):
    try:
        conn = get_conn(); cur = conn.cursor()
        # Nodes: namespaces from audit_findings + parsed from flow_key
        cur.execute("""
            SELECT DISTINCT namespace as ns FROM audit_findings
            WHERE time > NOW() - INTERVAL '24 hours'
            UNION
            SELECT DISTINCT split_part(flow_key, '|', 1) as ns FROM flow_baseline
            WHERE sample_count > 0
        """)
        namespaces = list({r["ns"] for r in cur.fetchall() if r["ns"]})

        # Edges: from flow_baseline (persistent) — includes intra-namespace flows
        cur.execute("""
            SELECT
                split_part(flow_key, '|', 1) as src_namespace,
                split_part(flow_key, '|', 2) as dst_namespace,
                split_part(flow_key, '|', 3)::integer as dst_port,
                ROUND(mean_rate::numeric, 2) as mean_rate,
                sample_count
            FROM flow_baseline
            WHERE sample_count >= 10
            ORDER BY mean_rate DESC
            LIMIT 50
        """)
        edges = cur.fetchall()

        # Violations per namespace
        cur.execute("""
            SELECT namespace,
                COUNT(*) FILTER (WHERE severity='CRITICAL') as critical,
                COUNT(*) FILTER (WHERE severity='HIGH') as high,
                COUNT(*) as total
            FROM audit_findings
            WHERE time > NOW() - INTERVAL '24 hours'
            GROUP BY namespace
        """)
        violations = {r["namespace"]: dict(r) for r in cur.fetchall()}
        conn.close()

        nodes = [{"id": ns, "label": ns,
                  "violations": violations.get(ns, {}).get("total", 0),
                  "critical":   violations.get(ns, {}).get("critical", 0),
                  "high":       violations.get(ns, {}).get("high", 0)}
                 for ns in namespaces]

        edge_list = [{"source": r["src_namespace"],
                      "target": r["dst_namespace"],
                      "port":   r["dst_port"],
                      "count":  float(r["mean_rate"]),
                      "sample_count": r["sample_count"]}
                     for r in edges
                     if r["src_namespace"] and r["dst_namespace"]]

        return {"nodes": nodes, "edges": edge_list}
    except Exception as e:
        log.error(f"policy-graph error: {e}"); raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pods")
def get_pods(namespace: Optional[str] = None):
    _cache_ready.wait(timeout=10)
    with _cache_lock:
        if namespace and namespace != "all":
            pods = list(_pod_cache.get(namespace, []))
            return {"namespace": namespace, "pods": pods, "count": len(pods)}
        result = {}
        for ns in WATCHED_NS:
            result[ns] = list(_pod_cache.get(ns, []))
        return {"namespaces": result, "total": sum(len(v) for v in result.values())}

@app.get("/api/network-policies")
def get_network_policies(namespace: Optional[str] = None):
    _cache_ready.wait(timeout=10)
    with _cache_lock:
        if namespace and namespace != "all":
            nps = list(_netpol_cache.get(namespace, []))
            return {"namespace": namespace, "policies": nps, "count": len(nps)}
        result = {}
        for ns in WATCHED_NS:
            result[ns] = list(_netpol_cache.get(ns, []))
        return {"namespaces": result, "total": sum(len(v) for v in result.values())}

@app.post("/api/rescan")
def trigger_rescan():
    import httpx as _httpx, time as _time
    try:
        # Trigger audit-engine rescan
        r = _httpx.post(
            "http://audit-engine.netpol-system.svc.cluster.local:8081/trigger-rescan",
            timeout=5.0
        )
        # Wait for rescan to complete (audit engine needs ~10-30s)
        _time.sleep(20)
        # Return updated compliance
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""
            SELECT severity, COUNT(*) as cnt FROM (
                SELECT DISTINCT ON (f.namespace, f.policy_name, f.rule_id) f.severity
                FROM audit_findings f
                WHERE f.resolved = false
                ORDER BY f.namespace, f.policy_name, f.rule_id, f.time DESC
            ) latest GROUP BY severity
        """)
        counts = {r["severity"]: r["cnt"] for r in cur.fetchall()}
        conn.close()
        c = counts.get("CRITICAL",0); h = counts.get("HIGH",0)
        m = counts.get("MEDIUM",0);   l = counts.get("LOW",0)
        penalty = min(c*20 + h*10 + m*5 + l*2, 100)
        score = max(100 - penalty, 0)
        grade = "A" if score>=90 else "B" if score>=80 else "C" if score>=70 else "D" if score>=60 else "F"
        return {"status": "rescan complete", "score": score, "grade": grade,
                "critical": c, "high": h, "medium": m, "low": l}
    except Exception as e:
        log.error(f"Rescan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/insights/anomalies", dependencies=[Depends(verify_key)])
def get_insights_anomalies(hours: int=Query(720,ge=1,le=8760), limit: int=Query(50,ge=1,le=200)):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(f"""
            SELECT ae_g.anomaly_type, ae_g.rule_id, ae_g.src_namespace, ae_g.dst_namespace,
                ae_g.dst_port, ae_g.severity, ae_g.description,
                ae_g.occurrences, ae_g.first_seen, ae_g.last_seen, ae_g.max_z_score,
                la.id as analysis_id, la.response as analysis_response,
                la.latency_ms as analysis_latency_ms, la.time as analysis_time
            FROM (
                SELECT anomaly_type, rule_id, src_namespace, dst_namespace, dst_port,
                    severity, description, COUNT(*) as occurrences,
                    MIN(time) as first_seen, MAX(time) as last_seen, MAX(z_score) as max_z_score
                FROM anomaly_events
                WHERE time > NOW() - INTERVAL '{hours} hours'
                GROUP BY anomaly_type, rule_id, src_namespace, dst_namespace, dst_port, severity, description
                ORDER BY occurrences DESC, MAX(time) DESC LIMIT {limit}
            ) ae_g
            LEFT JOIN LATERAL (
                SELECT la.id, la.response, la.latency_ms, la.time
                FROM llm_analyses la
                JOIN anomaly_events ae ON la.source_id = ae.id AND la.source_type = 'anomaly'
                WHERE ae.anomaly_type = ae_g.anomaly_type
                  AND ae.src_namespace = ae_g.src_namespace
                  AND ae.dst_namespace = ae_g.dst_namespace
                  AND ae.dst_port = ae_g.dst_port
                ORDER BY la.time DESC LIMIT 1
            ) la ON true
            ORDER BY ae_g.occurrences DESC, ae_g.last_seen DESC
        """)
        rows = cur.fetchall(); conn.close()
        items = []
        for r in rows:
            resp = r["analysis_response"]
            if resp and isinstance(resp, str):
                try: resp = json.loads(resp)
                except: resp = {"raw": resp}
            items.append({
                "anomaly_type": r["anomaly_type"], "rule_id": r["rule_id"],
                "src_namespace": r["src_namespace"], "dst_namespace": r["dst_namespace"],
                "dst_port": r["dst_port"], "severity": r["severity"],
                "description": r["description"], "occurrences": r["occurrences"],
                "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "z_score": float(r["max_z_score"]) if r["max_z_score"] else None,
                "analysis": {"id": r["analysis_id"], "response": resp,
                    "latency_ms": r["analysis_latency_ms"],
                    "created_at": r["analysis_time"].isoformat() if r["analysis_time"] else None
                } if r["analysis_id"] else None
            })
        return {"items": items, "count": len(items)}
    except Exception as e:
        log.error(f"insights/anomalies error: {e}"); raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat", dependencies=[Depends(verify_key)])
def chat(body: dict):
    import httpx, urllib.parse
    from datetime import datetime, timezone
    question = body.get("message", "").strip()
    history  = body.get("history", [])
    if not question:
        raise HTTPException(status_code=400, detail="message is required")

    # ── Off-topic detection ───────────────────────────────────────────────
    security_keywords = [
        "networkpolicy", "netpol", "compliance", "violation", "finding", "anomaly",
        "pod", "namespace", "kubernetes", "k8s", "openshift", "ocp", "ovn", "cni",
        "security", "rbac", "scc", "kyverno", "deny", "egress", "ingress", "firewall",
        "cluster", "node", "container", "image", "vulnerability", "cve", "scan",
        "pipeline", "cicd", "ci/cd", "devsecops", "devops", "gitops", "argocd",
        "helm", "operator", "sast", "dast", "trivy", "grype", "cosign", "sigstore",
        "supply chain", "sbom", "shift-left", "zero trust", "least privilege",
        "audit", "score", "grade", "critical", "high", "medium", "low",
        "fix", "remediate", "harden", "policy", "yaml", "manifests",
        "np-001", "np-002", "np-003", "np-004", "np-005", "np-006", "np-007", "np-008",
        "anom", "zscore", "z-score", "traffic", "flow", "drop", "acl",
        "online-boutique", "netpol-system", "llm-system", "grafana", "loki", "prometheus",
        "alertmanager", "fluent", "timescaledb", "postgresql", "ollama",
        "service mesh", "mtls", "certificate", "tls", "secret", "configmap",
        "admission", "webhook", "opa", "gatekeeper", "falco", "tetragon",
        "runtime", "forensic", "incident", "breach", "attack", "exploit",
        "lateral movement", "privilege escalation", "data exfiltration",
    ]
    q_lower = question.lower()
    is_on_topic = any(kw in q_lower for kw in security_keywords)

    # ── Fetch live context from DB ────────────────────────────────────────
    try:
        conn = get_conn(); cur = conn.cursor()

        cur.execute("""
            SELECT severity, COUNT(*) as cnt FROM (
                SELECT DISTINCT ON (f.namespace, f.policy_name, f.rule_id)
                    f.severity, f.resolved
                FROM audit_findings f
                ORDER BY f.namespace, f.policy_name, f.rule_id, f.time DESC
            ) latest WHERE resolved = false GROUP BY severity
        """)
        counts = {r["severity"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("""
            SELECT rule_id, namespace, severity, message, policy_name, time FROM (
                SELECT DISTINCT ON (f.namespace, f.policy_name, f.rule_id)
                    f.rule_id, f.namespace, f.severity, f.message,
                    f.policy_name, f.resolved, f.time
                FROM audit_findings f
                ORDER BY f.namespace, f.policy_name, f.rule_id, f.time DESC
            ) latest
            WHERE resolved = false
            ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                                   WHEN 'MEDIUM' THEN 3 ELSE 4 END
        """)
        all_findings = cur.fetchall()

        cur.execute("""
            SELECT anomaly_type, rule_id, src_namespace, dst_namespace,
                   dst_port, severity, description,
                   COUNT(*) as occurrences,
                   MIN(time) as first_seen,
                   MAX(time) as last_seen,
                   MAX(z_score) as max_z_score
            FROM anomaly_events
            WHERE time > NOW() - INTERVAL '72 hours'
            GROUP BY anomaly_type, rule_id, src_namespace, dst_namespace,
                     dst_port, severity, description
            ORDER BY MAX(time) DESC
            LIMIT 10
        """)
        top_anomalies = cur.fetchall()

        cur.execute("""
            SELECT la.source_type, la.response, la.time as analysis_time,
                   la.model, la.latency_ms,
                   af.rule_id, af.namespace, af.policy_name, af.severity,
                   ae.anomaly_type, ae.src_namespace, ae.dst_namespace
            FROM llm_analyses la
            LEFT JOIN audit_findings af ON la.source_id = af.id AND la.source_type = 'audit'
            LEFT JOIN anomaly_events ae ON la.source_id = ae.id AND la.source_type = 'anomaly'
            ORDER BY la.time DESC LIMIT 5
        """)
        recent_analyses = cur.fetchall()

        cur.execute("SELECT COUNT(*) as cnt FROM anomaly_events WHERE time > NOW() - INTERVAL '24 hours'")
        anomaly_24h = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM flow_events WHERE time > NOW() - INTERVAL '1 hour'")
        flow_drops_1h = cur.fetchone()["cnt"]

        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    # ── Build context ─────────────────────────────────────────────────────
    c = counts.get("CRITICAL",0); h = counts.get("HIGH",0)
    m = counts.get("MEDIUM",0);   l = counts.get("LOW",0)
    penalty = min(c*20 + h*10 + m*5 + l*2, 100)
    score = max(100 - penalty, 0)
    grade = "A" if score>=90 else "B" if score>=80 else "C" if score>=70 else "D" if score>=60 else "F"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    findings_text = ""
    for f in all_findings[:8]:
        ts = f['time'].strftime("%Y-%m-%d %H:%M") if f['time'] else "unknown"
        findings_text += f"  - [{f['severity']}] {f['rule_id']} | ns={f['namespace']} | policy={f['policy_name']} | detected={ts} | {f['message']}\n"

    anomalies_text = ""
    for a in top_anomalies[:6]:
        z = f" z={float(a['max_z_score']):.2f}" if a['max_z_score'] else ""
        first = a['first_seen'].strftime("%Y-%m-%d %H:%M") if a['first_seen'] else "?"
        last = a['last_seen'].strftime("%Y-%m-%d %H:%M") if a['last_seen'] else "?"
        occ = a['occurrences']
        anomalies_text += f"  - [{a['severity']}] {a['rule_id']} | {a['src_namespace']}->{a['dst_namespace']}:{a['dst_port']}{z} | x{occ} ({first} -> {last}) | {a['description']}\n"
    if not anomalies_text:
        anomalies_text = "  - No anomalies detected in last 72h\n"

    analyses_text = ""
    for la in recent_analyses[:3]:
        ts = la['analysis_time'].strftime("%Y-%m-%d %H:%M") if la['analysis_time'] else "?"
        resp = la['response']
        if isinstance(resp, str):
            try: resp = json.loads(resp)
            except: resp = {}
        summary = ""
        if la['source_type'] == 'audit' and la['rule_id']:
            summary = f"Audit {la['rule_id']} in {la['namespace']}/{la['policy_name']} ({la['severity']})"
            explanation = resp.get('explanation', '')[:150] if isinstance(resp, dict) else ''
            if explanation: summary += f" -- {explanation}"
        elif la['source_type'] == 'anomaly' and la['anomaly_type']:
            summary = f"Anomaly {la['anomaly_type']} {la['src_namespace']}->{la['dst_namespace']}"
            explanation = resp.get('explanation', '')[:150] if isinstance(resp, dict) else ''
            if explanation: summary += f" -- {explanation}"
        if summary:
            analyses_text += f"  - [{ts}] {summary}\n"
    if not analyses_text:
        analyses_text = "  - No recent LLM analyses\n"

    netpol_cache_text = ""
    total_policies = 0
    with _cache_lock:
        for ns in WATCHED_NS:
            nps = _netpol_cache.get(ns, [])
            total_policies += len(nps)
            if nps:
                netpol_cache_text += f"  {ns}: {len(nps)} policies -- " + ", ".join(n['name'] for n in nps[:8]) + ("..." if len(nps)>8 else "") + "\n"

    pod_cache_text = ""
    total_pods = 0
    with _cache_lock:
        for ns in WATCHED_NS:
            ps = _pod_cache.get(ns, [])
            total_pods += len(ps)
            if ps:
                not_ready = [p['name'] for p in ps if not p['ready']]
                high_restart = [f"{p['name']}({p['restarts']})" for p in ps if p['restarts'] > 5]
                status = f" ({len(not_ready)} not ready: {', '.join(not_ready[:3])})" if not_ready else " (all ready)"
                if high_restart:
                    status += f" ! high restarts: {', '.join(high_restart[:3])}"
                pod_cache_text += f"  {ns}: {len(ps)} pods{status}\n"

    # ── Web search if question needs external info ────────────────────────
    web_context = ""
    search_triggers = ["how to", "what is", "explain", "fix", "error", "cve",
                       "kubectl", "oc ", "networkpolicy", "ovn", "openshift",
                       "best practice", "documentation", "example", "yaml",
                       "devsecops", "pipeline", "cicd", "shift-left", "sbom",
                       "trivy", "grype", "falco", "sigstore", "cosign"]
    needs_search = any(t in q_lower for t in search_triggers)

    if needs_search:
        try:
            search_q = urllib.parse.quote(f"kubernetes openshift {question[:120]}")
            resp = httpx.get(
                f"https://api.duckduckgo.com/?q={search_q}&format=json&no_html=1&skip_disambig=1",
                timeout=5.0,
                headers={"User-Agent": "NetPolIntelligence/1.0"}
            )
            if resp.status_code == 200:
                ddg = resp.json()
                abstract = ddg.get("AbstractText", "")
                related = [r.get("Text","") for r in ddg.get("RelatedTopics",[])[:3] if r.get("Text")]
                if abstract:
                    web_context = f"\nWEB REFERENCE:\n  {abstract[:600]}\n"
                elif related:
                    web_context = f"\nWEB REFERENCE:\n  " + "\n  ".join(related[:3]) + "\n"
        except Exception:
            pass

    # ── Build conversation history ────────────────────────────────────────
    history_text = ""
    for msg in history[-6:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        history_text += f"{role}: {msg.get('content','')[:300]}\n"

    # ── Off-topic instruction ─────────────────────────────────────────────
    if is_on_topic:
        response_instruction = """RESPONSE STYLE:
- Give DETAILED, THOROUGH answers with specific data from the live context above
- Name exact namespaces, policy names, rule IDs, timestamps, and Z-scores
- Provide complete YAML fixes when relevant
- Explain root cause -> impact -> remediation step by step
- Reference DevSecOps best practices where applicable"""
    else:
        response_instruction = """RESPONSE STYLE:
- This question is NOT about cluster security, Kubernetes, or DevSecOps
- Give a SHORT answer (1-2 sentences maximum)
- Then redirect: "For cluster security questions, compliance issues, or DevSecOps guidance, I can provide detailed analysis with live data from your cluster."
- Do NOT provide lengthy general-knowledge answers -- your expertise is cluster security"""

    system_prompt = f"""You are an expert Kubernetes/OpenShift network security and DevSecOps engineer assistant embedded in the NetPol Intelligence System at lab.ocp.lan (OVN-Kubernetes CNI).
Current time: {now_utc}

YOUR CORE EXPERTISE:
1. Kubernetes NetworkPolicy security -- violations, compliance, Zero Trust enforcement
2. OVN-Kubernetes CNI -- ACL logs, flow analysis, drop detection
3. OpenShift 4 platform security -- SCCs, RBAC, admission control, OAuth, routes
4. DevSecOps practices -- CI/CD pipeline security, shift-left testing, image scanning, SBOM, supply chain security
5. Container security -- image hardening, runtime protection, vulnerability management
6. GitOps and Infrastructure as Code -- ArgoCD, Helm, Kustomize security patterns
7. Compliance frameworks -- CIS Benchmarks, NIST, SOC2 as applied to Kubernetes

YOUR JOB:
1. Answer questions about THIS SPECIFIC cluster using the LIVE DATA below
2. Diagnose root causes precisely -- name the exact namespace, policy, rule ID, and timestamp
3. Provide actionable fixes with exact YAML when asked
4. Explain WHY something is a problem (attack scenario, blast radius, compliance impact)
5. When discussing DevSecOps, tie recommendations to this cluster actual state
6. Reference conversation history when relevant
7. NEVER give generic answers -- always tie your answer to the actual data
8. Include timestamps when referring to findings or anomalies

{response_instruction}

DEVSECOPS KNOWLEDGE:
- CI/CD Pipeline Security: Enforce image scanning (Trivy, Grype) before deployment. Reject images with CRITICAL CVEs. Sign images with Cosign/Sigstore.
- Shift-Left: Validate NetworkPolicies in CI with tools like kubeval, conftest, or kube-linter. Catch NP-002/NP-003 violations before they reach the cluster.
- Supply Chain: Generate SBOM (Syft) for every build. Store in OCI registry alongside signed images. Verify provenance with SLSA framework.
- GitOps Security: All manifests in Git (ArgoCD/FluxCD). No manual kubectl apply. Drift detection alerts. Branch protection + PR reviews for policy changes.
- Runtime Security: Falco or Tetragon for runtime anomaly detection. Alert on unexpected process execution, file access, or network connections in pods.
- Image Hardening: Use distroless or UBI-minimal base images. Run as non-root (runAsNonRoot: true). Read-only root filesystem. Drop ALL capabilities, add only needed ones.
- Admission Control: Kyverno (already deployed) enforces default-deny. Add policies for: no latest tag, required resource limits, mandatory labels, no privileged containers.
- Secret Management: Use Sealed Secrets or External Secrets Operator. Never store secrets in Git. Rotate credentials regularly.

--- CLUSTER OVERVIEW ---
Cluster: lab.ocp.lan | OCP 4.21.8 | OVN-Kubernetes | {now_utc}
Nodes: 1 control plane + 4 workers (ocp-w-2 dedicated LLM)
Namespaces monitored: online-boutique (11 gRPC microservices), netpol-system (monitoring stack), llm-system (Ollama)
Total pods: {total_pods} | Total NetworkPolicies: {total_policies}
Anomalies (24h): {anomaly_24h} | Flow drops (1h): {flow_drops_1h}

--- COMPLIANCE ---
Score: {score}% | Grade: {grade} | CRITICAL:{c} HIGH:{h} MEDIUM:{m} LOW:{l}
Total active violations: {c+h+m+l}

--- ALL ACTIVE VIOLATIONS (with timestamps) ---
{findings_text if findings_text else "None currently active -- cluster is clean!"}

--- RECENT ANOMALIES (72h, grouped) ---
{anomalies_text}

--- RECENT LLM ANALYSES ---
{analyses_text}

--- LIVE NETWORKPOLICIES ---
{netpol_cache_text if netpol_cache_text else "Cache loading..."}

--- LIVE POD STATUS ---
{pod_cache_text if pod_cache_text else "Cache loading..."}

--- RULE DEFINITIONS ---
NP-001: Missing default-deny (namespace has no catch-all deny) | HIGH
NP-002: Allow-all ingress -- empty ingress from: selector | CRITICAL
NP-003: Allow-all egress -- empty egress to: selector | HIGH
NP-004: Unrestricted namespaceSelector (no matchLabels) | MEDIUM
NP-005: No egress NetworkPolicy in namespace | MEDIUM
NP-006: Privileged port exposure (<1024) from broad selector | HIGH
NP-007: Overlapping podSelector conflict between policies | MEDIUM
NP-008: Egress policies without explicit DNS port 53/5353 | LOW
ANOM-001: Access to control plane port 6443 from app namespace | CRITICAL
ANOM-002: Unexpected OVN ACL drop -- possible NetworkPolicy gap | HIGH
ZSCORE-001: Statistical traffic spike (Welford Z-score > 3) | varies
{web_context}"""

    # ── Build full prompt with history ────────────────────────────────────
    if history_text:
        full_prompt = system_prompt + f"\n--- CONVERSATION HISTORY ---\n{history_text}\n--- CURRENT QUESTION ---\nUser: {question}\nAssistant:"
    else:
        full_prompt = system_prompt + f"\n--- QUESTION ---\nUser: {question}\nAssistant:"

    # ── Choose num_predict based on topic ─────────────────────────────────
    max_tokens = 1500 if is_on_topic else 200

    # ── Call Ollama with streaming ────────────────────────────────────────
    from fastapi.responses import StreamingResponse
    import json as _json

    def stream_ollama():
        ctx = _json.dumps({"score": score, "grade": grade, "critical": c, "high": h})
        yield f"data: {ctx}\n\n"
        try:
            with httpx.stream(
                "POST",
                "http://ollama.llm-system.svc.cluster.local:11434/api/generate",
                json={"model": "llama3.2:3b", "prompt": full_prompt, "stream": True,
                      "options": {"num_predict": max_tokens, "temperature": 0.3}},
                timeout=180.0
            ) as r:
                for line in r.iter_lines():
                    if line:
                        try:
                            chunk = _json.loads(line)
                            token = chunk.get("response", "")
                            if token:
                                yield f"data: {_json.dumps({'token': token})}\n\n"
                            if chunk.get("done"):
                                yield f"data: {_json.dumps({'done': True})}\n\n"
                                break
                        except Exception:
                            continue
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(stream_ollama(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
