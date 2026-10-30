import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { cameraAPI } from '@/api/atem'
import { useRig } from '@/hooks/useRig'
import { CameraPanels, RigSummary } from './CameraPanels'

vi.mock('@/api/atem', () => ({ cameraAPI: { listCameras: vi.fn() } }))
vi.mock('@/hooks/useRig', () => ({ useRig: vi.fn() }))
vi.mock('./CameraJoystick', () => ({
  CameraJoystick: ({ cameraId }: { cameraId: number }) => <div data-testid={`joystick-${cameraId}`}>joystick {cameraId}</div>,
}))

const listCameras = vi.mocked(cameraAPI.listCameras)

beforeEach(() => {
  listCameras.mockReset()
  vi.mocked(useRig).mockReturnValue({
    rig: null,
    summary: null,
    loading: false,
    error: null,
    refresh: vi.fn(),
    validate: vi.fn(),
    save: vi.fn(),
  })
})

describe('CameraPanels', () => {
  it('renders one joystick per registered camera with its name and connection state', async () => {
    listCameras.mockResolvedValue({
      data: {
        cameras: [
          { camera_id: 2, name: 'Rear PTZ', host: '192.168.30.32', connected: false },
          { camera_id: 1, name: 'Front PTZ', host: '192.168.30.31', connected: true },
        ],
      },
    } as never)

    render(<CameraPanels />)

    await waitFor(() => expect(screen.getByTestId('joystick-2')).toBeInTheDocument())
    expect(screen.getByTestId('joystick-1')).toBeInTheDocument()
    expect(screen.getByText('Front PTZ')).toBeInTheDocument()
    expect(screen.getByText('Rear PTZ')).toBeInTheDocument()
    expect(screen.getByText('connected')).toBeInTheDocument()
    expect(screen.getByText('not connected')).toBeInTheDocument()
    // Sorted by id.
    const panels = screen.getByTestId('camera-panels')
    expect(panels.firstChild).toBe(screen.getByTestId('camera-panel-1'))
  })

  it('falls back to camera 1 when no cameras are registered', async () => {
    listCameras.mockResolvedValue({ data: { cameras: [] } } as never)
    render(<CameraPanels />)
    await waitFor(() => expect(listCameras).toHaveBeenCalled())
    expect(screen.getByTestId('joystick-1')).toBeInTheDocument()
    expect(screen.queryByTestId('joystick-2')).not.toBeInTheDocument()
  })
})

describe('RigSummary', () => {
  it('shows church, cameras, roles and mixer channels', () => {
    vi.mocked(useRig).mockReturnValue({
      rig: null,
      summary: {
        church: 'Grace Community Church',
        source: 'file',
        path: '/srv/rig.yaml',
        cameras: [{ id: 1, name: 'Front PTZ', driver: 'ptzoptics', atem_input: 1, host: '10.0.0.5' }],
        slides_input: 4,
        roles: { pastor: { camera: 1, preset: 1 } },
        mixer_roles: { choir: 6 },
        cue_overrides: ['anthem'],
        follower_cues: ['first_song'],
      },
      loading: false,
      error: null,
      refresh: vi.fn(),
      validate: vi.fn(),
      save: vi.fn(),
    })
    listCameras.mockResolvedValue({ data: { cameras: [] } } as never)

    render(<RigSummary />)

    expect(screen.getByText('Grace Community Church')).toBeInTheDocument()
    expect(screen.getByText('1: Front PTZ → ATEM 1')).toBeInTheDocument()
    expect(screen.getByText('slides → ATEM 4')).toBeInTheDocument()
    expect(screen.getByText('pastor: cam 1 / preset 1')).toBeInTheDocument()
    expect(screen.getByText('choir: ch 6')).toBeInTheDocument()
    expect(screen.getByText(/1 cue override: anthem/)).toBeInTheDocument()
  })
})
