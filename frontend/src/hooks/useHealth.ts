import { useEffect, useState } from 'react'

import { apiFetch } from '@/api/client'

export type ComponentStatus = 'ok' | 'degraded' | 'down' | 'off' | 'mock'

export interface ComponentHealth {
  status: ComponentStatus
  detail: string
  [key: string]: unknown
}

export interface ReadyResponse {
  status: 'ready' | 'degraded' | 'not_ready'
  version?: string
  components: Record<string, ComponentHealth>
  checks: Record<string, string>
}

export interface HealthState {
  ready: ReadyResponse | null
  backendDown: boolean
  loading: boolean
}

/** Polls GET /health/ready (public, no inference) every `intervalMs`. */
export function useHealth(intervalMs = 5000): HealthState {
  const [ready, setReady] = useState<ReadyResponse | null>(null)
  const [backendDown, setBackendDown] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await apiFetch('/health/ready')
        if (!res.ok) throw new Error(String(res.status))
        const body = (await res.json()) as ReadyResponse
        if (!cancelled) {
          setReady(body)
          setBackendDown(false)
        }
      } catch {
        if (!cancelled) setBackendDown(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    poll()
    const id = setInterval(poll, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [intervalMs])

  return { ready, backendDown, loading }
}

export const COMPONENT_LABELS: Record<string, string> = {
  database: 'Database',
  atem: 'ATEM switcher',
  cameras: 'PTZ cameras',
  easyworship: 'EasyWorship',
  mixer: 'Mixer meters',
  audio: 'Audio capture (USB)',
  ollama: 'Ollama',
  ai_director: 'AI Director',
  slide_verification: 'Slide verification',
  voice: 'Voice attention',
}
