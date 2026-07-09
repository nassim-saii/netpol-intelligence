# ── Prompt templates ──────────────────────────────────────────────────────────
# NOTE: detected_at is included for human readability in stored analyses,
#       but is intentionally EXCLUDED from hash keys (see *_HASH_KEY below)
#       to ensure cache hits across repeated detections of the same pattern.

ANOMALY_PROMPT = """You are a Kubernetes network security analyst. Analyze this traffic anomaly detected in an OpenShift cluster.
Anomaly Type: {anomaly_type}
Rule ID: {rule_id}
Source Namespace: {src_namespace}
Destination Namespace: {dst_namespace}
Destination Port: {dst_port}
Severity: {severity}
Description: {description}
Z-Score: {z_score}
Detected At: {detected_at}
Respond ONLY with a valid JSON object with exactly these fields, no preamble, no markdown:
{{
  "explanation": "plain English explanation of this anomaly in 2-3 sentences",
  "possible_causes": ["cause1", "cause2"],
  "is_likely_attack": true or false,
  "recommended_action": "immediate action to take",
  "priority": "CRITICAL or HIGH or MEDIUM or LOW"
}}"""

# Hash key: stable fields only — no detected_at, no z_score (floats drift)
ANOMALY_HASH_KEY = "{anomaly_type}|{rule_id}|{src_namespace}|{dst_namespace}|{dst_port}|{severity}|{description}"


AUDIT_PROMPT = """You are a Kubernetes security expert. Analyze this NetworkPolicy violation detected in an OpenShift cluster.
Rule Violated: {rule_id}
Namespace: {namespace}
Severity: {severity}
Policy Name: {policy_name}
Violation Details: {message}
Detected At: {detected_at}
Respond ONLY with a valid JSON object with exactly these fields, no preamble, no markdown:
{{
  "explanation": "plain English explanation of why this is a security risk in 2-3 sentences",
  "attack_scenario": "realistic attack scenario enabled by this misconfiguration",
  "fix_yaml": "corrected NetworkPolicy YAML snippet that resolves the violation",
  "priority": "CRITICAL or HIGH or MEDIUM or LOW",
  "zero_trust_principle": "which Zero Trust principle is violated"
}}"""

# Hash key: stable fields only — no detected_at
AUDIT_HASH_KEY = "{rule_id}|{namespace}|{severity}|{policy_name}|{message}"
