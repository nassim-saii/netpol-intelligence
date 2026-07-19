from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class AuditFinding:
    rule_id: str
    namespace: str
    severity: str
    message: str
    policy_name: str = None
    policy_yaml: dict = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

def get_ingress_from(rule):
    """Get ingress from peers - handles both from_ and _from attribute names."""
    return getattr(rule, "_from", None) or getattr(rule, "from_", None)

def check_default_deny(namespace, policies):
    has_default_deny = any(
        p.spec.pod_selector.match_labels is None
        and p.spec.pod_selector.match_expressions is None
        and (p.spec.ingress is None or p.spec.ingress == [])
        for p in policies
    )
    if not has_default_deny:
        return [AuditFinding(rule_id="NP-001", namespace=namespace, severity="HIGH",
            message=f"Namespace {namespace} has no default-deny NetworkPolicy")]
    return []

def check_allow_all_ingress(namespace, policies):
    findings = []
    for p in policies:
        if p.spec.ingress:
            for rule in p.spec.ingress:
                peers = get_ingress_from(rule)
                if peers is None or peers == []:
                    findings.append(AuditFinding(rule_id="NP-002", namespace=namespace,
                        severity="CRITICAL",
                        message=f"Policy {p.metadata.name} allows ALL ingress",
                        policy_name=p.metadata.name))
    return findings

def check_allow_all_egress(namespace, policies):
    findings = []
    for p in policies:
        if p.spec.egress:
            for rule in p.spec.egress:
                if rule.to is None or rule.to == []:
                    findings.append(AuditFinding(rule_id="NP-003", namespace=namespace,
                        severity="HIGH",
                        message=f"Policy {p.metadata.name} allows ALL egress",
                        policy_name=p.metadata.name))
    return findings

def check_missing_egress(namespace, policies):
    has_egress = any(
        p.spec.policy_types and "Egress" in p.spec.policy_types
        for p in policies
    )
    if not has_egress and policies:
        return [AuditFinding(rule_id="NP-005", namespace=namespace, severity="MEDIUM",
            message=f"Namespace {namespace} has no egress NetworkPolicy")]
    return []

def check_cross_namespace_wildcard(namespace, policies):
    findings = []
    for p in policies:
        if p.spec.ingress:
            for rule in p.spec.ingress:
                peers = get_ingress_from(rule)
                if peers:
                    for peer in peers:
                        if (peer.namespace_selector is not None
                                and peer.namespace_selector.match_labels is None
                                and peer.namespace_selector.match_expressions is None):
                            findings.append(AuditFinding(rule_id="NP-004",
                                namespace=namespace, severity="MEDIUM",
                                message=f"Policy {p.metadata.name} uses unrestricted namespaceSelector",
                                policy_name=p.metadata.name))
    return findings

def audit_namespace(namespace, policies):
    findings = []
    findings += check_default_deny(namespace, policies)
    findings += check_allow_all_ingress(namespace, policies)
    findings += check_allow_all_egress(namespace, policies)
    findings += check_missing_egress(namespace, policies)
    findings += check_cross_namespace_wildcard(namespace, policies)
    findings += check_privileged_port_exposure(namespace, policies)
    findings += check_policy_conflict(namespace, policies)
    findings += check_unrestricted_dns(namespace, policies)
    return findings

def check_privileged_port_exposure(namespace, policies):
    """NP-006: Policy allows ingress to privileged ports (<1024) from broad selector."""
    findings = []
    for p in policies:
        if p.spec.ingress:
            for rule in p.spec.ingress:
                peers = get_ingress_from(rule)
                # Broad selector = no from peers OR namespaceSelector without labels
                is_broad = (peers is None or peers == [] or
                    any(getattr(peer, 'namespace_selector', None) and
                        not getattr(getattr(peer, 'namespace_selector', None),
                            'match_labels', None)
                        for peer in (peers or [])))
                if is_broad and rule.ports:
                    for port in rule.ports:
                        port_num = port.port if isinstance(port.port, int) else 0
                        if port_num and port_num < 1024:
                            findings.append(AuditFinding(
                                rule_id="NP-006", namespace=namespace,
                                severity="LOW",
                                message=f"Policy {p.metadata.name} exposes privileged port {port_num} from broad selector",
                                policy_name=p.metadata.name))
    return findings

def check_policy_conflict(namespace, policies):
    """NP-007: One policy allows ALL ingress while another restricts — real conflict."""
    findings = []
    seen = set()
    for i, p1 in enumerate(policies):
        for p2 in policies[i+1:]:
            labels1 = getattr(p1.spec.pod_selector, 'match_labels', None) or {}
            labels2 = getattr(p2.spec.pod_selector, 'match_labels', None) or {}
            # Skip default-deny — it's intentionally complementary, not a conflict
            names = {p1.metadata.name, p2.metadata.name}
            if 'default-deny-all' in names or 'default-deny' in names:
                continue
            # Both select all pods
            if not labels1 and not labels2:
                # p1 allows ALL ingress (empty from) AND p2 has restricted ingress
                p1_allows_all = p1.spec.ingress and any(
                    not get_ingress_from(r) for r in p1.spec.ingress)
                p2_allows_all = p2.spec.ingress and any(
                    not get_ingress_from(r) for r in p2.spec.ingress)
                if p1_allows_all and p2.spec.ingress and not p2_allows_all:
                    key = tuple(sorted([p1.metadata.name, p2.metadata.name]))
                    if key not in seen:
                        seen.add(key)
                        findings.append(AuditFinding(
                            rule_id="NP-007", namespace=namespace,
                            severity="MEDIUM",
                            message=f"Policy conflict: '{p1.metadata.name}' allows ALL ingress but '{p2.metadata.name}' restricts it — overlapping podSelector",
                            policy_name=p1.metadata.name))
    return findings

def check_unrestricted_dns(namespace, policies):
    """NP-008: No policy explicitly restricting DNS egress to port 53 only."""
    has_dns_policy = any(
        p.spec.egress and any(
            rule.ports and any(
                (p.port == 53 or p.port == "53") for p in rule.ports
            ) for rule in p.spec.egress
        ) for p in policies
    )
    has_egress_policy = any(
        p.spec.policy_types and "Egress" in p.spec.policy_types
        for p in policies
    )
    if has_egress_policy and not has_dns_policy:
        return [AuditFinding(
            rule_id="NP-008", namespace=namespace, severity="LOW",
            policy_name=f"{namespace}-egress",
            message=f"Namespace {namespace} has egress policies but no explicit DNS (port 53) restriction")]
    return []
