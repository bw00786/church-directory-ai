import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ServiceReviewData } from '@/api/review'
import { useReview } from '@/hooks/useReview'
import { ServiceReview } from './ServiceReview'

vi.mock('@/hooks/useReview', () => ({ useReview: vi.fn() }))

const setDate = vi.fn()
const reload = vi.fn()

const review: ServiceReviewData = {
  date: '2026-10-25',
  has_data: true,
  totals: {
    cycles: 42, decisions: { continue: 30, transition: 12 }, stale_cycles: 2, model_errors: 1, phase_changes_accepted: 5,
    latency_ms: { avg: 800, p50: 700, p95: 1500, max: 2100 },
    proposals: { total: 20, executed: 9, approved: 4, pending: 1, rejected: 3, rejected_by_operator: 2, held: 5, expired: 1, dropped: 1, by_status: {} },
    executed_by_type: { 'PTZ_SELECT_ROLE -> pastor': 6 },
    top_held_reasons: [{ reason: 'min hold', count: 4 }, { reason: 'cooldown', count: 1 }],
    program_changes: { total: 15, app: 9, operator: 6 },
    mode_changes: [{ ts: '2026-10-25T14:00:00+00:00', mode: 'assisted' }],
    pauses: 1,
    degraded_events: { total: 2, by_component: { vad: 2 } },
    modes_seen: { assisted: 42 },
    rehearsals: 1,
    rehearsal_records_excluded: 7,
  },
  timeline: {
    total: 3,
    head: [
      { ts: '2026-10-25T14:00:01+00:00', kind: 'event', summary: 'mode -> assisted', status: 'ai_director_mode_changed' },
      { ts: '2026-10-25T14:05:00+00:00', kind: 'action', summary: 'PTZ_SELECT_ROLE -> pastor', status: 'executed_after_approval', detail: 'camera -> pastor (ok)' },
      { ts: '2026-10-25T14:06:00+00:00', kind: 'event', summary: 'program -> 2 (operator)', status: 'program_changed' },
    ],
    tail: [],
  },
  operator_advances: { slides: 14, recent: [] },
  operator_shots: null,
}

const hookValue = (overrides: Partial<ReturnType<typeof useReview>> = {}) => ({
  dates: ['2026-10-25', '2026-10-18'], date: '2026-10-25', setDate, review, loading: false, error: null, reload, ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useReview).mockReturnValue(hookValue())
})

describe('ServiceReview', () => {
  it('renders totals, hold reasons and the timeline for the selected date', () => {
    render(<ServiceReview />)
    expect(screen.getByText('Decision cycles').parentElement).toHaveTextContent('42')
    expect(screen.getByText('Executed').parentElement).toHaveTextContent('4 after approval · 1 still pending')
    expect(screen.getByText('Program changes').parentElement).toHaveTextContent('9 app · 6 operator')
    expect(screen.getByText('Latency').parentElement).toHaveTextContent('800 ms')
    expect(screen.getByText('min hold ×4')).toBeInTheDocument()
    expect(screen.getByText(/14 slide\(s\) on record/)).toBeInTheDocument()
    const rows = within(screen.getByRole('table', { name: 'Review timeline' })).getAllByRole('row')
    expect(rows).toHaveLength(4) // header + 3 entries
    expect(rows[2]).toHaveTextContent('PTZ_SELECT_ROLE -> pastor')
    expect(rows[2]).toHaveTextContent('camera -> pastor (ok)')
    expect(rows[2]).toHaveTextContent('executed_after_approval')
  })

  it('switches dates from the list', async () => {
    const user = userEvent.setup()
    render(<ServiceReview />)
    await user.selectOptions(screen.getByLabelText('Review date'), '2026-10-18')
    expect(setDate).toHaveBeenCalledWith('2026-10-18')
  })

  it('shows an empty state and the hidden-entries marker', () => {
    vi.mocked(useReview).mockReturnValue(hookValue({ review: { ...review, has_data: false } }))
    const { unmount } = render(<ServiceReview />)
    expect(screen.getByText(/No AI Director activity recorded on 2026-10-25/)).toBeInTheDocument()
    unmount()
    vi.mocked(useReview).mockReturnValue(
      hookValue({ review: { ...review, timeline: { total: 450, head: review.timeline.head, tail: review.timeline.head.slice(0, 1) } } }),
    )
    render(<ServiceReview />)
    expect(screen.getByText('… 446 more entries …')).toBeInTheDocument()
  })
})
