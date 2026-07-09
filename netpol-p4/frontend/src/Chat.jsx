import { apiFetch } from './api.js'
import { useState, useRef, useEffect } from 'react'

const STORAGE_KEY = 'netpol_chat_history'
const TTL_MS = 24 * 60 * 60 * 1000

const SUGGESTIONS = [
  "Why is my compliance score low?",
  "What is the most critical issue right now?",
  "Explain the NP-003 violation",
  "Is the online-boutique namespace safe?",
  "What should I fix first?",
  "How to fix allow-intra-namespace policy?",
  "What DevSecOps practices should we adopt?",
  "How to secure the CI/CD pipeline for this cluster?",
]

const INITIAL_MSG = { role:'assistant', content:'👋 Hi! I am the NetPol Intelligence assistant. I have full visibility into your cluster security posture — ask me anything about NetworkPolicy violations, anomalies, DevSecOps best practices, or what to fix first.' }

function loadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const { messages, savedAt } = JSON.parse(raw)
    if (Date.now() - savedAt > TTL_MS) { localStorage.removeItem(STORAGE_KEY); return null }
    return messages
  } catch { return null }
}

function saveHistory(messages) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ messages, savedAt: Date.now() })) } catch {}
}

function Message({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div style={{ display:'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom:12 }}>
      <div style={{
        maxWidth:'75%', padding:'10px 14px',
        borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
        background: isUser ? 'rgba(88,166,255,.2)' : 'var(--bg-card2)',
        border: '1px solid ' + (isUser ? 'rgba(88,166,255,.4)' : 'var(--border)'),
        fontSize:13, lineHeight:1.6, whiteSpace:'pre-wrap'
      }}>
        {msg.content}
        {msg.context && (
          <div style={{ marginTop:8, paddingTop:8, borderTop:'1px solid var(--border)',
            fontSize:11, color:'var(--text-muted)', display:'flex', gap:12, flexWrap:'wrap' }}>
            <span>Score: <b style={{ color: msg.context.score >= 60 ? 'var(--low)' : 'var(--critical)' }}>{msg.context.score}%</b></span>
            <span>Critical: <b style={{ color:'var(--critical)' }}>{msg.context.critical}</b></span>
            <span>High: <b style={{ color:'var(--high)' }}>{msg.context.high}</b></span>
            {msg.responseTime && !msg.thinking && <span style={{marginLeft:'auto',opacity:0.8,fontSize:'0.75rem',color:'var(--text-muted)'}}>⏱ {msg.responseTime}</span>}
          </div>
        )}
        {msg.stopped && (
          <div style={{ marginTop:6, fontSize:11, color:'var(--text-muted)', fontStyle:'italic' }}>
            ⏹ Generation stopped by user
          </div>
        )}
      </div>
    </div>
  )
}

export default function Chat() {
  const [messages, setMessages] = useState(() => loadHistory() || [INITIAL_MSG])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef(null)
  const startTimeRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => { saveHistory(messages) }, [messages])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }) }, [messages])

  const clearChat = () => { localStorage.removeItem(STORAGE_KEY); setMessages([INITIAL_MSG]) }

  const stopGeneration = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setStreaming(false)
    setLoading(false)
    // Mark the last message as stopped
    setMessages(prev => {
      const m = [...prev]
      if (m.length > 0 && m[m.length - 1].role === 'assistant') {
        m[m.length - 1] = { ...m[m.length - 1], stopped: true }
        // Remove "Thinking..." placeholder if it never got real content
        if (m[m.length - 1].thinking) {
          m[m.length - 1] = { role: 'assistant', content: '⏹ Generation stopped.', stopped: true }
        }
      }
      return m
    })
  }

  const send = async (text) => {
    const question = (text || input).trim()
    if (!question || loading || streaming) return
    setInput('')

    // Cancel any previous stream
    if (abortRef.current) { abortRef.current.abort() }
    const controller = new AbortController()
    abortRef.current = controller

    const updatedMsgs = [...messages, { role:'user', content: question }]
    setMessages(updatedMsgs)
    setLoading(true)
    try {
      startTimeRef.current = Date.now();
      const r = await apiFetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type':'application/json' },
        body: JSON.stringify({ message: question, history: updatedMsgs.slice(-6) }),
        signal: controller.signal
      })
      if (!r.ok) { setMessages(prev => [...prev, { role:'assistant', responseTime: ((Date.now()-(startTimeRef.current||Date.now()))/1000).toFixed(1)+'s', content: 'Error: ' + r.status }]); setLoading(false); return }
      const reader = r.body.getReader()
      const decoder = new TextDecoder()
      let ctx = null; let started = false
      setMessages(prev => [...prev, { role:'assistant', content: '🤔 Thinking...', context: null, thinking: true }])
      setLoading(false)
      setStreaming(true)
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) {
            const elapsed = ((Date.now()-(startTimeRef.current||Date.now()))/1000).toFixed(1)+'s';
            setMessages(prev => { const m=[...prev]; m[m.length-1]={...m[m.length-1],responseTime:elapsed}; return m });
            break;
          }
          // Check if aborted
          if (controller.signal.aborted) break
          const lines = decoder.decode(value).split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const d = JSON.parse(line.slice(6))
              if (d.score !== undefined) { ctx = d }
              else if (d.token) {
                if (!started) {
                  started = true
                  setMessages(prev => { const m=[...prev]; m[m.length-1]={role:'assistant',content:d.token,context:ctx}; return m })
                } else {
                  setMessages(prev => { const m=[...prev]; m[m.length-1]={...m[m.length-1],content:m[m.length-1].content+d.token}; return m })
                }
              } else if (d.error) {
                setMessages(prev => { const m=[...prev]; m[m.length-1]={role:'assistant', responseTime: ((Date.now()-(startTimeRef.current||Date.now()))/1000).toFixed(1)+'s',content:'LLM error: '+d.error}; return m })
              } else if (d.done) {
                const elapsed = ((Date.now()-(startTimeRef.current||Date.now()))/1000).toFixed(1)+'s';
                setMessages(prev => { const m=[...prev]; m[m.length-1]={...m[m.length-1],responseTime:elapsed}; return m });
                break
              }
            } catch(e) {}
          }
        }
      } catch(streamErr) {
        // AbortError is expected when user clicks Stop
        if (streamErr.name !== 'AbortError') {
          if (!started) setMessages(prev => { const m=[...prev]; m[m.length-1]={role:'assistant', responseTime: ((Date.now()-(startTimeRef.current||Date.now()))/1000).toFixed(1)+'s',content:'Stream error. Try again.'}; return m })
        }
      }
      setStreaming(false)
      abortRef.current = null
    } catch(e) {
      if (e.name !== 'AbortError') {
        setMessages(prev => [...prev, { role:'assistant', responseTime: ((Date.now()-(startTimeRef.current||Date.now()))/1000).toFixed(1)+'s', content: 'Connection error.' }])
      }
      setLoading(false)
      setStreaming(false)
      abortRef.current = null
    }
  }

  const handleKey = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }

  return (
    <div style={{ maxWidth:900, margin:'0 auto' }}>
      {/* Header */}
      <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:16, flexShrink:0 }}>
        <h2 style={{ fontWeight:700, fontSize:18 }}>🤖 Security Assistant</h2>
        <span style={{ fontSize:12, color:'var(--text-muted)' }}>llama3.2:3b · live cluster context</span>
        <div style={{ marginLeft:'auto', display:'flex', gap:8 }}>
          {streaming && (
            <button onClick={stopGeneration}
              style={{ background:'rgba(239,68,68,.15)', border:'1px solid rgba(239,68,68,.5)',
                color:'#ef4444', borderRadius:6, padding:'4px 12px',
                cursor:'pointer', fontSize:12, fontWeight:600,
                display:'flex', alignItems:'center', gap:4,
                animation:'pulse-border 1.5s ease-in-out infinite' }}>
              <span style={{ fontSize:14 }}>⏹</span> Stop
            </button>
          )}
          <button onClick={clearChat}
            style={{ background:'none', border:'1px solid var(--border)',
              color:'var(--text-muted)', borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:12 }}>
            Clear
          </button>
        </div>
      </div>

      {/* Suggestions */}
      {messages.length <= 1 && (
        <div style={{ display:'flex', gap:8, flexWrap:'wrap', marginBottom:16, flexShrink:0 }}>
          {SUGGESTIONS.map((s,i) => (
            <button key={i} onClick={() => send(s)} style={{
              background:'rgba(88,166,255,.08)', border:'1px solid rgba(88,166,255,.25)',
              color:'var(--accent)', borderRadius:20, padding:'6px 12px',
              cursor:'pointer', fontSize:12 }}>
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Messages — scrollable middle */}
      <div style={{ padding:'4px 0', paddingBottom:120 }}>
        {messages.map((m,i) => <Message key={i} msg={m} />)}
        {loading && (
          <div style={{ display:'flex', justifyContent:'flex-start', marginBottom:12 }}>
            <div style={{ padding:'10px 14px', borderRadius:'16px 16px 16px 4px',
              background:'var(--bg-card2)', border:'1px solid var(--border)', fontSize:13, color:'var(--text-muted)' }}>
              🤖 Thinking
              <span style={{ display:'inline-block', width:12, height:12,
                border:'2px solid var(--border)', borderTopColor:'var(--accent)',
                borderRadius:'50%', animation:'spin .8s linear infinite', marginLeft:8, verticalAlign:'middle' }} />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input — pinned at bottom */}
      <div style={{ position:'fixed', bottom:36, left:'50%', transform:'translateX(-50%)',
        width:'min(900px, calc(100% - 48px))',
        background:'var(--bg-card)', borderTop:'1px solid var(--border)',
        borderRadius:'8px 8px 0 0', padding:'12px 12px 8px', zIndex:50 }}>
        <div style={{ display:'flex', gap:8 }}>
          <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey}
            placeholder='Ask about cluster security, DevSecOps, violations... (Enter to send)'
            rows={2} style={{
              flex:1, background:'var(--bg-card)', border:'1px solid var(--border)',
              color:'var(--text)', borderRadius:8, padding:'10px 12px',
              fontSize:13, resize:'none', fontFamily:'inherit', outline:'none'
            }} />
          {streaming ? (
            <button onClick={stopGeneration} style={{
              background:'rgba(239,68,68,.15)', border:'1px solid rgba(239,68,68,.5)',
              color:'#ef4444', borderRadius:8, padding:'0 20px',
              cursor:'pointer', fontWeight:600, fontSize:13,
              display:'flex', alignItems:'center', gap:4
            }}>⏹ Stop</button>
          ) : (
            <button onClick={() => send()} disabled={!input.trim() || loading} style={{
              background: input.trim() && !loading ? 'var(--accent)' : 'var(--border)',
              color: input.trim() && !loading ? '#000' : 'var(--text-muted)',
              border:'none', borderRadius:8, padding:'0 20px',
              cursor: input.trim() && !loading ? 'pointer' : 'not-allowed',
              fontWeight:600, fontSize:13, transition:'all .15s'
            }}>Send</button>
          )}
        </div>
      </div>

      {/* Pulse animation for stop button */}
      <style>{`
        @keyframes pulse-border {
          0%, 100% { border-color: rgba(239,68,68,.5); }
          50% { border-color: rgba(239,68,68,.9); }
        }
      `}</style>
    </div>
  )
}
