export default function Documentation() {
  const sectionStyle = { marginBottom: 32 }
  const tableStyle = { borderCollapse: 'collapse', fontSize: 13 }
  const thStyle = { textAlign: 'left', padding: '6px 10px', borderBottom: '2px solid var(--border)', color: 'var(--text-muted)', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }
  const tdStyle = { padding: '6px 10px', borderBottom: '1px solid var(--border)' }
  const codeBlock = { background: 'var(--bg)', padding: '14px 18px', borderRadius: 8, fontFamily: 'monospace', fontSize: 13, lineHeight: 1.6, border: '1px solid var(--border)', overflowX: 'auto' }

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ fontWeight: 700, fontSize: 20, marginBottom: 8 }}>📖 Documentation</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 28 }}>
        Reference guide for the NetPol Intelligence detection engine — scoring, audit rules, and anomaly detection logic.
      </p>

      <div className="card" style={sectionStyle}>
        <h3 style={{ fontWeight: 700, fontSize: 16, marginBottom: 12 }}>🎯 Compliance Score — How It's Calculated</h3>
        <pre style={codeBlock}>
{`Score = 100 - (CRITICAL × 20 + HIGH × 10 + MEDIUM × 5 + LOW × 2)
                        capped at 0 minimum`}
        </pre>
        <div style={{ display: 'flex', gap: 32, marginTop: 16, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>GRADE SCALE</div>
            <table style={{ ...tableStyle, minWidth: 140 }}>
              <thead><tr><th style={thStyle}>Grade</th><th style={thStyle}>Range</th></tr></thead>
              <tbody>
                {[['A', '90–100', 'var(--low)'], ['B', '80–89', '#58a6ff'], ['C', '70–79', '#d29922'], ['D', '60–69', 'var(--high)'], ['F', '< 60', 'var(--critical)']].map(([g, r, c]) => (
                  <tr key={g}><td style={{ ...tdStyle, fontWeight: 700, color: c }}>{g}</td><td style={tdStyle}>{r}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>SEVERITY WEIGHTS</div>
            <table style={{ ...tableStyle, minWidth: 180 }}>
              <thead><tr><th style={thStyle}>Severity</th><th style={thStyle}>Penalty</th></tr></thead>
              <tbody>
                {[['CRITICAL', '−20 pts', 'var(--critical)'], ['HIGH', '−10 pts', 'var(--high)'], ['MEDIUM', '−5 pts', '#d29922'], ['LOW', '−2 pts', '#58a6ff']].map(([s, p, c]) => (
                  <tr key={s}><td style={tdStyle}><span className={'badge ' + s}>{s}</span></td><td style={{ ...tdStyle, fontWeight: 600, color: c, fontFamily: 'monospace' }}>{p}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card" style={sectionStyle}>
        <h3 style={{ fontWeight: 700, fontSize: 16, marginBottom: 12 }}>🔍 Detection Rules (NP-001 → NP-008)</h3>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ ...tableStyle, width: '100%' }}>
            <thead><tr><th style={thStyle}>Rule ID</th><th style={thStyle}>Name</th><th style={thStyle}>Severity</th><th style={thStyle}>What It Detects</th></tr></thead>
            <tbody>
              {[
                ['NP-001', 'Missing default-deny', 'HIGH', 'Namespace has no policy with empty podSelector and no ingress rules'],
                ['NP-002', 'Allow-all ingress', 'CRITICAL', 'Ingress rule has empty from: list — any source can reach any pod'],
                ['NP-003', 'Allow-all egress', 'HIGH', 'Egress rule has empty to: list — unrestricted outbound destination'],
                ['NP-004', 'Cross-namespace wildcard', 'MEDIUM', 'namespaceSelector: {} with no matchLabels — any namespace can ingress'],
                ['NP-005', 'Missing egress policy', 'MEDIUM', 'Namespace has policies but none include Egress in policyTypes'],
                ['NP-006', 'Privileged port exposure', 'LOW', 'Ingress allowed to ports < 1024 from broad selector'],
                ['NP-007', 'Policy conflict', 'MEDIUM', 'One policy allows ALL ingress while another restricts — overlapping podSelector'],
                ['NP-008', 'Unrestricted DNS', 'LOW', 'Namespace has egress policies but no explicit port 53/UDP restriction'],
              ].map(([id, name, sev, desc]) => (
                <tr key={id}>
                  <td style={{ ...tdStyle, fontWeight: 700, fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{id}</td>
                  <td style={{ ...tdStyle, fontWeight: 600, whiteSpace: 'nowrap' }}>{name}</td>
                  <td style={tdStyle}><span className={'badge ' + sev}>{sev}</span></td>
                  <td style={{ ...tdStyle, color: 'var(--text-muted)', fontSize: 12 }}>{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={sectionStyle}>
        <h3 style={{ fontWeight: 700, fontSize: 16, marginBottom: 12 }}>⚡ Rule-Based Anomaly Engine (ANOM-001 → ANOM-003)</h3>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ ...tableStyle, width: '100%' }}>
            <thead><tr><th style={thStyle}>Rule ID</th><th style={thStyle}>Name</th><th style={thStyle}>Severity</th><th style={thStyle}>Detection Logic</th></tr></thead>
            <tbody>
              {[
                ['ANOM-001', 'Access to control plane port from app namespace', 'CRITICAL', 'dst_port ∈ {2379, 2380, 6443, 22623} AND src_namespace ∉ sensitive_ns'],
                ['ANOM-002', 'Cross-namespace allowed communication', 'HIGH', 'src_ns ≠ dst_ns AND verdict=allow AND src not in kube-system/monitoring'],
                ['ANOM-003', 'Unexpected external egress', 'HIGH', 'dst_ip not RFC1918 AND port not in {53, 123, 443, 80} AND verdict=allow'],
              ].map(([id, name, sev, logic]) => (
                <tr key={id}>
                  <td style={{ ...tdStyle, fontWeight: 700, fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{id}</td>
                  <td style={{ ...tdStyle, fontWeight: 600 }}>{name}</td>
                  <td style={tdStyle}><span className={'badge ' + sev}>{sev}</span></td>
                  <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: 12, color: 'var(--text-muted)' }}>{logic}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
