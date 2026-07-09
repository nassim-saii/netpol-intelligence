import { apiFetch } from './api.js'
import { useEffect, useState } from 'react'

const NS_ICONS = {'online-boutique':'🛒','netpol-system':'🛡️','llm-system':'🧠'}

const sevColor = s => ({CRITICAL:'var(--critical)',HIGH:'var(--high)',MEDIUM:'#d29922',LOW:'var(--low)'}[s]||'var(--text-muted)')
const sevBg = s => ({CRITICAL:'rgba(248,81,73,0.12)',HIGH:'rgba(248,81,73,0.08)',MEDIUM:'rgba(210,153,34,0.12)',LOW:'rgba(63,185,80,0.12)'}[s]||'transparent')

function HealthBadge({active}){
  const c=active.filter(f=>f.severity==='CRITICAL').length
  const h=active.filter(f=>f.severity==='HIGH').length
  if(c>0) return <span style={pill('#f85149','rgba(248,81,73,0.12)')}>🔴 Critical</span>
  if(h>0) return <span style={pill('#ff7b72','rgba(248,81,73,0.08)')}>🟠 At risk</span>
  if(active.length>0) return <span style={pill('#d29922','rgba(210,153,34,0.12)')}>🟡 Warning</span>
  return <span style={pill('#3fb950','rgba(63,185,80,0.12)')}>🟢 Healthy</span>
}

function pill(color,bg){
  return {fontSize:12,padding:'3px 12px',borderRadius:12,background:bg,color,fontWeight:600,border:'1px solid '+color,whiteSpace:'nowrap'}
}

function FlowBar({rate,maxRate}){
  const pct=Math.min((rate/maxRate)*100,100)
  const color=rate>150?'#f85149':rate>100?'#d29922':'#3fb950'
  return <div style={{flex:1,height:7,background:'rgba(255,255,255,0.06)',borderRadius:4,overflow:'hidden'}}>
    <div style={{width:pct+'%',height:'100%',borderRadius:4,background:color,transition:'width 0.3s'}}/>
  </div>
}

