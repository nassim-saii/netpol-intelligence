import { useState, useEffect } from 'react'
import ComplianceReport from './ComplianceReport'
import LLMInsightPanel from './LLMInsightPanel'
import PolicyGraph from './PolicyGraph'
import Chat from './Chat'
import GrafanaPanel from './GrafanaPanel'
import Documentation from './Documentation'

// ── API fetch with auth key ──────────────────────────────────────────────────
const API_KEY = "netpol-demo-2026"
const apiFetch = (url, opts = {}) =>
  fetch(url, { ...opts, headers: { ...(opts.headers || {}), "X-API-Key": API_KEY } })

const TABS = [
  { id: 'overview',   label: '📊 Overview' },
  { id: 'llm',        label: '🔍 Insights' },
  { id: 'compliance', label: '✅ Compliance' },
  { id: 'grafana',    label: '📈 Grafana' },
  { id: 'docs',       label: '📖 Docs' },
  { id: 'chat',       label: '💬 Assistant' },
]

function StatCard({ label, value, sub, color, onClick }) {
  return (<div className='card' onClick={onClick}
    style={{ textAlign:'center', minWidth:130, cursor: onClick?'pointer':'default',
      transition:'transform 0.15s, box-shadow 0.15s' }}
    onMouseEnter={e => { if(onClick){ e.currentTarget.style.transform='translateY(-2px)';
      e.currentTarget.style.boxShadow='0 4px 12px rgba(0,0,0,0.3)' }}}
    onMouseLeave={e => { if(onClick){ e.currentTarget.style.transform='';
      e.currentTarget.style.boxShadow='' }}}>
    <div style={{ fontSize:26, fontWeight:700, color: color||'var(--accent)' }}>{value??'—'}</div>
    <div style={{ fontSize:11, fontWeight:600, color:'var(--text-muted)', marginTop:4 }}>{label}</div>
    {sub && <div style={{ fontSize:10, color:'var(--text-muted)', marginTop:2 }}>{sub}</div>}
  </div>)
}

function Overview({ stats, compliance, onTabChange , onAnomaliesClick}) {
  const score = compliance?.score ?? null
  const scoreColor = score >= 90 ? 'var(--low)' : score >= 60 ? '#d29922' : 'var(--critical)'
  return (<div>
    {/* ═══ STAT CARDS ═══ */}
    <div style={{ display:'flex', gap:12, flexWrap:'wrap', marginBottom:20 }}>
      <StatCard label='COMPLIANCE SCORE' value={score !== null ? score+'%' : '—'}
        sub={compliance?.grade ? 'Grade '+compliance.grade : null} color={scoreColor}
        onClick={() => onTabChange?.('compliance')} />
      <StatCard label='FINDINGS 24H' value={stats?.findings_24h}
        color={stats?.findings_24h > 0 ? 'var(--high)' : 'var(--low)'}
        onClick={() => onTabChange?.('compliance')} />
      <StatCard label='ANOMALIES 24H' value={stats?.anomalies_24h}
        color={stats?.anomalies_24h > 0 ? '#d29922' : 'var(--low)'}
        onClick={() => onAnomaliesClick ? onAnomaliesClick() : onTabChange?.('llm')} />
      <StatCard label='LLM ANALYSES 24H' value={stats?.llm_analyses_24h}
        onClick={() => onTabChange?.('llm')} />
      <StatCard label='FLOW DROPS 1H' value={stats?.flow_drops_1h} onClick={() => onTabChange?.('grafana')}
        color={stats?.flow_drops_1h > 0 ? 'var(--high)' : 'var(--low)'} />
    </div>

    {/* ═══ VIOLATION BREAKDOWN ═══ */}
    {compliance && (<div className='card' style={{ marginBottom:16 }}>
      <div style={{ fontWeight:600, marginBottom:12 }}>Violation Breakdown — Last 24h</div>
      <div style={{ display:'flex', gap:20, flexWrap:'wrap' }}>
        {[['CRITICAL',compliance.critical],['HIGH',compliance.high],
          ['MEDIUM',compliance.medium],['LOW',compliance.low]].map(([sev,cnt]) => (
          <div key={sev} style={{ display:'flex', alignItems:'center', gap:8 }}>
            <span className={'badge '+sev}>{sev}</span>
            <span style={{ fontWeight:700, fontSize:20 }}>{cnt}</span>
          </div>
        ))}
      </div>
    </div>)}

    {/* ═══ NAMESPACE MAP + FLOW BASELINES (was Policy Graph tab) ═══ */}
    <div style={{ marginBottom:16 }}>
      <PolicyGraph />
    </div>

    {/* ═══ QUICK LINKS ═══ */}
    <div className='card'>
      <div style={{ fontWeight:600, marginBottom:8 }}>Quick Links</div>
      <div style={{ display:'flex', gap:10, flexWrap:'wrap' }}>
        {[['📈 Grafana','http://grafana.apps.lab.ocp.lan','var(--accent)'],
          ['🔧 OpenShift Console','https://console-openshift-console.apps.lab.ocp.lan','var(--low)'],
          ['🛒 Online Boutique','http://boutique.apps.lab.ocp.lan','#d29922'],
        ].map(([label,href,color]) => (
          <a key={href} href={href} target='_blank' rel='noreferrer' style={{
            padding:'6px 14px', background:'rgba(88,166,255,.08)',
            border:'1px solid rgba(88,166,255,.25)', borderRadius:6, color, fontSize:13
          }}>{label}</a>
        ))}
      </div>
    </div>
  </div>)
}

