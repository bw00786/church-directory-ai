/**
 * Service director hook
 * Subscribes to /ws/director for live cue-sheet state and exposes controls.
 */

import { useEffect, useRef, useState } from 'react'

import { directorAPI } from '@/api/atem'

const WS_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(
  'http',
  'ws',
)

export interface CueAction {
  type: string
  description?: string
  atem_input?: number
  camera_id?: number
  preset_id?: number
  note?: string
}

export interface Cue {
  id: string
  name: string
  description?: string
  actions: CueAction[]
  advance: string
  ai_enabled?: boolean
  exit_hint?: string
}

export interface DirectorStatus {
  running: boolean
  autonomous: boolean
  script_name: string
  cue_index: number
  total_cues: number
  current_cue: Cue | null
  next_cue: Cue | null
  pending_suggestion: Record<string, unknown> | null
}

export interface DirectorAction {
  action: string
  detail: string
  description?: string
  cue_index: number
}

export function useDirector() {
  const [status, setStatus] = useState<DirectorStatus | null>(null)
  const [connected, setConnected] = useState(false)
  const [lastAction, setLastAction] = useState<DirectorAction | null>(null)
  const ws = useRef<WebSocket | null>(null)

  useEffect(() => {
    const socket = new WebSocket(`${WS_BASE}/ws/director`)
    ws.current = socket

    socket.onopen = () => setConnected(true)
    socket.onclose = () => setConnected(false)
    socket.onerror = () => setConnected(false)
    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'director') {
          setStatus(msg.data)
        } else if (msg.type === 'director_action') {
          setLastAction(msg.data)
        } else if (msg.type === 'director_suggestion') {
          setStatus((prev) => (prev ? { ...prev, pending_suggestion: msg.data } : prev))
        }
      } catch {
        // ignore malformed frames
      }
    }

    return () => socket.close()
  }, [])

  const start = (autonomous = true) => directorAPI.start(autonomous)
  const stop = () => directorAPI.stop()
  const next = () => directorAPI.next()
  const goto = (index: number) => directorAPI.goto(index)

  return { status, connected, lastAction, start, stop, next, goto }
}
