import { beforeEach, describe, expect, it, vi } from 'vitest'
import { assistantAPI } from './assistant'

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('axios', () => ({ default: { create: vi.fn(() => client) } }))

beforeEach(() => { vi.clearAllMocks() })

describe('assistant API timeouts', () => {
  it('allows the Ollama inference budget plus transport overhead for chat', async () => {
    const messages = [{ role: 'user' as const, content: 'Who preached last Sunday?' }]
    client.post.mockResolvedValueOnce({ data: { reply: 'No history found.', pending_confirmation: null } })
    await assistantAPI.chat(messages)
    expect(client.post).toHaveBeenCalledExactlyOnceWith(
      '/api/assistant/chat', { messages }, { timeout: 130_000 },
    )
  })

  it('does not apply the inference timeout to pending, confirm or cancel requests', async () => {
    await assistantAPI.pending()
    await assistantAPI.confirm('approval-1')
    await assistantAPI.cancel('approval-2')
    expect(client.get).toHaveBeenCalledExactlyOnceWith('/api/assistant/pending')
    expect(client.post.mock.calls).toEqual([
      ['/api/assistant/confirm/approval-1'], ['/api/assistant/cancel/approval-2'],
    ])
  })

  it('propagates a timeout without retrying a production-capable chat request', async () => {
    client.post.mockRejectedValueOnce(new Error('timeout exceeded'))
    await expect(assistantAPI.chat([])).rejects.toThrow('timeout exceeded')
    expect(client.post).toHaveBeenCalledTimes(1)
  })
})