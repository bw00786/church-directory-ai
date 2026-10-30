/**
 * AI Service Director hook
 * Polls /director/ai/status for the current mode, service context, and
 * pending proposals (assisted-mode queue with ids, TTL and hold reasons);
 * exposes mode switching + approve/reject by proposal id.
 */

import { useCallback, useEffect, useState } from 'react'

import { apiFetch } from '@/api/client'

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
    cycle_id?: string
    latency_ms?: number
    stale?: boolean
  } | null
  updated_at: string
}

export interface PendingAction {
  /** Stable proposal id; approve/reject address this, never a queue index. */
  id: string
  type: string
  target?: string | null
  parameters: Record<string, unknown>
  confidence: number
  reason: string
  source?: string
  category?: string
  cycle_id?: string | null
  /** Set while pacing (min hold / cooldown / speech boundary) defers the action. */
  held_reason?: string | null
  hold_count?: number
  age_seconds?: number
  /** Seconds until the proposal expires, as of the last poll. */
  expires_in_seconds?: number
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

export interface VisionStatus {
  detector: string | null
  inputs: Record<string, { healthy: boolean; fps: number; age: number | null }>
  occupancy: Record<string, { person_in_roi: boolean; frame_health: string }>
}

export interface LastCycle {
  cycle_id?: string
  latency_ms?: number | null
  context_version?: number | null
  current_context_version?: number | null
  drift?: number | null
  stale?: boolean
  decision?: string
  confidence?: number
  at?: string
}

export interface AdaptiveConfidenceStatus {
  enabled: boolean
  file?: string
  service_state?: string | null
  deltas: Record<string, number>
  feedback?: Record<string, { approved: number; rejected: number }>
  thresholds: Record<string, { base: number; effective: number }>
}

export interface AiDirectorStatus {
  mode: AiDirectorMode
  context: ServiceContextSnapshot
  pending_actions: PendingAction[]
  last_cycle?: LastCycle
  adaptive_confidence?: AdaptiveConfidenceStatus
  perception?: PerceptionStatus
  vision?: VisionStatus
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await apiFetch(path)
  return res.json()
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  return res.json()
}

export function useAIDirector(pollMs = 3000) {
  const [status, setStatus] = useState<AiDirectorStatus | null>(null)
  /** Wall-clock time (ms) of the last successful poll, for TTL countdowns. */
  const [fetchedAt, setFetchedAt] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const data = await getJSON<AiDirectorStatus>('/director/ai/status')
      setStatus(data)
      setFetchedAt(Date.now())
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
    async (id: string) => {
      await postJSON(`/director/ai/pending/${encodeURIComponent(id)}/approve`)
      await refresh()
    },
    [refresh],
  )

  const reject = useCallback(
    async (id: string) => {
      await postJSON(`/director/ai/pending/${encodeURIComponent(id)}/reject`)
      await refresh()
    },
    [refresh],
  )

  return { status, fetchedAt, loading, setMode, approve, reject, refresh }
}
