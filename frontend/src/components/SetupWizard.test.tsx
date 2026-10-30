import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SetupStatus } from '@/api/setup'
import { useSetup } from '@/hooks/useSetup'
import { SetupWizard, SetupWizardButton, STEPS } from './SetupWizard'

vi.mock('@/hooks/useSetup', async () => {
  const actual = await vi.importActual<typeof import('@/hooks/useSetup')>('@/hooks/useSetup')
  return { ...actual, useSetup: vi.fn() }
})

const status: SetupStatus = {
  enabled: true,
  needs_setup: true,
  missing: ['atem', 'api_keys'],
  all_mocked: false,
  mocks: { atem: false, mixer: true, easyworship: true },
  api_host: '0.0.0.0',
  api_loopback: false,
  keys: { operator: true, admin: false, auth_enabled: true },
  env_file: '/repo/.env',
  items: [
    { key: 'atem', label: 'ATEM switcher', configured: false, required: true, missing: true, mock: false, value: null, hint: 'ATEM_IP' },
    { key: 'camera_1', label: 'PTZ camera 1', configured: true, required: true, missing: false, mock: null, value: '10.0.0.9', hint: '' },
    { key: 'api_keys', label: 'API keys', configured: false, required: true, missing: true, mock: null, value: 'operator', hint: '' },
    { key: 'mixer_usb', label: 'Mixer audio (USB)', configured: false, required: false, missing: false, mock: true, value: null, hint: '' },
  ],
}

const discover = vi.fn()
const test = vi.fn()
const audioDevices = vi.fn()
const saveEnv = vi.fn()
const refresh = vi.fn().mockResolvedValue(undefined)

const hookValue = (overrides: Partial<ReturnType<typeof useSetup>> = {}) => ({
  status, loading: false, error: null, needsSetup: true, refresh, discover, test, audioDevices, saveEnv, ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  discover.mockResolvedValue({ found: '192.168.4.50', detail: 'ATEM found' })
  test.mockResolvedValue({ component: 'camera', ok: true, detail: 'VISCA 10.0.0.9:1240 reachable' })
  audioDevices.mockResolvedValue({
    devices: [{ index: 3, name: 'MGX16 USB', max_input_channels: 16, default_samplerate: 48000, hostapi: 'WASAPI', matches_configured: true }],
    note: null,
  })
  saveEnv.mockResolvedValue({ written: ['ATEM_IP', 'MGX_USB_DEVICE_NAME'], restart_required: true, env_file: '/repo/.env' })
  vi.mocked(useSetup).mockReturnValue(hookValue())
})

async function next(user: ReturnType<typeof userEvent.setup>, times = 1) {
  for (let i = 0; i < times; i += 1) await user.click(screen.getByRole('button', { name: 'Next' }))
}

describe('SetupWizard', () => {
  it('opens automatically when required settings are missing and lists them', () => {
    render(<SetupWizard />)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent(`Setup wizard — ${STEPS[0]}`)
    expect(within(dialog).getByText('ATEM switcher')).toBeInTheDocument()
    expect(within(dialog).getByText('API keys')).toBeInTheDocument()
  })

  it('stays closed when nothing is missing and opens from the toolbar button', async () => {
    const user = userEvent.setup()
    vi.mocked(useSetup).mockReturnValue(hookValue({ needsSetup: false, status: { ...status, needs_setup: false, missing: [] } }))
    render(
      <>
        <SetupWizardButton />
        <SetupWizard />
      </>,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Setup' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('does not reopen in the same session after being closed', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<SetupWizard />)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    unmount()
    render(<SetupWizard />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('discovers the ATEM, fills the field, and saves only touched keys with a restart banner', async () => {
    const user = userEvent.setup()
    render(<SetupWizard />)
    await next(user)
    expect(screen.getByRole('dialog')).toHaveTextContent('Setup wizard — Network')
    await user.click(screen.getByRole('button', { name: 'Discover ATEM' }))
    await waitFor(() => expect(screen.getByText('ATEM found (192.168.4.50)')).toBeInTheDocument())
    expect(discover).toHaveBeenCalledWith('atem')
    expect(screen.getByLabelText('ATEM_IP')).toHaveValue('192.168.4.50')

    await next(user, 2) // Cameras -> Audio
    const device = await screen.findByLabelText('USB input device')
    await user.selectOptions(device, 'MGX16 USB')

    await next(user, 3) // Slides -> Security -> Review
    expect(screen.getByRole('dialog')).toHaveTextContent('Setup wizard — Review & Save')
    expect(screen.getByText('ATEM_IP')).toBeInTheDocument()
    expect(screen.getByText('MGX_USB_DEVICE_NAME')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save to .env' }))
    await waitFor(() => expect(saveEnv).toHaveBeenCalledWith({ ATEM_IP: '192.168.4.50', MGX_USB_DEVICE_NAME: 'MGX16 USB' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Restart the backend')
  })

  it('runs the camera test against the entered host and masks the password in review', async () => {
    const user = userEvent.setup()
    render(<SetupWizard />)
    await next(user, 2)
    await user.type(screen.getByLabelText('CAMERA_1_PASSWORD'), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Test camera' }))
    await waitFor(() => expect(test).toHaveBeenCalledWith('camera', expect.objectContaining({ host: '10.0.0.9' })))
    expect(await screen.findByText('VISCA 10.0.0.9:1240 reachable')).toBeInTheDocument()
    await next(user, 4)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('CAMERA_1_PASSWORD')
    expect(dialog).not.toHaveTextContent('hunter2')
  })

  it('shows the admin-key error when saving is forbidden', async () => {
    const user = userEvent.setup()
    saveEnv.mockRejectedValue(Object.assign(new Error('Request failed with status code 403'), {
      isAxiosError: true, response: { status: 403, data: { detail: 'Admin API key required to write .env' } },
    }))
    render(<SetupWizard />)
    await next(user)
    await user.type(screen.getByLabelText('ATEM_IP'), '1.2.3.4')
    await next(user, 5)
    await user.click(screen.getByRole('button', { name: 'Save to .env' }))
    expect(await screen.findByText('Admin API key required')).toBeInTheDocument()
  })
})
