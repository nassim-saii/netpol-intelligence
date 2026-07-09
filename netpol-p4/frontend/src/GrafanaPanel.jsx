import { useState } from 'react'

const DASHBOARDS = [
  {
    id: 'flows',
    label: '🌊 OVN ACL Flows',
    url: 'https://grafana.apps.lab.ocp.lan/d/f94a8027-8b4d-40a3-b6d9-1c208d9b8ae3/ovn-acl-flow-monitor?kiosk&refresh=30s&from=now-15m&to=now',
  },
  {
    id: 'cluster',
    label: '🖥️ Cluster Health',
    url: 'https://grafana.apps.lab.ocp.lan/d/7555ef38-9893-4710-908b-aa2310cb9a0a/ovn-metrics-2b-cluster-health?kiosk&refresh=30s&from=now-30m&to=now',
  },
  {
    id: 'violations',
    label: '🚨 NP Violations',
    url: 'https://grafana.apps.lab.ocp.lan/d/netpol-violations-flows/networkpolicy-violations-flow-analysis?kiosk&refresh=60s&from=now-6h&to=now',
  },
]

export default function GrafanaPanel() {
  const [active, setActive] = useState('flows')
  const current = DASHBOARDS.find(d => d.id === active)

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'calc(100vh - 160px)', width:'100%' }}>
      <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:12 }}>
        <h2 style={{ fontWeight:700, fontSize:18, margin:0 }}>📈 Grafana</h2>
        <div style={{ display:'flex', gap:4 }}>
          {DASHBOARDS.map(d => (
            <button key={d.id} onClick={() => setActive(d.id)}
              style={{
                padding:'6px 14px', borderRadius:6, border:'1px solid var(--border)',
                background: active===d.id ? 'var(--accent)' : 'var(--bg-card)',
                color: active===d.id ? '#000' : 'var(--text)',
                fontWeight: active===d.id ? 700 : 400,
                fontSize:12, cursor:'pointer', transition:'all 0.15s'
              }}>
              {d.label}
            </button>
          ))}
        </div>
        <a href={current.url.replace('?kiosk','').replace('&kiosk','')}
          target='_blank' rel='noreferrer'
          style={{ marginLeft:'auto', fontSize:12, color:'var(--accent)', textDecoration:'none' }}>
          ↗ Open in Grafana
        </a>
      </div>
      <div style={{ flex:1, borderRadius:8, overflow:'hidden', border:'1px solid var(--border)' }}>
        <iframe
          key={active}
          src={current.url}
          width='100%'
          height='100%'
          frameBorder='0'
          style={{ display:'block' }}
        />
      </div>
    </div>
  )
}
