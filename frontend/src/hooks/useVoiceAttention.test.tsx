import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { voiceAPI } from '@/api/voice'
import { event, settings, status, MockWebSocket } from '@/test/voiceFixtures'
import { useVoiceAttention } from './useVoiceAttention'

vi.mock('@/api/voice', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/voice')>(),
  voiceAPI: { status: vi.fn(), events: vi.fn(), config: vi.fn(), command: vi.fn(), configure: vi.fn(), acknowledge: vi.fn() },
}))

beforeEach(() => {
  vi.resetAllMocks()
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.mocked(voiceAPI.status).mockResolvedValue(status)
  vi.mocked(voiceAPI.events).mockResolvedValue({ events: [event] })
  vi.mocked(voiceAPI.config).mockResolvedValue(settings)
  vi.mocked(voiceAPI.command).mockResolvedValue({ ...status, enabled: true })
  vi.mocked(voiceAPI.configure).mockResolvedValue({ ...settings, operator_name: 'Sam' })
  vi.mocked(voiceAPI.acknowledge).mockResolvedValue({ ...event, acknowledged: true })
})

describe('useVoiceAttention', () => {
  it('loads HTTP snapshots and accepts initial and subsequent WebSocket status', async () => {
    const { result } = renderHook(useVoiceAttention)
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.status).toEqual(status)
    expect(result.current.settings).toEqual(settings)
    expect(result.current.events).toEqual([event])
    for (const type of ['voice_status', 'voice_attention', 'voice_queue', 'voice_acknowledgement']) {
      act(() => MockWebSocket.instances[0].receive({ type, status: { ...status, muted: true }, event: { ...event, result: type } }))
      expect(result.current.status?.muted).toBe(true)
      expect(result.current.events).toHaveLength(1)
      expect(result.current.events[0].result).toBe(type)
    }
    expect(result.current.connected).toBe(true)
  })

  it('polls every five seconds, reconnects after disconnect, and stops polling when live', async () => {
    vi.useFakeTimers()
    const { result, unmount } = renderHook(useVoiceAttention)
    await act(async () => {})
    expect(voiceAPI.status).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(voiceAPI.status).toHaveBeenCalledTimes(2)
    act(() => MockWebSocket.instances[0].receive({ type: 'voice_status', status }))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(voiceAPI.status).toHaveBeenCalledTimes(2)
    act(() => MockWebSocket.instances[0].disconnect())
    expect(result.current.connected).toBe(false)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(voiceAPI.status).toHaveBeenCalledTimes(3)
    expect(MockWebSocket.instances).toHaveLength(2)
    act(() => MockWebSocket.instances[1].open())
    await act(async () => {})
    expect(voiceAPI.status).toHaveBeenCalledTimes(4)
    unmount()
    expect(MockWebSocket.instances[1].closed).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    expect(vi.mocked(voiceAPI.status).mock.calls[0][0]?.aborted).toBe(true)
  })

  it('falls back and reconnects if WebSocket construction fails', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', class { constructor() { throw new Error('Unavailable') } })
    const { result } = renderHook(useVoiceAttention)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.status).toEqual(status)
    expect(result.current.connected).toBe(false)
    expect(voiceAPI.status).toHaveBeenCalledTimes(2)
  })

  it('ignores malformed frames and retries after a socket error', async () => {
    vi.useFakeTimers()
    const { result } = renderHook(useVoiceAttention)
    await act(async () => {})
    act(() => {
      MockWebSocket.instances[0].onmessage?.({ data: '{invalid' })
      MockWebSocket.instances[0].receive({ type: 'other', status })
      MockWebSocket.instances[0].receive({ type: 'voice_status' })
      MockWebSocket.instances[0].onerror?.()
    })
    expect(result.current.status).toEqual(status)
    expect(MockWebSocket.instances[0].closed).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('does not let an initial slow snapshot overwrite newer socket data or lose config/history', async () => {
    let resolveStatus!: (value: typeof status) => void
    vi.mocked(voiceAPI.status).mockReturnValueOnce(new Promise((resolve) => { resolveStatus = resolve }))
    const { result } = renderHook(useVoiceAttention)
    act(() => MockWebSocket.instances[0].receive({ type: 'voice_attention', status: { ...status, muted: true },
      event: { ...event, id: 'new-event', timestamp: '2026-09-13T11:00:00Z' } }))
    await act(async () => { resolveStatus(status) })
    expect(result.current.status?.muted).toBe(true)
    expect(result.current.settings).toEqual(settings)
    expect(result.current.events.map((item) => item.id)).toEqual(['new-event', 'alert-1'])
  })

  it('handles commands, settings and acknowledgements without production approval operations', async () => {
    const { result } = renderHook(useVoiceAttention)
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => { expect(await result.current.command('enable')).toBe(true) })
    expect(result.current.status?.enabled).toBe(true)
    await act(async () => { await result.current.acknowledge(event.id, { action: 'acknowledge', feedback: 'useful' }) })
    expect(result.current.status?.pending).toEqual([])
    expect(result.current.status?.last_alert?.acknowledged).toBe(true)
    expect(result.current.events[0].acknowledged).toBe(true)
    await act(async () => { await result.current.saveSettings({ operator_name: 'Sam' }) })
    expect(voiceAPI.configure).toHaveBeenCalledWith({ operator_name: 'Sam' }, expect.any(AbortSignal))
    expect(voiceAPI.acknowledge).toHaveBeenCalledWith(event.id, { action: 'acknowledge', feedback: 'useful' }, expect.any(AbortSignal))
  })

  it('reports fetch/mutation errors and keeps last-known state on command failure', async () => {
    vi.mocked(voiceAPI.config).mockRejectedValueOnce(new Error('Config unavailable'))
    const { result } = renderHook(useVoiceAttention)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('Config unavailable')
    expect(result.current.status).toEqual(status)
    vi.mocked(voiceAPI.command).mockRejectedValueOnce(new Error('Test failed'))
    await act(async () => { expect(await result.current.command('test')).toBe(false) })
    expect(result.current.error).toBe('Test failed')
    expect(result.current.status).toEqual(status)
    expect(result.current.busy).toBe(false)
    await act(async () => { await result.current.refresh() })
    expect(result.current.error).toBeNull()
    expect(result.current.settings).toEqual(settings)
  })

  it('prevents double submissions and aborts in-flight commands on unmount', async () => {
    let resolveCommand!: (value: typeof status) => void
    vi.mocked(voiceAPI.command).mockReturnValue(new Promise((resolve) => { resolveCommand = resolve }))
    const { result, unmount } = renderHook(useVoiceAttention)
    await waitFor(() => expect(result.current.loading).toBe(false))
    let pending!: Promise<boolean>
    act(() => { pending = result.current.command('enable') })
    expect(result.current.busy).toBe(true)
    await act(async () => { expect(await result.current.command('enable')).toBe(false) })
    expect(voiceAPI.command).toHaveBeenCalledTimes(1)
    unmount()
    expect(vi.mocked(voiceAPI.command).mock.calls[0][1]?.aborted).toBe(true)
    await act(async () => { resolveCommand(status); expect(await pending).toBe(false) })
  })

  it('cleans up StrictMode probe sockets and pending reconnect timers', async () => {
    vi.useFakeTimers()
    const { unmount } = renderHook(useVoiceAttention, { wrapper: StrictMode })
    await act(async () => {})
    expect(MockWebSocket.instances).toHaveLength(2)
    expect(MockWebSocket.instances[0].closed).toBe(true)
    act(() => MockWebSocket.instances[1].disconnect())
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(MockWebSocket.instances).toHaveLength(2)
    expect(vi.getTimerCount()).toBe(0)
  })
})