from dataclasses import dataclass
from datetime import datetime

SENSITIVE_NAMESPACES = {"kube-system", "openshift-etcd", "openshift-kube-apiserver"}
SENSITIVE_PORTS      = {2379, 2380, 6443, 22623}  # etcd, API, MCS
# Ports that generate expected drops in K8s — not real NP gaps
NOISE_PORTS          = {5353, 5355}  # mDNS, LLMNR — not used in K8s, always dropped

@dataclass
class FlowEvent:
    src_namespace: str
    dst_namespace: str
    src_ip: str
    dst_ip: str
    dst_port: int
    verdict: str
    timestamp: datetime

RULES = [
    {
        "id": "ANOM-001",
        "name": "Access to control plane port from application namespace",
        "severity": "CRITICAL",
        "check": lambda f: (
            f.dst_port in SENSITIVE_PORTS and
            f.src_namespace not in SENSITIVE_NAMESPACES
        )
    },
    {
        "id": "ANOM-002",
        "name": "Unexpected drop — possible NetworkPolicy gap",
        "severity": "HIGH",
        "check": lambda f: (
            f.verdict == "drop" and
            f.dst_port not in NOISE_PORTS and
            f.src_namespace not in {"kube-system", "openshift-monitoring"}
        )
    },
    {
        "id": "ANOM-003",
        "name": "Unexpected external egress dropped",
        "severity": "MEDIUM",
        "check": lambda f: (
            f.dst_ip != "" and
            not f.dst_ip.startswith(("10.", "172.", "192.168.")) and
            f.dst_port not in {53, 123, 443, 80} and
            f.verdict == "drop"
        )
    },
]

def evaluate_rules(flow: FlowEvent) -> list:
    return [
        {"rule": r["id"], "name": r["name"], "severity": r["severity"]}
        for r in RULES if r["check"](flow)
    ]
