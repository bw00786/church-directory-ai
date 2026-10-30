import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RehearsalStatus } from '@/api/rehearsal'
import { useRehearsal } from '@/hooks/useRehearsal'
import { RehearsalPanel } from './RehearsalPanel'

vi.mock('@/hooks/useRehearsal', () => ({ useRehearsal: vi.fn() }))

const start = vi.fn().mockResolvedValue(undefined)
const stop = vi.fn().mockResolvedValue(undefined)
const refresh = vi.fn().mockResolvedValue(undefined)
const loadSamples = vi.fn().mockResolvedValue(undefined)

const samples = [
  { id: 'default', kind: 'sample' as const, name: 'Sunday morning (short)', description: '', entries: 12 },
  { id: 'easter', kind: 'recording' as const, name: 'Easter 2026', description: '', entries: 300 },
]

const idle: RehearsalStatus = {
  state: 'idle', running: false, name: null, source: null, mode: 'ai_directed', speed: 1, position: 0, total: 0, progress: 0,
  started_at: null, finished_at: null, error: null, service_state: null, decisions: [], proposals: [], executed: [], mock_only: true,
}

const running: RehearsalStatus = {
  ...idle, state: 'running', running: true, name: 'Sunday morning (short)', position: 3, total: 12, progress: 0.25, service_state: 'sermon',
  executed: [{ at: 'now', type: 'PTZ_SELECT_ROLE', target: 'pastor', detail: 'mock PTZ_SELECT_ROLE -> pastor' }],
  decisions: [
    { index: 0, at: 'now', observation: { speaker_role: 'liturgist', speaking: true, transcript: 'Good morning' }, decision: 'continue', confidence: 0.3, reason: 'welcome', service_state: 'welcome', latency_ms: 300, actions: [], proposals: [], dropped: 0, executed: 0 },
    { index: 1, at: 'now', observation: { speaker_role: 'pastor', speaking: true, transcript: 'Please be seated' }, decision: 'transition', confidence: 0.95, reason: 'pastor began preaching', service_state: 'sermon', latency_ms: 812, actions: [{ type: 'PTZ_SELECT_ROLE', target: 'pastor' }], proposals: ['p1'], dropped: 0, executed: 1 },
  ],
}

const hookValue = (overrides: Partial<ReturnType<typeof useRehearsal>> = {}) => ({
  status: idle, samples, allowed: { allowed: true, reason: 'ok' }, error: null, busy: false, start, stop, refresh, loadSamples, ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useRehearsal).mockReturnValue(hookValue())
})

describe('RehearsalPanel', () => {
  it('starts the selected recording with speed and mode', async () => {
    const user = userEvent.setup()
    render(<RehearsalPanel />)
    await user.selectOptions(screen.getByLabelText('Recording'), 'easter')
    await user.selectOptions(screen.getByLabelText('Speed'), '5')
    await user.selectOptions(screen.getByLabelText('Mode'), 'assisted')
    await user.click(screen.getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith({ sample: 'easter', speed: 5, mode: 'assisted' })
  })

  it('shows progress, decisions (newest first) and a stop button while running', async () => {
    const user = userEvent.setup()
    vi.mocked(useRehearsal).mockReturnValue(hookValue({ status: running }))
    render(<RehearsalPanel />)
    expect(screen.getByLabelText('rehearsal progress')).toHaveAttribute('aria-valuenow', '25')
    expect(screen.getByText('running · 3/12')).toBeInTheDocument()
    expect(screen.getByText('1 executed (mock)')).toBeInTheDocument()
    const rows = within(screen.getByRole('list', { name: 'Rehearsal decisions' })).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('transition (95%) — pastor began preaching')
    expect(rows[0]).toHaveTextContent('PTZ_SELECT_ROLE → pastor')
    expect(rows[1]).toHaveTextContent('#1 · liturgist: “Good morning”')
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument()
  })

  it('disables start and explains when rehearsal is not allowed, and surfaces errors', () => {
    vi.mocked(useRehearsal).mockReturnValue(
      hookValue({ allowed: { allowed: false, reason: 'real hardware is configured' }, error: 'a rehearsal is already running' }),
    )
    render(<RehearsalPanel />)
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
    expect(screen.getByText(/Rehearsal unavailable: real hardware is configured/)).toBeInTheDocument()
    expect(screen.getByText('a rehearsal is already running')).toBeInTheDocument()
  })
})
