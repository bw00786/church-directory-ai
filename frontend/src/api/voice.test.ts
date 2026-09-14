import { beforeEach, describe, expect, it, vi } from 'vitest'
import { voiceAPI, voiceSocketURL, type VoiceCommand, type VoiceSettingsUpdate } from './voice'
import { event, settings, status } from '@/test/voiceFixtures'

const fetchMock = vi.fn()
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset() })

describe('voice API contract', () => {
  it('reads status, history and configuration at the contracted paths', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => status })
    await voiceAPI.status()
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ events: [event] }) })
    await voiceAPI.events()
    fetchMock.mockResolvedValue({ ok: true, json: async () => settings })
    await voiceAPI.config()
    expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      '/api/voice/status', '/api/voice/events', '/api/voice/config',
    ])
  })

  it.each<VoiceCommand>(['enable', 'disable', 'mute', 'unmute', 'repeat', 'test'])('posts %s without browser audio or extra payload', async (command) => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => status })
    expect(await voiceAPI.command(command)).toEqual(status)
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining(`/api/voice/${command}`), {
      method: 'POST', signal: undefined,
    })
  })

  it('strips environment routing and unrelated fields from configuration updates', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => settings })
    await voiceAPI.configure({ mode: 'testing', operator_name: 'Sam', output_device: 'PA',
      routing_verified: true, church_pa: true, enabled: true, queue_limit: 999,
      persona: { speaking_rate: 0.95 },
    } as VoiceSettingsUpdate)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      mode: 'testing', operator_name: 'Sam', persona: { speaking_rate: 0.95 },
    })
  })

  it('acknowledges with optional feedback and URL-encodes the event ID; never sends approval tokens', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => event })
    await voiceAPI.acknowledge('id/1', { action: 'dismiss', feedback: 'too_late' })
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/voice/events/id%2F1/ack'), expect.objectContaining({
      method: 'POST', body: JSON.stringify({ action: 'dismiss', feedback: 'too_late' }),
    }))
  })

  it('rejects non-successful responses and propagates cancellation', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503, statusText: 'Unavailable' })
    const controller = new AbortController()
    await expect(voiceAPI.status(controller.signal)).rejects.toThrow('503 Unavailable')
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal)
  })

  it('uses the backend websocket path', () => {
    expect(voiceSocketURL()).toBe('ws://localhost:8000/ws/voice')
  })
})