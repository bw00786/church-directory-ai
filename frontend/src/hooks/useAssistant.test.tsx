import { StrictMode } from 'react'
import { act, renderHook } from '@testing-library/react'
import type { AxiosResponse } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { assistantAPI, PendingAction } from '@/api/assistant'
import { useAssistant } from './useAssistant'

vi.mock('@/api/assistant', () => ({
  assistantAPI: { pending: vi.fn(), chat: vi.fn(), confirm: vi.fn(), cancel: vi.fn() },
}))

const first: PendingAction = { token: 'first', action: 'atem_stop_stream', description: 'Stop stream' }
const second: PendingAction = { token: 'second', action: 'atem_stop_recording', description: 'Stop recording' }
const response = <T,>(data: T) => ({ data }) as AxiosResponse<T>
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers()
  vi.mocked(assistantAPI.pending).mockResolvedValue(response([first, second]))
  vi.mocked(assistantAPI.confirm).mockResolvedValue(response({ ok: true }))
  vi.mocked(assistantAPI.cancel).mockResolvedValue(response({ ok: true }))
  vi.mocked(assistantAPI.chat).mockResolvedValue(response({ reply: 'Hello', pending_confirmation: null }))
})

describe('useAssistant approvals', () => {
  it('loads approvals without chat, exposes the oldest, and polls every five seconds', async () => {
    const { result, unmount } = renderHook(useAssistant)
    await act(async () => {})
    expect(result.current.pending).toEqual(first)
    expect(result.current.messages).toEqual([])
    expect(assistantAPI.chat).not.toHaveBeenCalled()
    expect(assistantAPI.confirm).not.toHaveBeenCalled()
    expect(assistantAPI.cancel).not.toHaveBeenCalled()
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([second]))
    await act(async () => { await vi.advanceTimersByTimeAsync(4999) })
    expect(assistantAPI.pending).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(result.current.pending).toEqual(second)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(assistantAPI.pending).toHaveBeenCalledTimes(2)
  })

  it('confirms and cancels sequential approvals using existing endpoints and refreshes each time', async () => {
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([second]))
    await act(async () => { await result.current.confirmPending() })
    expect(assistantAPI.confirm).toHaveBeenCalledExactlyOnceWith(first.token)
    expect(result.current.pending).toEqual(second)
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([]))
    await act(async () => { await result.current.cancelPending() })
    expect(assistantAPI.cancel).toHaveBeenCalledExactlyOnceWith(second.token)
    expect(assistantAPI.pending).toHaveBeenCalledTimes(3)
    expect(result.current.pending).toBeNull()
    expect(result.current.messages.map((item) => item.content)).toEqual(['Confirmed: Stop stream', 'Cancelled: Stop recording'])
  })

  it.each([
    [{ ok: false, error: 'Permission denied' }, 'Permission denied'],
    [{ ok: false }, 'Failed to confirm action'],
  ])('does not report Confirmed for an unsuccessful HTTP 200 result: %j', async (outcome, message) => {
    vi.mocked(assistantAPI.confirm).mockResolvedValue(response(outcome))
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([second]))
    await act(async () => { await result.current.confirmPending() })
    expect(result.current.error).toBe(message)
    expect(result.current.messages).toEqual([])
    expect(result.current.pending).toEqual(second)
    expect(assistantAPI.pending).toHaveBeenCalledTimes(2)
    expect(result.current.resolving).toBe(false)
  })

  it.each(['confirmPending', 'cancelPending'] as const)('refreshes after %s fails with a stale token or transport error', async (method) => {
    vi.mocked(assistantAPI.confirm).mockRejectedValue(new Error('Unknown confirmation token'))
    vi.mocked(assistantAPI.cancel).mockRejectedValue(new Error('Unknown confirmation token'))
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([second]))
    await act(async () => { await result.current[method]() })
    expect(result.current.error).toBe('Unknown confirmation token')
    expect(result.current.messages).toEqual([])
    expect(result.current.pending).toEqual(second)
    expect(assistantAPI.pending).toHaveBeenCalledTimes(2)
  })

  it('blocks duplicate and competing submissions through the post-action refresh', async () => {
    const confirmation = deferred<Awaited<ReturnType<typeof assistantAPI.confirm>>>()
    const refresh = deferred<Awaited<ReturnType<typeof assistantAPI.pending>>>()
    vi.mocked(assistantAPI.confirm).mockReturnValue(confirmation.promise)
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    const staleConfirm = result.current.confirmPending
    vi.mocked(assistantAPI.pending).mockReturnValue(refresh.promise)
    let operation!: Promise<void>
    act(() => { operation = result.current.confirmPending() })
    expect(result.current.resolving).toBe(true)
    await act(async () => {
      await result.current.confirmPending()
      await result.current.cancelPending()
      await vi.advanceTimersByTimeAsync(5000)
      confirmation.resolve(response({ ok: true }))
    })
    expect(assistantAPI.pending).toHaveBeenCalledTimes(2)
    await act(async () => { await result.current.confirmPending() })
    expect(result.current.resolving).toBe(true)
    await act(async () => { refresh.resolve(response([second])); await operation })
    await act(async () => { await staleConfirm() })
    expect(assistantAPI.confirm).toHaveBeenCalledTimes(1)
    expect(assistantAPI.cancel).not.toHaveBeenCalled()
    expect(result.current.resolving).toBe(false)
    expect(result.current.pending).toEqual(second)
  })

  it('ignores a stale poll arriving after an action and its refresh', async () => {
    const poll = deferred<Awaited<ReturnType<typeof assistantAPI.pending>>>()
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    vi.mocked(assistantAPI.pending).mockReturnValueOnce(poll.promise)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([second]))
    await act(async () => { await result.current.confirmPending() })
    await act(async () => { poll.resolve(response([first, second])) })
    expect(result.current.pending).toEqual(second)
    expect(result.current.messages).toHaveLength(1)
  })

  it('preserves chat history and does not hide older approvals with a new chat proposal or null response', async () => {
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    vi.mocked(assistantAPI.chat).mockResolvedValueOnce(response({ reply: 'Please confirm', pending_confirmation: second }))
    await act(async () => { await result.current.send('  stop recording  ') })
    expect(assistantAPI.chat).toHaveBeenLastCalledWith([{ role: 'user', content: 'stop recording' }])
    expect(result.current.pending).toEqual(first)
    await act(async () => { await result.current.send('hello') })
    expect(assistantAPI.chat).toHaveBeenLastCalledWith([
      { role: 'user', content: 'stop recording' }, { role: 'assistant', content: 'Please confirm' },
      { role: 'user', content: 'hello' },
    ])
    expect(result.current.pending).toEqual(first)
    expect(result.current.messages[result.current.messages.length - 1]?.content).toBe('Hello')
  })

  it('still surfaces chat proposals when pending discovery fails and recovers on polling', async () => {
    vi.mocked(assistantAPI.pending).mockRejectedValue(new Error('Pending unavailable'))
    vi.mocked(assistantAPI.chat).mockResolvedValue(response({ reply: 'Please confirm', pending_confirmation: first }))
    const { result } = renderHook(useAssistant)
    await act(async () => { await result.current.send('stop stream') })
    expect(result.current.pending).toEqual(first)
    expect(result.current.error).toBe('Pending unavailable')
    vi.mocked(assistantAPI.pending).mockResolvedValue(response([]))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.pending).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('preserves execution errors even when the follow-up refresh fails', async () => {
    const { result } = renderHook(useAssistant)
    await act(async () => {})
    vi.mocked(assistantAPI.confirm).mockResolvedValue(response({ ok: false, error: 'Permission denied' }))
    vi.mocked(assistantAPI.pending).mockRejectedValue(new Error('Pending unavailable'))
    await act(async () => { await result.current.confirmPending() })
    expect(result.current.error).toBe('Permission denied')
    expect(result.current.messages).toEqual([])
    expect(result.current.pending).toEqual(second)
  })

  it('cleans up StrictMode polling and ignores old mount snapshots', async () => {
    const initial = deferred<Awaited<ReturnType<typeof assistantAPI.pending>>>()
    vi.mocked(assistantAPI.pending).mockReturnValueOnce(initial.promise)
    const { result, unmount } = renderHook(useAssistant, { wrapper: StrictMode })
    await act(async () => {})
    expect(assistantAPI.pending).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(1)
    await act(async () => { initial.resolve(response([])) })
    expect(result.current.pending).toEqual(first)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})