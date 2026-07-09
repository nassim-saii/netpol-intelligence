# NetworkPolicy Intelligence System — Deployment Runbook

*OpenShift 4.21.8 · Cluster: `lab.ocp.lan` · Namespace: `netpol-system`*
*Prepared for NextStep IT Cloud & Infrastructure Team*

---

## 1. Overview

This runbook provides the complete end-to-end deployment procedure for the NetworkPolicy Intelligence System on OpenShift 4.21.8. The steps below cover all four implementation phases — from namespace creation to dashboard go-live.

All commands run from the bastion host **`ocp-lb`** as `system:admin`. Source code resides at `~/netpol-intelligence/`. Cluster domain: `lab.ocp.lan`.

Two deployment paths are documented:

- **Path A — Prebuilt images (recommended):** pull the six service images directly from [Docker Hub](https://hub.docker.com/u/nassimsai) and apply the manifests. No in-cluster build step required.
- **Path B — Build from source in-cluster:** use OpenShift `BuildConfig` (`oc new-build --binary --strategy=docker`) to build each service directly from the source tree. This was the original air-gap-capable pipeline used during development.

Expected total deployment time: **~45 minutes** on first run (Ollama model pull ~5 min, frontend build ~3 min if using Path B). Subsequent rebuilds per-service: 2–4 minutes.

---

## 2. Prerequisites

Before starting, confirm:

- [ ] OpenShift 4.21.8 cluster healthy — `oc get nodes` shows all nodes `Ready`
- [ ] `oc login` performed as `system:admin` on `ocp-lb`
- [ ] Helm 3 installed (for Loki, Grafana, TimescaleDB, Kyverno)
- [ ] Source tree cloned to `~/netpol-intelligence/` on `ocp-lb`
- [ ] Worker node `ocp-w-2` labelled and tainted `dedicated=llm:NoSchedule` (12 vCPU, dedicated to Ollama)
- [ ] Docker Hub reachable from `ocp-lb` if using Path A (`docker pull` needs internet egress)

---

## 3. Deployment Commands

### PHASE 1 — Foundation & Observability Stack

| # | Step | Command(s) | Notes |
|---|---|---|---|
| **01** | Create namespaces | `oc new-project netpol-system && oc new-project llm-system` | Primary namespace + LLM namespace |
| **02** | RBAC + SCCs | `oc create clusterrole netpol-reader --verb=get,list,watch --resource=networkpolicies,pods,namespaces,endpoints,services`<br>`oc create clusterrolebinding netpol-engine-binding --clusterrole=netpol-reader --serviceaccount=netpol-system:netpol-engine`<br>`oc adm policy add-scc-to-user privileged -z netpol-engine -n netpol-system` | `netpol-engine` SA needs cluster-reader + privileged SCC |
| **03** | Enable OVN ACL logging | `for ns in netpol-system online-boutique; do oc annotate ns $ns k8s.ovn.org/acl-logging='{"deny":"alert","allow":"notice"}' --overwrite; done` | Required for `flow_events` pipeline |
| **04** | Deploy Loki | `helm install loki grafana/loki -n netpol-system -f ~/netpol-intelligence/helm/loki-values.yaml` | Log backend for OVN ACL events |
| **05** | Deploy Grafana + Route | `helm install grafana grafana/grafana -n netpol-system -f ~/netpol-intelligence/helm/grafana-values.yaml`<br>`oc expose service grafana -n netpol-system --hostname=grafana.apps.lab.ocp.lan` | Grafana 12.3.1; datasource UIDs provisioned via ConfigMap |
| **06** | Deploy Fluent Bit DaemonSet | `oc apply -f ~/netpol-intelligence/fluent-bit/fluent-bit-daemonset.yaml -n netpol-system` | Tails `/var/log/ovn/acl-audit-log.log` on all nodes → Loki |
| **07** | Provision TimescaleDB PVs | `oc debug node/ocp-w-0.lab.ocp.lan -- chroot /host bash -c "mkdir -p /mnt/timescaledb-data /mnt/timescaledb-wal && chmod 777 /mnt/timescaledb-{data,wal}"`<br>`oc apply -f ~/netpol-intelligence/manifests/timescaledb-pvs.yaml` | Local-storage PVs on `ocp-w-0` (no dynamic provisioner) |
| **08** | Deploy TimescaleDB | `helm install timescaledb timescale/timescaledb-single -n netpol-system -f /tmp/tsdb-values.yaml`<br>`oc rollout status statefulset/timescaledb -n netpol-system` | StatefulSet; target `timescaledb-0` for `oc exec` (**not** `deploy/timescaledb`) |
| **09** | Create DB schema + app user | `oc exec -n netpol-system timescaledb-0 -- psql -U postgres -f /tmp/schema.sql`<br>`oc create secret generic netpol-db-dsn -n netpol-system --from-literal=DATABASE_URL='postgresql://netpol_app:NetpolApp%402024!@timescaledb.netpol-system.svc.cluster.local:5432/netpol_intelligence?sslmode=require'` | Creates hypertables: `audit_findings`, `flow_events`, `flow_baseline`, `anomaly_events`, `llm_analyses` |
| **10** | Install Kyverno + NetworkPolicies | `helm install kyverno kyverno/kyverno -n kyverno --create-namespace --set admissionController.replicas=1 --set backgroundController.enabled=true --set reportsController.enabled=true`<br>`oc apply -f ~/netpol-intelligence/kyverno-policies/` | ⚠️ Generate `ClusterPolicies` **must** use opt-in label selectors and exclude `openshift-*`/`kube-system`/`kyverno` namespaces — see §5 warning |

### PHASE 2 — Audit Engine & Flow Processor

| # | Step | Command(s) | Notes |
|---|---|---|---|
| **11** | Build + deploy Audit Engine | **Path A:** `oc apply -f ~/netpol-intelligence/deploy/audit-engine-deployment.yaml` (image: `docker.io/nassimsai/audit-engine:v1.0-pfe2026`)<br>**Path B:** `cd ~/netpol-intelligence/services/audit-engine && oc new-build --name=audit-engine --binary --strategy=docker -n netpol-system && oc start-build audit-engine --from-dir=. --follow -n netpol-system`<br>`oc rollout status deployment/audit-engine -n netpol-system` | Watches K8s API; 5-min cooldown per namespace; evaluates 8 security rules (NP-001–NP-008) |
| **12** | Build + deploy Flow Processor | Same pattern as #11, service `flow-processor` | 30-second Loki poll; drops-only mode; builds `flow_baseline` via Welford algorithm |
| **13** | Import Grafana dashboards | `for f in ~/netpol-intelligence/grafana/*.json; do curl -s -X POST -u admin:Admin@OCP123 -H "Content-Type: application/json" -d @$f http://grafana.apps.lab.ocp.lan/api/dashboards/db; done` | 3 dashboards: NetworkPolicy Violations & Flow Analysis, OVN ACL Flow Monitor, OVN Metrics + Cluster Health |

### PHASE 3 — LLM Service & Anomaly Detection

| # | Step | Command(s) | Notes |
|---|---|---|---|
| **14** | Deploy Ollama | `oc apply -f ~/netpol-intelligence/llm/ollama-deployment.yaml -n llm-system`<br>`oc rollout status deployment/ollama -n llm-system` | Strategy: `Recreate` (not `RollingUpdate`). Node: `ocp-w-2` (taint `dedicated=llm:NoSchedule`, 12 vCPU) |
| **15** | Pull `llama3.2:3b` model | `OLLAMA_POD=$(oc get pod -n llm-system -l app=ollama -o jsonpath='{.items[0].metadata.name}')`<br>`oc exec -n llm-system $OLLAMA_POD -- ollama pull llama3.2:3b` | Model stored at `/models/` inside pod. ~5 min on first pull |
| **16** | Build + deploy LLM Service | Same pattern as #11, service `llm-service` | Orchestrates `audit_findings` → LLM prompt → `llm_analyses` table. Uses stdlib `http.server`, not FastAPI |
| **17** | Build + deploy Anomaly Detector | Same pattern as #11, service `anomaly-detector` | Z-score (Welford, 2 tiers) + Isolation Forest. Polls `flow_baseline` every 30s |
| **18** | Apply Prometheus alerting rules | `oc apply -f ~/netpol-intelligence/prometheus/netpol-rules.yaml -n netpol-system` | AlertManager rules for anomaly severity thresholds |

### PHASE 4 — API Gateway & React Dashboard

| # | Step | Command(s) | Notes |
|---|---|---|---|
| **19** | Build + deploy API Gateway | **Path A:** `oc apply -f ~/netpol-intelligence/netpol-p4/deploy/api-gateway-deployment.yaml` (image: `docker.io/nassimsai/api-gateway:v1.0-pfe2026`)<br>**Path B:** `oc new-build --name=api-gateway --binary --strategy=docker -n netpol-system && oc start-build api-gateway --from-dir=$HOME/netpol-intelligence/netpol-p4/api-gateway/ --follow -n netpol-system` | FastAPI; exposes 8 REST + 1 SSE endpoint. API key: `netpol-demo-2026` (header `X-API-Key`) |
| **20** | Build + deploy React Dashboard | Same pattern as #19, service `netpol-dashboard` (image: `docker.io/nassimsai/netpol-dashboard:v1.0-pfe2026`) | React 18 + Vite; served via nginx; route: `https://netpol-dashboard.apps.lab.ocp.lan` |
| **21** | Apply Phase 4 NetworkPolicies | `oc apply -f ~/netpol-intelligence/netpol-p4/deploy/netpol-phase4.yaml` | Allows dashboard ↔ api-gateway; allows api-gateway ↔ timescaledb and llm-service |

---

## 4. Verification — Health Checks

| # | Step | Command(s) | Expected Result |
|---|---|---|---|
| **22** | All pods running | `oc get pods -n netpol-system`<br>`oc get pods -n llm-system` | `audit-engine`, `flow-processor`, `llm-service`, `anomaly-detector`, `api-gateway`, `netpol-dashboard`, `timescaledb-0`, `loki`, `grafana`, `fluent-bit-*` all `Running` |
| **23** | Verify data pipeline (DB) | `oc exec -n netpol-system timescaledb-0 -- psql -U postgres -d netpol_intelligence -c "SELECT 'audit_findings' tbl, COUNT(*) FROM audit_findings UNION ALL SELECT 'flow_events', COUNT(*) FROM flow_events UNION ALL SELECT 'anomaly_events', COUNT(*) FROM anomaly_events UNION ALL SELECT 'llm_analyses', COUNT(*) FROM llm_analyses;"` | All tables show rows within 5–10 minutes of deployment |
| **24** | Smoke-test API Gateway | `curl -sk -H "X-API-Key: netpol-demo-2026" https://netpol-dashboard.apps.lab.ocp.lan/api/compliance \| python3 -m json.tool` | `{"score": <0-100>, "grade": "<A-F>", ...}` |
| **25** | Smoke-test LLM chatbot (SSE) | `curl -sk -H "X-API-Key: netpol-demo-2026" -H "Content-Type: application/json" -d '{"message":"Summarise current compliance status"}' https://netpol-dashboard.apps.lab.ocp.lan/api/chat` | SSE stream with `data: {...}` events from `llama3.2:3b` |
| **26** | Verify Grafana datasources | `for id in 1 2 3; do curl -s -u admin:Admin@OCP123 http://grafana.apps.lab.ocp.lan/api/datasources/${id}/health \| python3 -m json.tool; done` | ID1 Prometheus OK, ID2 Loki OK, ID3 TimescaleDB OK |
| **27** | Post-restart Grafana fix (if needed) | `PERM_TOKEN=$(oc get secret prometheus-grafana-token -n openshift-monitoring -o jsonpath='{.data.token}' \| base64 -d)`<br>`curl -s -X PUT -H "Content-Type: application/json" -u admin:Admin@OCP123 http://grafana.apps.lab.ocp.lan/api/datasources/3 -d '{"id":3,"uid":"bfjel3ansj08wa","name":"TimescaleDB","type":"grafana-postgresql-datasource","database":"netpol_intelligence","jsonData":{"sslmode":"require","database":"netpol_intelligence"}}'` | Required after every Grafana pod restart — Grafana 12 known bug (database field not persisted) |
| **28** | Rebuild any service (standard pattern) | `oc start-build <service-name> --from-dir=<src-dir>/ --follow -n netpol-system`<br>`oc rollout restart deployment/<service-name> -n netpol-system` | Always run `rollout restart` explicitly — deployments do **not** auto-rollout on new image tag in this cluster |

---

## 5. Access URLs & Credentials

| Service | URL | Credentials |
|---|---|---|
| Dashboard (React SPA) | https://netpol-dashboard.apps.lab.ocp.lan | `X-API-Key: netpol-demo-2026` |
| API Gateway | https://netpol-dashboard.apps.lab.ocp.lan/api/ | `X-API-Key: netpol-demo-2026` |
| Grafana | http://grafana.apps.lab.ocp.lan | `admin` / `Admin@OCP123` |
| Online Boutique (test) | http://boutique.apps.lab.ocp.lan | Public |
| OpenShift Console | https://console-openshift-console.apps.lab.ocp.lan | `kubeadmin` |
| Ollama API (internal) | http://ollama.llm-system.svc.cluster.local:11434 | Cluster-internal only |

> **⚠️ Never commit real credentials to the repository.** Use `deploy/secrets.example.yaml` as a template and create actual secrets via `oc create secret` post-clone.

---

## 6. Known Behaviours & Operational Notes

| Topic | Known Behaviour / Action Required |
|---|---|
| **DNS port** | OVN-K evaluates NetworkPolicy post-DNAT → DNS traffic hits port 5353 internally, not 53. Allow both in egress rules. |
| **OVN ingress** | OCP Router NetworkPolicy requires two separate `from:` entries — `kubernetes.io/metadata.name=openshift-ingress` **and** `network.openshift.io/policy-group=ingress`. |
| **Kyverno scope** | `generate` ClusterPolicies must **not** target system namespaces (risk of cluster outage — see incident note below). Use opt-in label strategy on target namespaces. |
| **Ollama strategy** | Deployment strategy must be `Recreate` (not `RollingUpdate`). Two 10+ vCPU pods cannot coexist on `ocp-w-2`. |
| **`oc exec` target** | Target `timescaledb-0` directly — `oc exec deploy/timescaledb` fails (StatefulSet, not Deployment). |
| **Rollout restart** | After every `oc start-build`, manually run `oc rollout restart` — deployments do **not** auto-rollout on new image tag. |
| **Grafana 12 bug** | After pod restart, re-PUT the TimescaleDB datasource. The `database` field is not persisted correctly in Grafana 12. |
| **K8s API egress** | `netpol-system` pods need unrestricted egress to `172.30.0.1:443` (K8s API). This CIDR cannot be matched by NetworkPolicy selectors — expect an unavoidable NP-003 finding here. |

> **⚠️ Incident history:** early Kyverno installs generated `default-deny-all` NetworkPolicies in **all** `openshift-*` namespaces (including `openshift-etcd`), causing a cluster outage requiring manual SSH recovery. The Phase 1, Step 10 configuration above (opt-in label selectors + explicit namespace exclusions) prevents recurrence — do not deploy Kyverno `ClusterPolicies` without these safeguards.

---

## 7. Support Contacts

- **Cloud & Infrastructure Director:** Arafet Ben Kilani — NextStep IT, Charguia 1, Tunis
- **Project Author:** Nassim Saii — Network & System Security Engineering, TEK-UP University
- **Academic Supervisor:** Khaoula Ammar — TEK-UP University

---

*NetworkPolicy Intelligence System — Deployment Runbook*
