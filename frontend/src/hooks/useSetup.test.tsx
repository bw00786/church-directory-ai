import { act, renderHook, waitFor } from '@testing-library/react'
import type { AxiosResponse } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { setupAPI } from '@/api/setup'
import { errorDetail, useSetup } from './useSetup'

vi.mock('@/api/setup', () => ({
  setupAPI: { status: vi.fn(), discover: vi.fn(), audioDevices: vi.fn(), test: vi.fn(), saveEnv: vi.fn() },
}))

const response = <T,>(data: T) => ({ data }) as AxiosResponse<T>
const status = { enabled: true, needs_setup: true, missing: ['atem'], items: [] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(setupAPI.status).mockResolvedValue(response(status as never))
  vi.mocked(setupAPI.saveEnv).mockResolvedValue(response({ written: ['ATEM_IP'], restart_required: true, env_file: '.env' }))
})

describe('useSetup', () => {
  it('loads status once and exposes needsSetup', async () => {
    const { result } = renderHook(() => useSetup())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.needsSetup).toBe(true)
    expect(result.current.status?.missing).toEqual(['atem'])
    expect(setupAPI.status).toHaveBeenCalledTimes(1)
  })

  it('saves env values then refreshes the status', async () => {
    const { result } = renderHook(() => useSetup())
    await waitFor(() => expect(result.current.loading).toBe(false))
    vi.mocked(setupAPI.status).mockResolvedValue(response({ ...status, needs_setup: false, missing: [] } as never))
    let written: string[] = []
    await act(async () => {
      written = (await result.current.saveEnv({ ATEM_IP: '1.2.3.4' })).written
    })
    expect(written).toEqual(['ATEM_IP'])
    expect(setupAPI.saveEnv).toHaveBeenCalledWith({ ATEM_IP: '1.2.3.4' })
    expect(result.current.needsSetup).toBe(false)
  })

  it('reports the status error without throwing', async () => {
    vi.mocked(setupAPI.status).mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useSetup())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('offline')
    expect(result.current.needsSetup).toBe(false)
  })
})

describe('errorDetail', () => {
  it('prefers the backend detail and names the admin key on 403', () => {
    const forbidden = Object.assign(new Error('403'), { isAxiosError: true, response: { status: 403, data: { detail: 'nope' } } })
    const unprocessable = Object.assign(new Error('422'), { isAxiosError: true, response: { status: 422, data: { detail: 'keys not allowed: FOO' } } })
    expect(errorDetail(forbidden, 'x')).toBe('Admin API key required')
    expect(errorDetail(unprocessable, 'x')).toBe('keys not allowed: FOO')
    expect(errorDetail(new Error('boom'), 'x')).toBe('boom')
    expect(errorDetail('weird', 'fallback')).toBe('fallback')
  })
})
