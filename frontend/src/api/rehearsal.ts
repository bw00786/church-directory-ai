/**
 * Rehearsal (replay) mode API (/director/rehearsal).
 */

import client from './client'
import type { PendingAction } from '@/hooks/useAIDirector'

export type RehearsalState = 'idle' | 'running' | 'finished' | 'stopped' | 'failed'
export type RehearsalMode = 'manual' | 'assisted' | 'ai_directed'

export interface RehearsalSample {
  id: string
  kind: 'sample' | 'recording'
  name: string
  description: string
  entries: number
}

export interface RehearsalDecision {
  index: number
  at: string
  observation: { speaker_role: string; speaking: boolean; transcript: string }
  decision: string
  confidence: number
  reason: string
  service_state: string
  latency_ms: number
  actions: { type: string; target?: string | null }[]
  proposals: string[]
  dropped: number
  executed: number
}

export interface RehearsalStatus {
  state: RehearsalState
  running: boolean
  name: string | null
  source: string | null
  mode: RehearsalMode
  speed: number
  position: number
  total: number
  progress: number
  started_at: string | null
  finished_at: string | null
  error: string | null
  service_state: string | null
  decisions: RehearsalDecision[]
  proposals: PendingAction[]
  executed: { at: string; type: string; target?: string | null; detail: string }[]
  mock_only: boolean
}

export interface RehearsalStartRequest {
  sample?: string
  file?: string
  speed?: number
  mode?: RehearsalMode
}

export const rehearsalAPI = {
  async samples() {
    return client.get<{ samples: RehearsalSample[]; allowed: boolean; reason: string }>('/director/rehearsal/samples')
  },
  async status() {
    return client.get<RehearsalStatus>('/director/rehearsal/status')
  },
  async start(body: RehearsalStartRequest) {
    return client.post<RehearsalStatus>('/director/rehearsal/start', body)
  },
  async stop() {
    return client.post<RehearsalStatus>('/director/rehearsal/stop')
  },
}
