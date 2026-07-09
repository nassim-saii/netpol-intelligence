import { apiFetch } from './api.js'
import { useState, useEffect } from 'react'

export default function AnomalyTimeline() {
  const [anomalies, setAnomalies] = useState([])
  const [loading, setLoading] = useState(true)
  const [hours, setHours] = useState(24)
  const [selected, setSelected] = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      const r = await apiFetch('/api/anomalies?limit=100&hours='+hours)
      const d = await r.json()
      setAnomalies(d.anomalies || [])
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  useEffect(() => { load() }, [hours])

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:20 }}>
        <h2 style={{ fontWeight:700, fontSize:18 }}>Anomaly Timeline</h2>
        <select value={hours} onChange={e => setHours(+e.target.value)}
          style={{ background:'var(--bg-card)', border:'1px solid var(--border)',
            color:'var(--text)', borderRadius:6, padding:'4px 8px', fontSize:12 }}>
          {[1,6,24,48,168].map(h => <option key={h} value={h}>Last {h}h</option>)}
        </select>
        <span style={{ color:'var(--text-muted)', fontSize:12 }}>{anomalies.length} events</span>
      </div>
      {loading ? <div className='spinner' /> : (
        <div style={{ display:'flex', gap:16 }}>
          <div className='card' style={{ flex:1 }}>
            <div style={{ fontWeight:600, marginBottom:12 }}>Events</div>
            {anomalies.length === 0 ? (
              <div style={{ textAlign:'center', color:'var(--text-muted)', padding:40 }}>
                <div style={{ fontSize:32, marginBottom:12 }}>✅</div>
                No anomalies detected in this window
              </div>
            ) : (
              <div style={{ maxHeight:500, overflowY:'auto' }}>
                {anomalies.map((a,i) => (
                  <div key={i} onClick={() => setSelected(selected===i ? null : i)} style={{
                    padding:'10px 12px', marginBottom:4, borderRadius:6, cursor:'pointer',
                    background: selected===i ? 'rgba(88,166,255,.1)' : 'var(--bg-card2)',
                    border: '1px solid '+(selected===i ? 'rgba(88,166,255,.4)' : 'var(--border)'),
                  }}>
                    <div style={{ display:'flex', gap:8, alignItems:'center', marginBottom:4 }}>
                      <span className={'badge '+a.severity}>{a.severity}</span>
                      <span className='mono' style={{ fontSize:11 }}>{a.rule_id}</span>
                      <span style={{ marginLeft:'auto', fontSize:10, color:'var(--text-muted)', textAlign:'right' }}>
                        {a.occurrences > 1 && (
                          <span style={{ background:'#d29922', color:'#000', borderRadius:10,
                            padding:'1px 7px', fontWeight:700, fontSize:11, marginRight:6 }}>
                            ×{a.occurrences}
                          </span>
                        )}
                        {a.last_seen ? (d => {
  const dt = new Date(d);
  return dt.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'})
    + ' ' + String(dt.getDate()).padStart(2,'0')
    + '/' + String(dt.getMonth()+1).padStart(2,'0');
})(a.last_seen) : '—'}
                        {a.occurrences > 1 && a.first_seen && (
                          <div style={{ fontSize:9, color:'var(--text-muted)', marginTop:1 }}>
                            first: {(d => {
  const dt = new Date(d);
  return dt.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'})
    + ' ' + String(dt.getDate()).padStart(2,'0')
    + '/' + String(dt.getMonth()+1).padStart(2,'0');
})(a.first_seen)}
                          </div>
                        )}
                      </span>
                    </div>
                    <div style={{ fontSize:12, color:'var(--text-muted)' }}>
                      {a.src_namespace} → {a.dst_namespace}:{a.dst_port}
                    </div>
                    {selected===i && (
                      <div style={{ marginTop:8, padding:8, background:'rgba(0,0,0,.3)',
                        borderRadius:4, fontSize:12 }}>
                        <div><b>Type:</b> {a.anomaly_type}</div>
                        <div><b>Z-Score:</b> {a.z_score?.toFixed(2) ?? '—'}</div>
                        {a.description && <div style={{ marginTop:4, color:'var(--text-muted)' }}>{a.description}</div>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{ width:180 }}>
            <div className='card' style={{ marginBottom:12 }}>
              <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:8 }}>BY SEVERITY</div>
              {['CRITICAL','HIGH','MEDIUM','LOW'].map(sev => {
                const cnt = anomalies.filter(a => a.severity===sev).length
                return cnt > 0 ? (
                  <div key={sev} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
                    <span className={'badge '+sev}>{sev}</span>
                    <span style={{ fontWeight:700 }}>{cnt}</span>
                  </div>
                ) : null
              })}
              {anomalies.length===0 && <div style={{ fontSize:12, color:'var(--text-muted)' }}>None</div>}
            </div>
            <div className='card'>
              <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:8 }}>BY TYPE</div>
              {[...new Set(anomalies.map(a => a.anomaly_type))].filter(Boolean).map(type => (
                <div key={type} style={{ display:'flex', justifyContent:'space-between', marginBottom:4, fontSize:12 }}>
                  <span className='mono' style={{ fontSize:10, color:'var(--text-muted)' }}>{type}</span>
                  <span>{anomalies.filter(a => a.anomaly_type===type).length}</span>
                </div>
              ))}
              {anomalies.length===0 && <div style={{ fontSize:12, color:'var(--text-muted)' }}>None</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
