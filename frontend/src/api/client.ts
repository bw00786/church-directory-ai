/**
 * Shared HTTP/WebSocket client for the control plane.
 *
 * - Base URL: VITE_API_BASE_URL. When unset, production builds talk to the
 *   same origin (the backend serves the SPA); `vite dev` falls back to the
 *   backend's default port because the dev-server proxy only covers a few
 *   paths. Set VITE_API_BASE_URL= (empty) to force same-origin in dev.
 * - Auth: the API key from localStorage ("cpd.apiKey") or VITE_API_KEY is sent
 *   as X-API-Key on every request; WebSockets get it as ?token= because
 *   browsers cannot set headers on them.
 * - 401 responses notify `onUnauthorized` listeners (see ApiKeyGate).
 */

import axios, { type AxiosError } from 'axios'

export const API_KEY_STORAGE_KEY = 'cpd.apiKey'
export const API_KEY_HEADER = 'X-API-Key'

export function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL as string | undefined
  if (configured !== undefined) return configured.replace(/\/$/, '')
  return import.meta.env.DEV ? 'http://localhost:8000' : ''
}

function storage(): Storage | null {
  try {
    const candidate = globalThis.localStorage
    return candidate && typeof candidate.getItem === 'function' ? candidate : null
  } catch {
    return null // private mode / sandboxed iframe
  }
}

export function getApiKey(): string | null {
  const stored = storage()?.getItem(API_KEY_STORAGE_KEY)
  if (stored) return stored
  const fromEnv = import.meta.env.VITE_API_KEY as string | undefined
  return fromEnv ? fromEnv : null
}

export function setApiKey(key: string | null): void {
  const store = storage()
  if (!store) return // not persisted when storage is unavailable
  if (key && key.trim()) store.setItem(API_KEY_STORAGE_KEY, key.trim())
  else store.removeItem(API_KEY_STORAGE_KEY)
}

export function authHeaders(): Record<string, string> {
  const key = getApiKey()
  return key ? { [API_KEY_HEADER]: key } : {}
}

type UnauthorizedListener = (detail: string) => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

/** Subscribe to 401s from the shared client; returns an unsubscribe function. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => {
    unauthorizedListeners.delete(listener)
  }
}

export function notifyUnauthorized(detail = 'API key required'): void {
  unauthorizedListeners.forEach((listener) => listener(detail))
}

/** Absolute URL for a backend path (absolute URLs pass through unchanged). */
export function apiUrl(path: string): string {
  return /^https?:\/\//i.test(path) ? path : `${apiBaseUrl()}${path}`
}

/** ws:// or wss:// URL for a backend WebSocket path, carrying ?token= when a key is set. */
export function wsUrl(path: string): string {
  const url = new URL(apiUrl(path), window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  const key = getApiKey()
  if (key) url.searchParams.set('token', key)
  return url.toString()
}

/** `fetch` with the API base URL, X-API-Key header, and 401 notification. */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const key = getApiKey()
  let request = init
  if (key) {
    const headers = new Headers(init?.headers)
    headers.set(API_KEY_HEADER, key)
    request = { ...init, headers }
  }
  const response = await fetch(apiUrl(path), request)
  if (response.status === 401) {
    let detail = 'API key required'
    try {
      const body = (await response.clone().json()) as { detail?: string }
      if (body?.detail) detail = body.detail
    } catch {
      // Non-JSON body; keep the default message.
    }
    notifyUnauthorized(detail)
  }
  return response
}

export const client = axios.create({ baseURL: apiBaseUrl() })

client.interceptors.request.use((config) => {
  const key = getApiKey()
  if (key) config.headers.set(API_KEY_HEADER, key)
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    if (error.response?.status === 401) notifyUnauthorized(error.response.data?.detail ?? 'API key required')
    return Promise.reject(error)
  },
)

export default client
