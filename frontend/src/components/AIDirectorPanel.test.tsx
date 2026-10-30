import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAIDirector, type AiDirectorStatus, type PendingAction } from '@/hooks/useAIDirector'
import { AIDirectorPanel, PendingActionRow, remainingSeconds } from './AIDirectorPanel'

vi.mock('@/hooks/useAIDirector', () => ({ useAIDirector: vi.fn() }))

const approve = vi.fn().mockResolvedValue(undefined)
const reject = vi.fn().mockResolvedValue(undefined)
const setMode = vi.fn().mockResolvedValue(undefined)
const pause = vi.fn().mockResolvedValue(undefined)
const resume = vi.fn().mockResolvedValue(undefined)
const refresh = vi.fn().mockResolvedValue(undefined)

const pending: PendingAction = {
  id: 'abc123',
  type: 'PTZ_SELECT_ROLE',
  target: 'pastor',
  parameters: {},
  confidence: 0.93,
  reason: 'pastor is speaking',
  source: 'ai_director',
  held_reason: 'min hold: last program change 3.0s ago (< 8s)',
  expires_in_seconds: 42,
}

const status: AiDirectorStatus = {
  mode: 'assisted',
  context: {
    service_state: 'sermon',
    speaker: 'pastor',
    speaking: true,
    camera_role: 'pastor',
    atem_program: 1,
    easyworship_item: 'Sermon',
    recent_transcript: '',
    last_actions: [],
    last_decision: { decision: 'transition', confidence: 0.9, reason: 'sermon began', latency_ms: 812, stale: true },
    updated_at: 'now',
  },
  pending_actions: [pending],
  last_cycle: { latency_ms: 812, stale: true },
}

const hookValue = (overrides: Partial<ReturnType<typeof useAIDirector>> = {}) => ({
  status, fetchedAt: Date.now(), loading: false, setMode, pause, resume, approve, reject, refresh, ...overrides,
})

beforeEach(() => {
  approve.mockResolvedValue(undefined)
  reject.mockResolvedValue(undefined)
  vi.mocked(useAIDirector).mockReturnValue(hookValue())
})

describe('remainingSeconds', () => {
  it('counts down from the last poll and never goes negative', () => {
    const fetchedAt = 1_000_000
    expect(remainingSeconds(pending, fetchedAt, fetchedAt)).toBe(42)
    expect(remainingSeconds(pending, fetchedAt, fetchedAt + 5_000)).toBe(37)
    expect(remainingSeconds(pending, fetchedAt, fetchedAt + 90_000)).toBe(0)
    expect(remainingSeconds({ ...pending, expires_in_seconds: undefined }, fetchedAt, fetchedAt)).toBeNull()
  })
})

describe('AIDirectorPanel', () => {
  it('shows reason, hold reason, TTL and approves/rejects by proposal id', async () => {
    const user = userEvent.setup()
    render(<AIDirectorPanel />)
    const list = screen.getByRole('list', { name: 'Pending AI proposals' })
    const row = within(list).getByRole('listitem', { name: 'PTZ_SELECT_ROLE pastor' })
    expect(row).toHaveTextContent('pastor is speaking (93%)')
    expect(row).toHaveTextContent('held: min hold')
    expect(within(row).getByLabelText(/expires in 4\d seconds/)).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Approve' }))
    expect(approve).toHaveBeenCalledWith('abc123')
    await user.click(within(row).getByRole('button', { name: 'Reject' }))
    expect(reject).toHaveBeenCalledWith('abc123')
  })

  it('surfaces cycle latency and stale-context on the decision card', () => {
    render(<AIDirectorPanel />)
    expect(screen.getByText('812 ms')).toBeInTheDocument()
    expect(screen.getByText('stale context')).toBeInTheDocument()
  })

  it('labels the queue as held actions in ai_directed mode and hides it when empty', () => {
    vi.mocked(useAIDirector).mockReturnValue(hookValue({ status: { ...status, mode: 'ai_directed' } }))
    const { unmount } = render(<AIDirectorPanel />)
    expect(screen.getByText('Held Actions (waiting for pacing)')).toBeInTheDocument()
    unmount()
    vi.mocked(useAIDirector).mockReturnValue(hookValue({ status: { ...status, pending_actions: [] } }))
    render(<AIDirectorPanel />)
    expect(screen.queryByRole('list', { name: 'Pending AI proposals' })).not.toBeInTheDocument()
  })

  it('renders a row without hold or TTL when the queue omits them', () => {
    const bare: PendingAction = { id: 'x', type: 'EASYWORSHIP_NEXT_SLIDE', parameters: {}, confidence: 0.8, reason: 'verse ended' }
    render(<PendingActionRow action={bare} fetchedAt={null} now={Date.now()} onApprove={approve} onReject={reject} />)
    expect(screen.queryByText(/held:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/expires in/)).not.toBeInTheDocument()
  })

  it('pauses the AI and shows the paused state with a resume button', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<AIDirectorPanel />)
    expect(screen.queryByText('AI paused')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Pause AI Director' }))
    expect(pause).toHaveBeenCalledTimes(1)
    unmount()
    vi.mocked(useAIDirector).mockReturnValue(
      hookValue({ status: { ...status, mode: 'manual', paused: true, paused_from: 'assisted' } }),
    )
    render(<AIDirectorPanel />)
    expect(screen.getByText('AI paused')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Resume returns to Assisted')
    expect(screen.queryByRole('button', { name: 'Pause AI Director' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Resume AI Director/ }))
    expect(resume).toHaveBeenCalledTimes(1)
  })
})
