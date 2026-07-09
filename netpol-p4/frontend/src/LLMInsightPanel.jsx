import { apiFetch } from './api.js'
import { useState, useEffect } from 'react'

export default function LLMInsightPanel({ initialFilter, onFilterApplied } = {}) {
  const [filter, setFilter]     = useState('all')
  const [analyses, setAnalyses] = useState([])
  const [loading, setLoading]   = useState(true)
  const [hours, setHours]       = useState(720)
  const [expanded, setExpanded] = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      let url = '/api/llm-analyses?limit=50&hours=' + hours
      if (filter === 'anomalies') url += '&source_type=anomaly'
      if (filter === 'audit')     url += '&source_type=audit'
      const d = await apiFetch(url).then(r => r.json())
      setAnalyses(d.analyses || [])
    } catch(e) { console.error(e) }
    setLoading(false)
  }


  useEffect(() => {
    if (initialFilter) {
      setFilter(initialFilter);
      if (onFilterApplied) onFilterApplied();
    }
  }, [initialFilter]);
  useEffect(() => { load() }, [filter, hours])

  const fmt = d => {
    if (!d) return '—'
    const dt = new Date(d)
    return dt.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'})
      + ' ' + String(dt.getDate()).padStart(2,'0') + '/' + String(dt.getMonth()+1).padStart(2,'0')
  }
  const sc = s => s==='CRITICAL'?'var(--critical)':s==='HIGH'?'var(--high)':s==='MEDIUM'?'#d29922':'var(--low)'

  const renderDetail = (resp) => {
    if (!resp) return null
    if (typeof resp === 'string') return <p style={{fontSize:12,color:'var(--text-muted)',marginTop:8}}>{resp}</p>
    return (
      <div style={{fontSize:12,marginTop:12,paddingTop:12,borderTop:'1px solid var(--border)'}}>
        {resp.explanation && <div style={{marginBottom:8}}>
          <b style={{color:'var(--accent)'}}>💡 Explanation</b>
          <p style={{color:'var(--text-muted)',lineHeight:1.6,marginTop:4}}>{resp.explanation}</p>
        </div>}
        {resp.attack_scenario && <div style={{marginBottom:8}}>
          <b style={{color:'var(--high)'}}>⚔️ Attack Scenario</b>
          <p style={{color:'var(--text-muted)',lineHeight:1.6,marginTop:4}}>{resp.attack_scenario}</p>
        </div>}
        {resp.possible_causes && <div style={{marginBottom:8}}>
          <b style={{color:'#d29922'}}>🔍 Possible Causes</b>
          <ul style={{paddingLeft:16,color:'var(--text-muted)',lineHeight:1.6,marginTop:4}}>
            {resp.possible_causes.map((c,i)=><li key={i}>{c}</li>)}
          </ul>
        </div>}
        {resp.recommended_action && <div style={{marginBottom:8}}>
          <b style={{color:'var(--low)'}}>✅ Recommended Action</b>
          <p style={{color:'var(--text-muted)',lineHeight:1.6,marginTop:4}}>{resp.recommended_action}</p>
        </div>}
        {resp.fix_yaml && <div style={{marginBottom:8}}>
          <b style={{color:'var(--low)'}}>🔧 Fix YAML</b>
          <pre style={{background:'rgba(0,0,0,.4)',border:'1px solid var(--border)',borderRadius:6,
            padding:12,fontSize:11,overflowX:'auto',marginTop:4}}>{resp.fix_yaml}</pre>
        </div>}
        {resp.zero_trust_principle && <div style={{marginTop:8,padding:'6px 10px',
          background:'rgba(88,166,255,.08)',border:'1px solid rgba(88,166,255,.2)',
          borderRadius:6,fontSize:11,color:'var(--accent)'}}>
          🛡️ Zero Trust: {resp.zero_trust_principle}
        </div>}
      </div>
    )
  }

  return (
    <div>
      <div style={{display:'flex',alignItems:'center',gap:16,marginBottom:20,flexWrap:'wrap'}}>
        <h2 style={{fontWeight:700,fontSize:18}}>🔍 Insights</h2>
        <select value={filter} onChange={e=>{setFilter(e.target.value);setExpanded(null)}}
          style={{background:'var(--bg-card)',border:'1px solid var(--border)',
            color:'var(--text)',borderRadius:6,padding:'4px 8px',fontSize:12}}>
          <option value='all'>All</option>
          <option value='anomalies'>⚠️ Anomaly Events</option>
          <option value='audit'>📋 Audit Findings</option>
        </select>
        <span style={{color:'var(--text-muted)',fontSize:12}}>llama3.2:3b · ocp-w-2</span>
        <span style={{color:'var(--text-muted)',fontSize:12}}>{analyses.length} analyses</span>
      </div>
      {loading ? <div className='spinner'/> : analyses.length === 0 ? (
        <div className='card' style={{textAlign:'center',padding:40,color:'var(--text-muted)'}}>
          <div style={{fontSize:32,marginBottom:12}}>🤖</div>
          No analyses yet. The LLM service polls every 5 minutes.
        </div>
      ) : (
        <div style={{display:'flex',flexDirection:'column',gap:8}}>
          {analyses.map((a, i) => {
            const resp = a.response || {}
            const ev = a.event
            const fi = a.finding
            const sev = fi?.severity || ev?.severity || resp.priority
            const borderCol = sc(sev)
            const isExp = expanded === i
            return (
              <div key={a.id||i} className='card'
                style={{borderLeft:`3px solid ${borderCol}`,cursor:'pointer',
                  transition:'background 0.15s'}}
                onClick={() => setExpanded(isExp ? null : i)}
                onMouseEnter={e => e.currentTarget.style.background='rgba(88,166,255,0.05)'}
                onMouseLeave={e => e.currentTarget.style.background=''}>
                <div style={{display:'flex',alignItems:'flex-start',gap:10,flexWrap:'wrap'}}>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:6,flexWrap:'wrap'}}>
                      <span style={{fontSize:12,fontWeight:600,color:'var(--accent)'}}>
                        {a.source_type==='audit' ? '📋 Audit Finding' : '⚠️ Anomaly'}
                      </span>
                      {sev && <span className={'badge '+sev}>{sev}</span>}
                      {ev && <span className='mono' style={{fontSize:11,color:'var(--text-muted)'}}>{ev.rule_id}</span>}
                      {fi && <span className='mono' style={{fontSize:11,color:'var(--text-muted)'}}>{fi.rule_id}</span>}
                      {fi && (fi.resolved
                        ? <span style={{fontSize:11,color:'var(--low)',background:'rgba(63,185,80,0.1)',
                            padding:'1px 8px',borderRadius:10,border:'1px solid var(--low)'}}>✓ Fixed</span>
                        : <span style={{fontSize:11,color:'var(--critical)',background:'rgba(248,81,73,0.1)',
                            padding:'1px 8px',borderRadius:10,border:'1px solid var(--critical)'}}>⚠ Active</span>
                      )}
                    </div>
                    {ev && (
                      <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:4}}>
                        <span style={{color:'var(--accent)'}}>{ev.src_namespace}</span>
                        {' → '}
                        <span style={{color:'var(--accent)'}}>{ev.dst_namespace}</span>
                        <span>:{ev.dst_port}</span>
                        {ev.z_score && <span style={{marginLeft:8,fontSize:11,color:'#d29922'}}>Z={ev.z_score.toFixed(2)}</span>}
                      </div>
                    )}
                    {fi && (
                      <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:4}}>
                        <span style={{color:'var(--accent)'}}>{fi.namespace}</span>
                        {' · '}
                        <span className='mono' style={{fontSize:11}}>{fi.policy_name}</span>
                        {fi.detected_at && <span style={{marginLeft:8,fontSize:11}}>🕐 {fmt(fi.detected_at)}</span>}
                      </div>
                    )}
                    {fi?.message && (
                      <p style={{fontSize:11,color:'var(--text-muted)',lineHeight:1.5,margin:'0 0 4px',
                        fontStyle:'italic'}}>{fi.message}</p>
                    )}
                    {resp.explanation && (
                      <p style={{fontSize:12,color:'var(--text-muted)',lineHeight:1.5,margin:0}}>
                        {resp.explanation}
                      </p>
                    )}
                  </div>
                  <div style={{textAlign:'right',whiteSpace:'nowrap',flexShrink:0}}>
                    <div style={{fontSize:11,color:'var(--text-muted)'}}>{fmt(a.created_at)}</div>
                    {a.latency_ms && <div style={{fontSize:10,color:'var(--text-muted)',marginTop:2}}>{(a.latency_ms/1000).toFixed(1)}s</div>}
                    <div style={{fontSize:11,color:'var(--text-muted)',marginTop:4}}>{isExp ? '▲ less' : '▼ more'}</div>
                  </div>
                </div>
                {isExp && renderDetail(resp)}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
