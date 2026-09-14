import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useVoiceAttention } from '@/hooks/useVoiceAttention'
import { event, settings, status } from '@/test/voiceFixtures'
import { VoiceAlert, VoiceAttentionPanel, VoiceQueue, VoiceSettings, VoiceTestDialog } from './VoiceAttentionPanel'

vi.mock('@/hooks/useVoiceAttention', () => ({ useVoiceAttention: vi.fn() }))

const command = vi.fn().mockResolvedValue(true)
const acknowledge = vi.fn().mockResolvedValue(true)
const saveSettings = vi.fn().mockResolvedValue(true)
const refresh = vi.fn().mockResolvedValue(undefined)
const hookValue = () => ({ status, settings, events: [event], loading: false, connected: true,
  error: null, busy: false, command, acknowledge, saveSettings, refresh })

beforeEach(() => {
  command.mockResolvedValue(true)
  acknowledge.mockResolvedValue(true)
  saveSettings.mockResolvedValue(true)
  vi.mocked(useVoiceAttention).mockReturnValue(hookValue())
})

describe('VoiceAlert and VoiceQueue', () => {
  it('labels critical priority accessibly, acknowledges and dismisses without approving', async () => {
    const user = userEvent.setup()
    render(<VoiceAlert event={event} busy={false} onAcknowledge={acknowledge} />)
    expect(screen.getByRole('article', { name: 'Critical alert' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(event.message)
    expect(screen.getByText('P4 · Critical')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review in Assistant' })).toHaveAttribute('href', '#assistant-confirmation')
    await user.click(screen.getByRole('link', { name: 'Review in Assistant' }))
    expect(acknowledge).not.toHaveBeenCalled()
    expect(command).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Acknowledge' }))
    expect(acknowledge).toHaveBeenLastCalledWith(event.id, { action: 'acknowledge' })
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(acknowledge).toHaveBeenLastCalledWith(event.id, { action: 'dismiss' })
  })

  it('routes operator-required notices without a token to the existing director', () => {
    render(<VoiceAlert event={{ ...event, input: { ...event.input, approval_token: null } }} busy={false} onAcknowledge={acknowledge} />)
    expect(screen.getByRole('link', { name: 'Review in Director' })).toHaveAttribute('href', '#ai-director-panel')
  })

  it.each(['useful', 'not_useful', 'too_sensitive', 'too_late', 'correct', 'incorrect'])('submits %s feedback only through acknowledgement', async (feedback) => {
    const user = userEvent.setup()
    render(<VoiceAlert event={event} busy={false} onAcknowledge={acknowledge} />)
    await user.click(screen.getByRole('combobox', { name: 'Alert feedback' }))
    await user.click(screen.getByRole('option', { name: feedback.replace(/_/g, ' ') }))
    await user.click(screen.getByRole('button', { name: 'Send feedback' }))
    expect(acknowledge).toHaveBeenCalledWith(event.id, { action: 'acknowledge', feedback })
  })

  it('disables resolved/acknowledged alerts and shows queue empty state', () => {
    render(<><VoiceAlert event={{ ...event, acknowledged: true }} busy={false} onAcknowledge={acknowledge} />
      <VoiceQueue events={[]} busy={false} onAcknowledge={acknowledge} /></>)
    expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeDisabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('No pending alerts.')).toBeInTheDocument()
  })
})

describe('VoiceSettings', () => {
  it('shows read-only routing and sends only allowed editable fields', async () => {
    const user = userEvent.setup()
    render(<VoiceSettings settings={settings} busy={false} onSave={saveSettings} />)
    await user.click(screen.getByRole('button', { name: 'Voice settings' }))
    expect(screen.getByText(/Server environment \(read-only\)/)).toHaveTextContent('CoreAudio')
    expect(screen.queryByRole('textbox', { name: /output|routing|device/i })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Voice mode' })).toHaveTextContent('attention only')
    await user.clear(screen.getByRole('textbox', { name: 'Operator name' }))
    await user.type(screen.getByRole('textbox', { name: 'Operator name' }), 'Sam')
    await user.click(screen.getByRole('combobox', { name: 'Voice mode' }))
    await user.click(screen.getByRole('option', { name: 'testing' }))
    expect(screen.getByText(/TESTING may speak background/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save voice settings' }))
    expect(saveSettings).toHaveBeenCalledWith({ mode: 'testing', operator_name: 'Sam', cooldown_seconds: 30,
      repeat_interval_seconds: 60, aggregation_window_seconds: 2, persona: settings.persona })
    expect(screen.getByText('Voice settings saved.')).toBeInTheDocument()
  })

  it('preserves unsaved edits across snapshots, prevents blank timing values, and resets edits', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<VoiceSettings settings={settings} busy={false} onSave={saveSettings} />)
    await user.click(screen.getByRole('button', { name: 'Voice settings' }))
    const cooldown = screen.getByRole('spinbutton', { name: 'Cooldown (seconds)' })
    await user.clear(cooldown)
    await user.click(screen.getByRole('button', { name: 'Save voice settings' }))
    expect(saveSettings).not.toHaveBeenCalled()
    await user.type(cooldown, '15')
    rerender(<VoiceSettings settings={{ ...settings, cooldown_seconds: 40 }} busy={false} onSave={saveSettings} />)
    expect(cooldown).toHaveValue(15)
    await user.click(screen.getByRole('button', { name: 'Reset changes' }))
    expect(cooldown).toHaveValue(40)
  })
})

describe('VoiceTestDialog', () => {
  it('requires onsite verification and reports queued, never verified playback', async () => {
    const user = userEvent.setup()
    const onTest = vi.fn().mockResolvedValue(true)
    render(<VoiceTestDialog open busy={false} onClose={vi.fn()} onTest={onTest} />)
    const dialog = screen.getByRole('dialog', { name: 'Test dedicated headset' })
    expect(within(dialog).getByText(/PA\/ATEM\/stream/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queue headset test' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Queue headset test' }))
    expect(onTest).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/Test queued.*does not guarantee physical routing/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queue headset test' })).toBeDisabled()
    expect(document.querySelector('audio, video')).toBeNull()
  })

  it('reports test failure inside the modal instead of claiming a queued test', async () => {
    const user = userEvent.setup()
    render(<VoiceTestDialog open busy={false} onClose={vi.fn()} onTest={vi.fn().mockResolvedValue(false)} />)
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Queue headset test' }))
    expect(screen.getByText(/Headset test request failed/)).toBeInTheDocument()
    expect(screen.queryByText(/Test queued/)).not.toBeInTheDocument()
  })
})

describe('VoiceAttentionPanel', () => {
  it('renders status, warnings, last alert, queue, history and backend-only controls', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<VoiceAttentionPanel />)
    expect(screen.getByRole('region', { name: 'Voice attention' })).toBeInTheDocument()
    expect(screen.getByText(/Acknowledgement does not approve a production action/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Last alert' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Queue (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Recent alerts (1)' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enable voice' }))
    expect(command).toHaveBeenLastCalledWith('enable')
    await user.click(screen.getByRole('button', { name: 'Mute voice' }))
    expect(command).toHaveBeenLastCalledWith('mute')
    vi.mocked(useVoiceAttention).mockReturnValue({ ...hookValue(), status: { ...status, enabled: true, muted: true } })
    rerender(<VoiceAttentionPanel />)
    await user.click(screen.getByRole('button', { name: 'Disable voice' }))
    expect(command).toHaveBeenLastCalledWith('disable')
    await user.click(screen.getByRole('button', { name: 'Unmute voice' }))
    expect(command).toHaveBeenLastCalledWith('unmute')
    expect(screen.getByRole('button', { name: 'Repeat last alert' })).toBeDisabled()
    vi.mocked(useVoiceAttention).mockReturnValue({ ...hookValue(), status: { ...status, enabled: true } })
    rerender(<VoiceAttentionPanel />)
    await user.click(screen.getByRole('button', { name: 'Repeat last alert' }))
    expect(command).toHaveBeenLastCalledWith('repeat')
    await user.click(screen.getByRole('button', { name: 'Test headset' }))
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Queue headset test' }))
    expect(command).toHaveBeenLastCalledWith('test')
  })

  it('shows loading, failure and fallback states and disables unavailable controls', async () => {
    const user = userEvent.setup()
    vi.mocked(useVoiceAttention).mockReturnValue({ ...hookValue(), status: null, settings: null, loading: true, connected: false })
    const { rerender } = render(<VoiceAttentionPanel />)
    expect(screen.getByRole('progressbar', { name: 'Loading voice attention' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enable voice' })).toBeDisabled()
    expect(screen.getByText(/polling every 5s/)).toBeInTheDocument()
    vi.mocked(useVoiceAttention).mockReturnValue({ ...hookValue(), settings: null, error: 'Network unavailable',
      status: { ...status, error: 'Synthesis failed', headset: { ...status.headset, error: 'Device missing' }, mode: 'testing' } })
    rerender(<VoiceAttentionPanel />)
    expect(screen.getByText('Network unavailable')).toBeInTheDocument()
    expect(screen.getByText('Synthesis failed')).toBeInTheDocument()
    expect(screen.getByText('Headset: Device missing')).toBeInTheDocument()
    expect(screen.getByText(/Voice settings unavailable/)).toBeInTheDocument()
    expect(screen.getByText(/TESTING may speak background events/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  })
})