export default function App() {
  const [tab, setTab] = useState('overview')
  const [insightFilter, setInsightFilter] = useState(null)
  const [chatMessages, setChatMessages] = useState([{ role:'assistant', content:'👋 Hi! I am the NetPol Intelligence assistant. I have full visibility into your cluster security posture — ask me anything about your NetworkPolicy violations, anomalies, or what to fix first.' }])
  const [stats, setStats] = useState(null)
  const [compliance, setCompliance] = useState(null)
  const [lastRefresh, setLastRefresh] = useState(new Date())

  const fetchHeader = async () => {
    try {
      const [s,c] = await Promise.all([
        apiFetch('/api/stats').then(r=>r.json()),
        apiFetch('/api/compliance').then(r=>r.json()),
      ])
      setStats(s); setCompliance(c); setLastRefresh(new Date())
    } catch(e) { console.error('fetch failed',e) }
  }

  useEffect(() => {
    fetchHeader()
    const id = setInterval(fetchHeader, 30000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{ minHeight:'100vh', display:'flex', flexDirection:'column' }}>
      <header style={{ background:'var(--bg-card)', borderBottom:'1px solid var(--border)',
        padding:'0 24px', height:52, display:'flex', alignItems:'center', gap:16 }}>
        <span style={{ fontSize:17, fontWeight:700, color:'var(--accent)' }}>🛡️ NetPol Intelligence</span>
        <span style={{ color:'var(--text-muted)', fontSize:12 }}>lab.ocp.lan · OCP 4.21.8 · OVN-K</span>
        <div style={{ marginLeft:'auto', fontSize:11, color:'var(--text-muted)' }}>
          {lastRefresh.toLocaleTimeString()}
          <button onClick={fetchHeader} style={{ marginLeft:8, background:'none',
            border:'1px solid var(--border)', color:'var(--text-muted)', borderRadius:4,
            padding:'2px 8px', cursor:'pointer', fontSize:11 }}>↺</button>
        </div>
      </header>
      <nav style={{ background:'var(--bg-card)', borderBottom:'1px solid var(--border)',
        padding:'0 24px', display:'flex', gap:2 }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            background:'none', border:'none', cursor:'pointer', padding:'10px 14px',
            fontSize:13, fontWeight:500,
            color: tab===t.id ? 'var(--accent)' : 'var(--text-muted)',
            borderBottom: tab===t.id ? '2px solid var(--accent)' : '2px solid transparent',
            marginBottom:-1 }}>
            {t.label}
          </button>
        ))}
      </nav>
      <main style={{ flex:1, padding: tab==='grafana' ? 12 : 24, paddingBottom: tab==='chat' ? 120 : 60, maxWidth: tab==='grafana' ? '100%' : 1400, width:'100%', margin:'0 auto' }}>
        {tab==='overview'   && <Overview onTabChange={setTab} onAnomaliesClick={() => { setInsightFilter('anomalies'); setTab('llm') }} stats={stats} compliance={compliance} />}
        {tab==='llm'        && <LLMInsightPanel initialFilter={insightFilter} onFilterApplied={() => setInsightFilter(null)} />}
        {tab==='compliance' && <ComplianceReport />}
        {tab==='grafana'    && <GrafanaPanel />}
        {tab==='docs'       && <Documentation />}
        {tab==='chat'       && <Chat messages={chatMessages} setMessages={setChatMessages} />}
      </main>
      <footer style={{ position:'fixed', bottom:0, left:0, right:0, zIndex:100,
        borderTop:'1px solid var(--border)', padding:'8px 24px',
        background:'var(--bg-card)',
        color:'var(--text-muted)', fontSize:11, display:'flex', justifyContent:'space-between' }}>
        <span>NetworkPolicy Intelligence System · Phase 4 · April 2026</span>
        <span>netpol-system · llama3.2:3b</span>
      </footer>
    </div>
  )
}
