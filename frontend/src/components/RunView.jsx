import React, { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { abortRun, approveBash, getRun } from '../api'
import useWebSocket from '../hooks/useWebSocket'
import { useTasksContext } from '../TasksContext'

export default function RunView() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const { tasks } = useTasksContext()

  const [run, setRun] = useState(null)
  const [initialEvents, setInitialEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [aborting, setAborting] = useState(false)
  const [elapsed, setElapsed] = useState('0s')
  const [expandedEvents, setExpandedEvents] = useState(new Set())
  const [selectedFile, setSelectedFile] = useState(null)
  const [fileDiffs, setFileDiffs] = useState({})
  const [bashApproval, setBashApproval] = useState(null)

  const feedBottomRef = useRef(null)

  // Load run + historical events
  useEffect(() => {
    getRun(runId)
      .then((data) => {
        setRun(data)
        setInitialEvents(data.events || [])
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [runId])

  // Live WebSocket events (only when run is still running)
  const isRunning = run?.status === 'running'
  const { events: liveEvents, connected } = useWebSocket(runId, { enabled: isRunning })

  // After run completes via WS, refresh run record
  useEffect(() => {
    const doneEvent = liveEvents.find((e) => e.type === 'done')
    if (doneEvent) {
      getRun(runId).then((data) => {
        setRun(data)
        setInitialEvents(data.events || [])
      }).catch(() => {})
    }
  }, [liveEvents, runId])

  // Combine: show initialEvents + live events not already in initial set
  const initialIds = new Set(initialEvents.map((e) => e.id))
  const dedupedLive = liveEvents.filter((e) => e.id == null || !initialIds.has(e.id))
  const allEvents = [...initialEvents, ...dedupedLive]

  // Build file diff map: path → diff string from write_file tool_result events
  useEffect(() => {
    const map = {}
    for (let i = 0; i < allEvents.length; i++) {
      const e = allEvents[i]
      const content = e.content != null && typeof e.content === 'object' ? e.content : e
      if (content?.type === 'tool_call' && content?.name === 'write_file') {
        const path = content?.input?.path
        if (!path) continue
        // find the next tool_result for write_file after this index
        for (let j = i + 1; j < allEvents.length; j++) {
          const re = allEvents[j]
          const rc = re.content != null && typeof re.content === 'object' ? re.content : re
          if (rc?.type === 'tool_result' && rc?.name === 'write_file') {
            map[path] = rc?.result ?? ''
            break
          }
        }
      }
    }
    setFileDiffs(map)
  }, [allEvents.length])

  // Watch live events for bash approval requests
  useEffect(() => {
    if (liveEvents.length === 0) return
    const last = liveEvents[liveEvents.length - 1]
    if (last?.type === 'bash_approval_request') {
      setBashApproval({ command: last.command })
    }
  }, [liveEvents])

  // Auto-scroll feed
  useEffect(() => {
    feedBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [allEvents.length])

  // Elapsed timer
  useEffect(() => {
    if (!isRunning || !run?.started_at) return
    const start = new Date(run.started_at).getTime()
    const tick = () => {
      const s = Math.floor((Date.now() - start) / 1000)
      setElapsed(formatDuration(s))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [isRunning, run?.started_at])

  const handleAbort = async () => {
    setAborting(true)
    try {
      await abortRun(runId)
      const updated = await getRun(runId)
      setRun(updated)
    } catch (e) {
      setError(e.message)
    } finally {
      setAborting(false)
    }
  }

  const handleBashApproval = async (approved) => {
    setBashApproval(null)
    try {
      await approveBash(runId, approved)
    } catch (_) {}
  }

  const toggleExpand = (idx) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev)
      next.has(idx) ? next.delete(idx) : next.add(idx)
      return next
    })
  }

  if (loading) return <div className="p-8 text-gray-400">Loading run…</div>
  if (error && !run) return <div className="p-8 text-red-400">Error: {error}</div>

  const task = tasks.find((t) => t.id === run?.task_id)

  // Files written: deduplicated paths from write_file tool calls
  const filesWritten = []
  const seenPaths = new Set()
  for (const e of allEvents) {
    const content = e.content || e
    if (content?.type === 'tool_call' && content?.name === 'write_file') {
      const p = content?.input?.path
      if (p && !seenPaths.has(p)) {
        seenPaths.add(p)
        filesWritten.push(p)
      }
    }
  }

  return (
    <div className="flex flex-col h-screen bg-gray-900">
      {/* Bash approval modal */}
      {bashApproval && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg shadow-xl">
            <h2 className="text-base font-semibold mb-1">Allow bash command?</h2>
            <p className="text-xs text-gray-400 mb-3">The agent wants to run:</p>
            <pre className="bg-gray-900 rounded px-3 py-2 text-sm text-yellow-300 font-mono overflow-x-auto mb-5">
              {bashApproval.command}
            </pre>
            <div className="flex gap-3">
              <button
                onClick={() => handleBashApproval(true)}
                className="px-4 py-2 bg-orange-500 hover:bg-orange-600 rounded text-sm font-medium transition-colors"
              >
                Allow
              </button>
              <button
                onClick={() => handleBashApproval(false)}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors"
              >
                Deny
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-4 px-6 py-3 bg-gray-800 border-b border-gray-700 flex-shrink-0">
        <button
          onClick={() => navigate('/')}
          className="text-gray-400 hover:text-gray-200 text-sm transition-colors"
        >
          ← Board
        </button>
        <div className="flex-1 min-w-0">
          <h1 className="font-semibold truncate">{task?.title || 'Run'}</h1>
          <p className="text-xs text-gray-500 truncate">
            {run?.id} · started {new Date(run?.started_at).toLocaleTimeString()}
          </p>
        </div>
        <StatusBadge status={run?.status} />
      </div>

      {error && (
        <div className="px-6 py-2 bg-red-900/30 text-red-400 text-sm">{error}</div>
      )}

      {/* Body: feed + sidebar */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Event feed */}
        <div className="flex-1 overflow-y-auto p-4 space-y-2 min-w-0">
          {allEvents.length === 0 && !connected && (
            <p className="text-gray-500 text-sm">No events yet.</p>
          )}

          {allEvents.map((event, idx) => (
            <EventItem
              key={idx}
              event={event}
              idx={idx}
              expanded={expandedEvents.has(idx)}
              onToggle={() => toggleExpand(idx)}
            />
          ))}

          {connected && (
            <div className="flex items-center gap-2 text-gray-500 text-sm py-1">
              <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
              Thinking…
            </div>
          )}

          <div ref={feedBottomRef} />
        </div>

        {/* Right: Files written + diff viewer */}
        <div className="w-64 flex-shrink-0 border-l border-gray-700 flex flex-col overflow-hidden">
          <div className="p-4 border-b border-gray-700 flex-shrink-0">
            <h3 className="text-xs text-gray-400 uppercase tracking-wider mb-3">Files Written</h3>
            {filesWritten.length === 0 ? (
              <p className="text-xs text-gray-600">None yet</p>
            ) : (
              <ul className="space-y-1">
                {filesWritten.map((p) => (
                  <li key={p}>
                    <button
                      onClick={() => setSelectedFile(selectedFile === p ? null : p)}
                      className={`text-xs font-mono break-all text-left w-full rounded px-1.5 py-1 transition-colors ${
                        selectedFile === p
                          ? 'bg-orange-500/20 text-orange-300'
                          : 'text-green-400 hover:bg-gray-700'
                      }`}
                    >
                      {p}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Diff panel */}
          {selectedFile && (
            <div className="flex-1 overflow-y-auto p-3">
              <p className="text-xs text-gray-500 mb-2 font-mono truncate">{selectedFile}</p>
              {fileDiffs[selectedFile] ? (
                <DiffView diff={fileDiffs[selectedFile]} />
              ) : (
                <p className="text-xs text-gray-600">No diff available</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Bottom bar */}
      <div className="flex items-center gap-4 px-6 py-2 bg-gray-800 border-t border-gray-700 flex-shrink-0 text-xs text-gray-400">
        <span>{isRunning ? `⏱ ${elapsed}` : run?.completed_at ? `Completed in ${formatDuration(Math.floor((new Date(run.completed_at) - new Date(run.started_at)) / 1000))}` : ''}</span>
        <span>{allEvents.length} events</span>
        <span className="truncate">{task?.model || run?.model || ''}</span>
        <div className="flex-1" />
        {run?.summary && (
          <span className="text-gray-500 italic truncate max-w-xs" title={run.summary}>
            {run.summary.slice(0, 60)}{run.summary.length > 60 ? '…' : ''}
          </span>
        )}
        {isRunning && (
          <button
            onClick={handleAbort}
            disabled={aborting}
            className="px-3 py-1 bg-red-900/40 hover:bg-red-900/70 text-red-400 rounded text-xs transition-colors disabled:opacity-50"
          >
            {aborting ? 'Aborting…' : 'Abort'}
          </button>
        )}
      </div>
    </div>
  )
}

function DiffView({ diff }) {
  const lines = diff.split('\n')
  return (
    <pre className="text-xs font-mono leading-relaxed overflow-x-auto">
      {lines.map((line, i) => {
        let cls = 'text-gray-500'
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'text-green-400 bg-green-900/20'
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'text-red-400 bg-red-900/20'
        else if (line.startsWith('@@')) cls = 'text-blue-400'
        else if (line.startsWith('---') || line.startsWith('+++')) cls = 'text-gray-400'
        return (
          <span key={i} className={`block ${cls}`}>{line || ' '}</span>
        )
      })}
    </pre>
  )
}

function EventItem({ event, idx, expanded, onToggle }) {
  // Content can be the top-level event object (from WS) or event.content (from REST)
  const content = event.content != null && typeof event.content === 'object' ? event.content : event
  const type = content.type || event.type

  if (type === 'ping' || type === 'done' || type === 'bash_approval_request') {
    return null
  }

  if (type === 'text') {
    const text = content.content ?? content.text ?? (typeof event.content === 'string' ? event.content : '')
    if (!text) return null
    return (
      <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">{text}</div>
    )
  }

  if (type === 'tool_call') {
    const name = content.name || ''
    const input = content.input || {}
    return (
      <div className="border border-gray-700 rounded overflow-hidden">
        <button
          onClick={onToggle}
          className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-750 text-left text-sm transition-colors"
        >
          <span className="text-orange-400 font-mono text-xs">⚙</span>
          <span className="text-gray-300 font-mono text-xs">{name}</span>
          <span className="ml-auto text-gray-600 text-xs">{expanded ? '▲' : '▼'}</span>
        </button>
        {expanded && (
          <pre className="px-3 py-2 bg-gray-900 text-xs text-gray-400 overflow-x-auto">
            {JSON.stringify(input, null, 2)}
          </pre>
        )}
      </div>
    )
  }

  if (type === 'tool_result') {
    const name = content.name || ''
    const result = content.result ?? content.output ?? ''
    return (
      <div className="border border-gray-700/50 rounded overflow-hidden">
        <button
          onClick={onToggle}
          className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800/50 hover:bg-gray-800 text-left text-sm transition-colors"
        >
          <span className="text-green-500 text-xs">✓</span>
          <span className="text-gray-400 font-mono text-xs">{name} result</span>
          <span className="ml-auto text-gray-600 text-xs">{expanded ? '▲' : '▼'}</span>
        </button>
        {expanded && (
          <pre className="px-3 py-2 bg-gray-900/50 text-xs text-gray-500 overflow-x-auto max-h-48 overflow-y-auto">
            {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
          </pre>
        )}
      </div>
    )
  }

  if (type === 'error') {
    const msg = content.content ?? content.error ?? JSON.stringify(content)
    return (
      <div className="text-sm text-red-400 bg-red-900/20 rounded px-3 py-2">{msg}</div>
    )
  }

  // Fallback for unknown event types
  return (
    <div className="text-xs text-gray-600 font-mono">
      {JSON.stringify(content).slice(0, 200)}
    </div>
  )
}

function StatusBadge({ status }) {
  const styles = {
    running:   'bg-yellow-900/50 text-yellow-400',
    completed: 'bg-green-900/50 text-green-400',
    failed:    'bg-red-900/50 text-red-400',
    aborted:   'bg-gray-700 text-gray-400',
    review:    'bg-blue-900/50 text-blue-400',
  }
  return (
    <span className={`text-xs px-2 py-1 rounded capitalize ${styles[status] || 'bg-gray-700 text-gray-400'}`}>
      {status}
    </span>
  )
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s}s`
}
