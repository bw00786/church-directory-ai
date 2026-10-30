/**
 * Post-service review API (/director/review).
 */

import client from './client'

export interface TimelineEntry {
  ts: string | null
  kind: 'decision' | 'action' | 'model_error' | 'event'
  summary: string
  status?: string | null
  detail?: string | null
  cycle_id?: string | null
}

export interface ReviewTotals {
  cycles: number
  decisions: Record<string, number>
  stale_cycles: number
  model_errors: number
  phase_changes_accepted: number
  latency_ms: { avg: number | null; p50: number | null; p95: number | null; max: number | null }
  proposals: {
    total: number
    executed: number
    approved: number
    pending: number
    rejected: number
    rejected_by_operator: number
    held: number
    expired: number
    dropped: number
    by_status: Record<string, number>
  }
  executed_by_type: Record<string, number>
  top_held_reasons: { reason: string; count: number }[]
  program_changes: { total: number; app: number; operator: number }
  mode_changes: { ts: string | null; mode: string | null }[]
  pauses: number
  degraded_events: { total: number; by_component: Record<string, number> }
  modes_seen: Record<string, number>
  rehearsals: number
  rehearsal_records_excluded: number
}

export interface ServiceReviewData {
  date: string
  has_data: boolean
  totals: ReviewTotals
  timeline: { total: number; head: TimelineEntry[]; tail: TimelineEntry[] }
  operator_advances?: { slides: number; recent: unknown[] } | null
  operator_shots?: Record<string, unknown> | null
}

export const reviewAPI = {
  async dates() {
    return client.get<{ dates: string[] }>('/director/review/dates')
  },
  async get(date?: string) {
    return client.get<ServiceReviewData>('/director/review', { params: date ? { date } : undefined })
  },
}
