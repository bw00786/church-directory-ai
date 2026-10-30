import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '@/api/client'
import { useAIDirector } from './useAIDirector'

vi.mock('@/api/client', () => ({ apiFetch: vi.fn() }))

const statusBody = {
  mode: 'assisted',
  context: { service_state: 'hymn', speaker: null, speaking: false, camera_role: null, atem_program: null,
    easyworship_item: null, recent_transcript: '', last_actions: [], last_decision: null, updated_at: 'now' },
  pending_actions: [{ id: 'p-1', type: 'ATEM_CUT', parameters: {}, confidence: 0.95, reason: 'go', expires_in_seconds: 60 }],
}

function jsonResponse(body: unknown) {
  return { json: async () => body } as Response
}

beforeEach(() => {
  vi.mocked(apiFetch).mockResolvedValue(jsonResponse(statusBody))
})

describe('useAIDirector', () => {
  it('loads status and records the poll time for TTL countdowns', async () => {
    const { result } = renderHook(() => useAIDirector(60_000))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.status?.pending_actions[0].id).toBe('p-1')
    expect(result.current.fetchedAt).not.toBeNull()
    expect(apiFetch).toHaveBeenCalledWith('/director/ai/status')
  })

  it('approves and rejects by proposal id, then refreshes', async () => {
    const { result } = renderHook(() => useAIDirector(60_000))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => { await result.current.approve('p-1') })
    expect(apiFetch).toHaveBeenCalledWith('/director/ai/pending/p-1/approve', expect.objectContaining({ method: 'POST' }))
    await act(async () => { await result.current.reject('p 2') })
    expect(apiFetch).toHaveBeenCalledWith('/director/ai/pending/p%202/reject', expect.objectContaining({ method: 'POST' }))
    const statusCalls = vi.mocked(apiFetch).mock.calls.filter(([path]) => path === '/director/ai/status')
    expect(statusCalls.length).toBeGreaterThanOrEqual(3)
  })
})
