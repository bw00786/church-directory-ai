import { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  API_KEY_STORAGE_KEY, apiFetch, authHeaders, client, getApiKey, notifyUnauthorized, onUnauthorized, setApiKey, wsUrl,
} from './client'

type Handler = { fulfilled: (config: InternalAxiosRequestConfig) => InternalAxiosRequestConfig }
const requestHandlers = (client.interceptors.request as unknown as { handlers: Handler[] }).handlers

function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() { return data.size },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => { data.delete(key) },
    setItem: (key: string, value: string) => { data.set(key, value) },
  }
}

let store: Storage
// setup.ts unstubs globals after each test, so re-stub a fresh in-memory Storage every time.
beforeEach(() => { store = memoryStorage(); vi.stubGlobal('localStorage', store) })
afterEach(() => { store.clear() })

describe('API key storage', () => {
  it('reads the key from localStorage and clears it', () => {
    expect(getApiKey()).toBeNull()
    setApiKey('  operator-key  ')
    expect(store.getItem(API_KEY_STORAGE_KEY)).toBe('operator-key')
    expect(authHeaders()).toEqual({ 'X-API-Key': 'operator-key' })
    setApiKey(null)
    expect(getApiKey()).toBeNull()
    expect(authHeaders()).toEqual({})
  })
})

describe('axios client header injection', () => {
  it('adds X-API-Key only when a key is present', () => {
    const run = () => requestHandlers[0].fulfilled({ headers: new AxiosHeaders() } as InternalAxiosRequestConfig)
    expect(run().headers.get('X-API-Key')).toBeUndefined()
    setApiKey('operator-key')
    expect(run().headers.get('X-API-Key')).toBe('operator-key')
  })
})

describe('apiFetch', () => {
  it('prefixes the base URL and sends the key header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 200, ok: true })
    vi.stubGlobal('fetch', fetchMock)
    setApiKey('operator-key')
    await apiFetch('/atem/status', { method: 'POST' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/atem/status')
    expect(init.method).toBe('POST')
    expect((init.headers as Headers).get('X-API-Key')).toBe('operator-key')
  })

  it('passes the request through untouched without a key', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 200, ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const init = { method: 'GET' }
    await apiFetch('/atem/status', init)
    expect(fetchMock.mock.calls[0][1]).toBe(init)
  })

  it('notifies listeners on 401 with the backend detail', async () => {
    const response = { status: 401, ok: false, clone: () => ({ json: async () => ({ detail: 'Invalid API key' }) }) }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
    const listener = vi.fn()
    const unsubscribe = onUnauthorized(listener)
    await apiFetch('/atem/status')
    expect(listener).toHaveBeenCalledExactlyOnceWith('Invalid API key')
    unsubscribe()
    notifyUnauthorized('again')
    expect(listener).toHaveBeenCalledTimes(1)
  })
})

describe('wsUrl', () => {
  it('builds a ws:// URL without a token when no key is set', () => {
    expect(wsUrl('/ws/director')).toBe('ws://localhost:8000/ws/director')
  })

  it('appends ?token= when a key is set', () => {
    setApiKey('operator-key')
    expect(wsUrl('/ws/cameras/1/joystick')).toBe('ws://localhost:8000/ws/cameras/1/joystick?token=operator-key')
  })
})