export default function PolicyGraph(){
  const [graph,setGraph]=useState(null)
  const [baseline,setBaseline]=useState([])
  const [findings,setFindings]=useState([])
  const [pods,setPods]=useState({})
  const [netpols,setNetpols]=useState({})
  const [loading,setLoading]=useState(true)
  const [selected,setSelected]=useState(null)
  const [expandedPods,setExpandedPods]=useState({})

  useEffect(()=>{
    Promise.all([
      apiFetch('/api/policy-graph').then(r=>r.json()),
      apiFetch('/api/flow-baseline').then(r=>r.json()),
      apiFetch('/api/findings?hours=168&limit=200&include_resolved=true').then(r=>r.json()).catch(()=>({findings:[]})),
      apiFetch('/api/pods').then(r=>r.json()).catch(()=>({namespaces:{}})),
      apiFetch('/api/network-policies').then(r=>r.json()).catch(()=>({namespaces:{}})),
    ]).then(([g,b,f,p,np])=>{
      setGraph(g); setBaseline(b.baseline||[]); setFindings(f.findings||[])
      setPods(p.namespaces||{}); setNetpols(np.namespaces||{})
      if(g.nodes?.length) setSelected(g.nodes[0])
      setLoading(false)
    }).catch(e=>{console.error(e);setLoading(false)})
  }, [])

  useEffect(() => {
    const id = setInterval(() => {
      apiFetch('/api/flow-baseline').then(r=>r.json())
        .then(b => setBaseline(b.baseline||[]))
        .catch(e => console.error('baseline refresh:', e))
    }, 15 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  // Refresh flow baseline every 15 minutes
  useEffect(() => {
    const id = setInterval(() => {
      apiFetch('/api/flow-baseline').then(r=>r.json()).then(b=>setBaseline(b.baseline||[]))
    }, 15 * 60 * 1000)
    return () => clearInterval(id)
  },[])

  if(loading) return <div className='spinner'/>
  if(!graph?.nodes?.length) return (
    <div className='card' style={{textAlign:'center',padding:40,color:'var(--text-muted)'}}>
      <div style={{fontSize:32,marginBottom:12}}>🕸️</div>
      No namespace data yet. Audit findings and flow events generate the graph.
    </div>
  )

  const {nodes,edges}=graph
  const maxRate=Math.max(...baseline.map(b=>b.mean_rate),1)
  const normalBaselines=baseline.filter(b=>!b.flow_key.includes('|drop')).slice(0,8)
  const dropBaselines=baseline.filter(b=>b.flow_key.includes('|drop'))
  const dropLookup={}
  dropBaselines.forEach(d=>{dropLookup[d.flow_key.replace('|drop','')]=d})

  const nsFindingsActive=ns=>findings.filter(f=>f.namespace===ns&&!f.resolved)
  const nsEdgeCount=ns=>edges.filter(e=>{
    const s=typeof e.source==='object'?e.source.id:e.source
    const t=typeof e.target==='object'?e.target.id:e.target
    return s===ns||t===ns
  }).length

  const cardBorder=(node,isSel,activeCount)=>{
    if(isSel) return '2px solid var(--accent)'
    if(activeCount>0){
      const hasCrit=nsFindingsActive(node.id).some(f=>f.severity==='CRITICAL')
      return hasCrit?'2px solid #f85149':'2px solid #d29922'
    }
    return '1px solid var(--border)'
  }

  return (
    <div>
      <div style={{display:'flex',alignItems:'center',gap:16,marginBottom:20}}>
        <h2 style={{fontWeight:700,fontSize:18}}>Namespace Map</h2>
        <span style={{fontSize:13,color:'var(--text-muted)'}}>
          {nodes.length} namespaces · {edges.length} flow edges · click for details
        </span>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'repeat('+Math.min(nodes.length,3)+', 1fr)',gap:14,marginBottom:18}}>
        {nodes.map(node=>{
          const icon=NS_ICONS[node.id]||'📦'
          const isSel=selected?.id===node.id
          const active=nsFindingsActive(node.id)
          const nsPods=pods[node.id]||[]
          const nsNps=netpols[node.id]||[]
          const readyPods=nsPods.filter(p=>p.ready)
          return (
            <div key={node.id} className='card' onClick={()=>setSelected(node)}
              style={{cursor:'pointer',transition:'all 0.15s',
                border:cardBorder(node,isSel,active.length),
                background:isSel?'rgba(88,166,255,0.04)':'var(--bg-card)',
                padding:'16px 18px',display:'flex',flexDirection:'column'}}>
              <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:10}}>
                <span style={{fontSize:22}}>{icon}</span>
                <span style={{fontWeight:700,fontSize:16,flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{node.id}</span>
                <HealthBadge active={active}/>
              </div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:14}}>
                {[
                  ['Pods',readyPods.length+'/'+nsPods.length,nsPods.length>0&&readyPods.length<nsPods.length?'#d29922':null],
                  ['Flows',nsEdgeCount(node.id),null],
                  ['Active violations',active.length,active.length>0?'#f85149':'#3fb950'],
                  ['Policies',nsNps.length,null],
                ].map(([label,val,color])=>(
                  <div key={label} style={{background:'rgba(255,255,255,0.04)',borderRadius:8,padding:'10px 12px'}}>
                    <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:3}}>{label}</div>
                    <div style={{fontSize:22,fontWeight:700,color:color||'var(--text)'}}>{val}</div>
                  </div>
                ))}
              </div>
              <div style={{marginTop:'auto'}}/>
              <div style={{display:'flex',flexWrap:'wrap',gap:5}}>
                {(expandedPods[node.id]?nsPods:nsPods.slice(0,8)).map(p=>(
                  <span key={p.name} style={{fontSize:11,padding:'3px 10px',borderRadius:10,
                    background:p.ready?'rgba(255,255,255,0.06)':'rgba(248,81,73,0.12)',
                    color:p.ready?'var(--text-muted)':'#f85149'}}
                    title={p.name+' — '+p.status+' — '+p.node}>
                    {p.name.replace(/-[a-z0-9]{8,10}-[a-z0-9]{5}$/,'').replace(/-[a-f0-9]{8,}$/,'')}
                  </span>
                ))}
                {nsPods.length>8&&(
                  <span onClick={e=>{e.stopPropagation();setExpandedPods(prev=>({...prev,[node.id]:!prev[node.id]}))}}
                    style={{fontSize:11,padding:'3px 10px',borderRadius:10,cursor:'pointer',
                    background:'rgba(88,166,255,0.1)',color:'var(--accent)',fontWeight:600}}>
                    {expandedPods[node.id]?'▲ less':'+'+( nsPods.length-8)+' more'}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div style={{display:'flex',gap:14,alignItems:'flex-start'}}>
        <div className='card' style={{flex:1,minWidth:0,padding:'16px 18px'}}>
          <div style={{fontWeight:700,marginBottom:6,display:'flex',alignItems:'center',gap:10,fontSize:16}}>
            🔀 Flow baselines
            <span style={{fontSize:12,color:'var(--text-muted)',fontWeight:400}}>top by mean rate</span>
          </div>
          <div style={{display:'flex',gap:14,fontSize:11,color:'var(--text-muted)',marginBottom:12}}>
            <span><span style={{display:'inline-block',width:9,height:9,borderRadius:'50%',background:'#3fb950',marginRight:4,verticalAlign:'middle'}}/>{'< 100/min'}</span>
            <span><span style={{display:'inline-block',width:9,height:9,borderRadius:'50%',background:'#d29922',marginRight:4,verticalAlign:'middle'}}/>100–150</span>
            <span><span style={{display:'inline-block',width:9,height:9,borderRadius:'50%',background:'#f85149',marginRight:4,verticalAlign:'middle'}}/>{'>'}150</span>
            <span style={{background:'rgba(248,81,73,0.12)',color:'#f85149',padding:'1px 8px',borderRadius:8,fontWeight:600,fontSize:10}}>drops/min</span>
          </div>
          {normalBaselines.length===0?(
            <div style={{fontSize:13,color:'var(--text-muted)',padding:20,textAlign:'center'}}>No baseline data yet</div>
          ):normalBaselines.map((b,i)=>{
            const [src,dst,port]=b.flow_key.split('|')
            const drop=dropLookup[b.flow_key]
            return (
              <div key={i} style={{display:'flex',alignItems:'center',gap:8,padding:'7px 0',
                borderBottom:i<normalBaselines.length-1?'1px solid var(--border)':'none'}}>
                <span className='mono' style={{fontSize:11,color:'var(--accent)',minWidth:75,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{src}</span>
                <span style={{color:'var(--text-muted)',fontSize:11}}>→</span>
                <span className='mono' style={{fontSize:11,color:'var(--accent)',minWidth:75,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{dst}</span>
                <span className='mono' style={{fontSize:11,background:'rgba(255,255,255,0.06)',padding:'2px 7px',borderRadius:5}}>:{port}</span>
                <FlowBar rate={b.mean_rate} maxRate={maxRate}/>
                <span style={{fontSize:11,color:'var(--text-muted)',minWidth:60,textAlign:'right'}}>{b.mean_rate.toFixed(1)}/m</span>
                {drop?(<span style={{fontSize:10,padding:'2px 7px',borderRadius:8,background:'rgba(248,81,73,0.12)',color:'#f85149',fontWeight:600,whiteSpace:'nowrap'}}>
                  {drop.mean_rate.toFixed(1)}</span>):<span style={{minWidth:35}}/>}
              </div>
            )
          })}
        </div>

        <div className='card' style={{width:300,flexShrink:0,padding:'16px 18px'}}>
          {selected?(<>
            <div style={{fontWeight:700,marginBottom:14,display:'flex',alignItems:'center',gap:10,fontSize:16}}>
              <span style={{fontSize:22}}>{NS_ICONS[selected.id]||'📦'}</span>
              {selected.id}
            </div>
            <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:6,fontWeight:700}}>
              ACTIVE VIOLATIONS ({nsFindingsActive(selected.id).length})
            </div>
            {nsFindingsActive(selected.id).length===0?(
              <div style={{fontSize:13,color:'var(--text-muted)',padding:'8px 0',marginBottom:12}}>No active violations 🎉</div>
            ):(
              <div style={{marginBottom:12}}>
                {nsFindingsActive(selected.id).map((f,i)=>(
                  <div key={i} style={{display:'flex',alignItems:'center',gap:6,padding:'6px 0',borderBottom:'1px solid var(--border)',fontSize:12}}>
                    <span style={{fontSize:10,padding:'2px 8px',borderRadius:8,background:sevBg(f.severity),color:sevColor(f.severity),fontWeight:600,whiteSpace:'nowrap'}}>{f.severity}</span>
                    <span className='mono' style={{fontSize:11,whiteSpace:'nowrap'}}>{f.rule_id}</span>
                    <span style={{color:'var(--text-muted)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}} title={f.message}>{f.policy_name}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{fontSize:12,color:'var(--text-muted)',marginBottom:6,fontWeight:700}}>
              NETWORK POLICIES ({(netpols[selected.id]||[]).length})
            </div>
            {(netpols[selected.id]||[]).length===0?(
              <div style={{fontSize:13,color:'var(--text-muted)',padding:'8px 0'}}>No policies</div>
            ):(
              <div>
                {(netpols[selected.id]||[]).map((np,i)=>(
                  <div key={i} style={{display:'flex',alignItems:'center',gap:6,padding:'5px 0',borderBottom:'1px solid var(--border)',fontSize:11}}>
                    <span style={{fontSize:10,padding:'2px 6px',borderRadius:6,background:'rgba(88,166,255,0.08)',color:'var(--accent)',fontWeight:600,whiteSpace:'nowrap'}}>
                      {np.policy_types.join('+')||'—'}
                    </span>
                    <span className='mono' style={{fontSize:11,flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                      title={JSON.stringify(np.pod_selector)}>{np.name}</span>
                    <span style={{fontSize:9,color:'var(--text-muted)'}}>
                      {np.ingress_rules>0?'I:'+np.ingress_rules+' ':''}{np.egress_rules>0?'E:'+np.egress_rules:''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>):(
            <div style={{fontSize:14,color:'var(--text-muted)',textAlign:'center',padding:24}}>
              Click a namespace card for details
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
