import { useEffect, useRef, useState } from 'react'
import { WS_BASE } from '../api'

const MAX_RECONNECTS = 5

export default function useWebSocket(runId, { enabled = true } = {}) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(null)

  const wsRef = useRef(null)
  const reconnectCount = useRef(0)
  const reconnectTimer = useRef(null)
  const isDone = useRef(false)

  useEffect(() => {
    if (!runId || !enabled) return

    // Reset state when runId changes
    setEvents([])
    setConnected(false)
    setError(null)
    reconnectCount.current = 0
    isDone.current = false

    function connect() {
      const ws = new WebSocket(`${WS_BASE}/runs/${runId}/stream`)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
        setError(null)
        reconnectCount.current = 0
      }

      ws.onmessage = (e) => {
        let event
        try {
          event = JSON.parse(e.data)
        } catch (_) {
          return
        }
        if (event.type === 'ping') return
        setEvents((prev) => [...prev, event])
        if (event.type === 'done') {
          isDone.current = true
          setConnected(false)
          ws.close()
        }
      }

      ws.onerror = () => {
        setError('WebSocket connection error')
      }

      ws.onclose = () => {
        setConnected(false)
        if (isDone.current) return
        if (reconnectCount.current >= MAX_RECONNECTS) {
          setError('Connection lost after multiple retries')
          return
        }
        reconnectCount.current += 1
        reconnectTimer.current = setTimeout(connect, 2000)
      }
    }

    connect()

    return () => {
      clearTimeout(reconnectTimer.current)
      isDone.current = true // prevent reconnect on unmount
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [runId, enabled])

  return { events, connected, error }
}
