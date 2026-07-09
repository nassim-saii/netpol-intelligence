import { apiFetch } from './api.js'
import { useState, useEffect } from 'react'

export default function ComplianceReport() {
  const [rescanning, setRescanning] = useState(false)
  const [rescanMsg, setRescanMsg] = useState(null)

  const handleRescan = async () => {
    setRescanning(true)
    setRescanMsg(null)
    try {
      const res = await apiFetch('/api/rescan', { method: 'POST' })
      const data = await res.json()
      setRescanMsg(`✅ Rescan complete — Grade ${data.grade} (${data.score}/100)`)
      // Reload all data without page reload
      await load()
    } catch (e) {
      setRescanMsg('❌ Rescan failed: ' + e.message)
    } finally {
      setRescanning(false)
    }
  }

  const [compliance, setCompliance] = useState(null)
  const [findings, setFindings] = useState([])
  const [hours, setHours] = useState(720)
  const [severity, setSeverity] = useState('all')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ hours, limit: 100 })
      if (severity !== 'all') params.set('severity', severity)
      const [c, f] = await Promise.all([
        apiFetch('/api/compliance').then(r => r.json()),
        apiFetch('/api/findings?' + params + '&include_resolved=true').then(r => r.json()),
      ])
      setCompliance(c); setFindings(f.findings || [])
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  useEffect(() => { load() }, [hours, severity])

  const scoreColor = compliance?.score >= 90 ? '#3fb950' : compliance?.score >= 60 ? '#d29922' : '#f85149'

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:20 }}>
        <h2 style={{ fontWeight:700, fontSize:18 }}>Compliance Report</h2>
        <select value={hours} onChange={e => setHours(+e.target.value)}
          style={{ background:'var(--bg-card)', border:'1px solid var(--border)',
            color:'var(--text)', borderRadius:6, padding:'4px 8px', fontSize:12 }}>
          {[[1,'1h'],[6,'6h'],[24,'1d'],[48,'2d'],[168,'7d'],[720,'30d']].map(([v,l]) => <option key={v} value={v}>Last {l}</option>)}
        </select>
        <select value={severity} onChange={e => setSeverity(e.target.value)}
          style={{ background:'var(--bg-card)', border:'1px solid var(--border)',
            color:'var(--text)', borderRadius:6, padding:'4px 8px', fontSize:12 }}>
          <option value='all'>All Severities</option>
          {['CRITICAL','HIGH','MEDIUM','LOW'].map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      {loading ? <div className='spinner' /> : (<>
        {compliance && (
          <div style={{ display:'flex', gap:16, marginBottom:20, flexWrap:'wrap' }}>
            <div className='card' style={{ display:'flex', alignItems:'center', gap:20, flex:1, minWidth:220 }}>
              <div style={{ width:80, height:80, borderRadius:'50%',
                border:'6px solid '+scoreColor, display:'flex', flexDirection:'column',
                alignItems:'center', justifyContent:'center' }}>
                <span style={{ fontSize:20, fontWeight:700, color:scoreColor }}>{compliance.score}</span>
                <span style={{ fontSize:10, color:'var(--text-muted)' }}>/ 100</span>
              </div>
              <div>
                <div style={{ fontSize:22, fontWeight:700, color:scoreColor }}>Grade {compliance.grade}</div>
                <button
                  onClick={handleRescan}
                  disabled={rescanning}
                  style={{
                    marginTop:8, padding:'6px 14px',
                    background: rescanning ? '#444' : '#238636',
                    color:'#fff', border:'none', borderRadius:6,
                    cursor: rescanning ? 'not-allowed' : 'pointer',
                    fontSize:13, fontWeight:600
                  }}>
                  {rescanning ? '⏳ Scanning...' : '🔄 Rescan'}
                </button>
                {rescanMsg && (
                  <div style={{marginTop:6, fontSize:12, color:'#8b949e'}}>
                    {rescanMsg}
                  </div>
                )}
                <div style={{ fontSize:12, color:'var(--text-muted)' }}>
                  {compliance.namespaces_with_violations} of {compliance.total_namespaces} namespaces violated
                </div>
                <div style={{ display:'flex', gap:10, marginTop:8, flexWrap:'wrap' }}>
                  {[['CRITICAL',compliance.critical],['HIGH',compliance.high],
                    ['MEDIUM',compliance.medium],['LOW',compliance.low]].map(([s,c]) => (
                    <span key={s}><span className={'badge '+s}>{s}</span> <b>{c}</b></span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
        <div className='card'>
          <div style={{ fontWeight:600, marginBottom:12, display:'flex', alignItems:'center', gap:12 }}>
            Audit Findings ({findings.filter(f => !f.resolved).length})
            <span style={{ fontSize:12, color:'var(--text-muted)', fontWeight:400 }}>
              <span style={{ color:'#f85149', fontWeight:700 }}>{findings.filter(f => !f.resolved).length} active</span> ·{' '}
              <span style={{ color:'#3fb950' }}>{findings.filter(f => f.resolved).length} fixed</span>
              </span>
          </div>
          <div style={{ overflowX:'auto' }}>
            <table>
              <thead><tr>
                <th>Time</th><th>Rule</th><th>Namespace</th>
                <th>Severity</th><th>Status</th><th>Policy</th><th>Message</th>
              </tr></thead>
              <tbody>
                {findings.length === 0 ? (
                  <tr><td colSpan={6} style={{ textAlign:'center', color:'var(--text-muted)', padding:24 }}>
                    No findings in this window 🎉
                  </td></tr>
                ) : (() => { const active = findings.filter(f => !f.resolved); return active.length === 0 ? (<tr><td colSpan={7} style={{ textAlign:"center", color:"var(--text-muted)", padding:24 }}>No findings in this window 🎉</td></tr>) : active.map((f,i) => (
                  <tr key={i} style={{ opacity: f.resolved ? 0.4 : 1 }}>
                    <td className='mono' style={{ whiteSpace:'nowrap', color:'var(--text-muted)' }}>
                      {f.time ? (d => {
  const dt = new Date(d);
  return dt.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'})
    + ' ' + String(dt.getDate()).padStart(2,'0')
    + '/' + String(dt.getMonth()+1).padStart(2,'0');
})(f.time) : '—'}
                    </td>
                    <td className='mono'>{f.rule_id}</td>
                    <td style={{ color:'var(--accent)', fontSize:12 }}>{f.namespace}</td>
                    <td><span className={'badge '+f.severity}>{f.severity}</span></td>
                    <td>
                      {f.resolved
                        ? <span style={{ fontSize:11, color:'#3fb950', background:'rgba(63,185,80,0.1)',
                            padding:'2px 8px', borderRadius:10, border:'1px solid #3fb950',
                            fontWeight:600 }}>✓ Fixed</span>
                        : f.exempted
                          ? <span style={{ fontSize:11, color:'#f85149', background:'rgba(248,81,73,0.1)',
                              padding:'2px 8px', borderRadius:10, border:'1px solid #f85149',
                              fontWeight:600, cursor:'help' }}
                              title={f.exemption_reason || 'Exempted from scoring'}>⚠ Active</span>
                          : <span style={{ fontSize:11, color:'#f85149', background:'rgba(248,81,73,0.1)',
                              padding:'2px 8px', borderRadius:10, border:'1px solid #f85149',
                              fontWeight:600 }}>⚠ Active</span>}
                    </td>
                    <td className='mono' style={{ color:'var(--text-muted)', fontSize:11 }}>{f.policy_name||'—'}</td>
                    <td style={{ maxWidth:300, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
                        textDecoration: f.resolved ? 'line-through' : 'none' }}
                        title={f.message}>{f.message}</td>
                  </tr>
                ))})()}
              </tbody>
            </table>
          </div>
        </div>
      </>)}
    </div>
  )
}
