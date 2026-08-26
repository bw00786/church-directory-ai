/**
 * AI Service Director hook
 * Polls /director/ai/status for the current mode, service context, and
 * pending (assisted-mode) actions; exposes mode switching + approve/reject.
 */

import { useCallback, useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export type AiDirectorMode = 'manual' | 'assisted' | 'ai_directed'

export interface ServiceContextSnapshot {
  service_state: string
  speaker: string | null
  speaking: boolean
  camera_role: string | null
  atem_program: number | null
  easyworship_item: string | null
  recent_transcript: string
  last_actions: string[]
  last_decision: {
    decision?: string
    confidence?: number
    reason?: string
  } | null
  updated_at: string
}

export interface PendingAction {
  type: string
  target?: string | null
  parameters: Record<string, unknown>
  confidence: number
  reason: string
}

export interface PerceptionChannel {
  channel: number
  source: 'usb' | 'meter'
  vad_provider: 'silero' | 'energy'
  asr: boolean
  last_frame_age: number | null
}

export interface PerceptionStatus {
  usb_enabled: boolean
  usb_active: boolean
  channels: Record<string, PerceptionChannel>
}

export interface AiDirectorStatus {
  mode: AiDirectorMode
  context: ServiceContextSnapshot
  pending_actions: PendingAction[]
  perception?: PerceptionStatus
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  return res.json()
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  return res.json()
}

export function useAIDirector(pollMs = 3000) {
  const [status, setStatus] = useState<AiDirectorStatus | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const data = await getJSON<AiDirectorStatus>('/director/ai/status')
      setStatus(data)
    } catch {
      // Backend unreachable; keep last known status.
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, pollMs)
    return () => clearInterval(id)
  }, [refresh, pollMs])

  const setMode = useCallback(
    async (mode: AiDirectorMode) => {
      await postJSON('/director/ai/mode', { mode })
      await refresh()
    },
    [refresh],
  )

  const approve = useCallback(
    async (index: number) => {
      await postJSON(`/director/ai/pending/${index}/approve`)
      await refresh()
    },
    [refresh],
  )

  const reject = useCallback(
    async (index: number) => {
      await postJSON(`/director/ai/pending/${index}/reject`)
      await refresh()
    },
    [refresh],
  )

  return { status, loading, setMode, approve, reject, refresh }
}
