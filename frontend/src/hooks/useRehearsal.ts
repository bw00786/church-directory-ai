/**
 * Rehearsal mode: sample list, start/stop, and status polling (faster while running).
 */

import { useCallback, useEffect, useState } from 'react'
import { isAxiosError } from 'axios'

import { rehearsalAPI, type RehearsalSample, type RehearsalStartRequest, type RehearsalStatus } from '@/api/rehearsal'

export function useRehearsal(idleMs = 5000, runningMs = 1000) {
  const [status, setStatus] = useState<RehearsalStatus | null>(null)
  const [samples, setSamples] = useState<RehearsalSample[]>([])
  const [allowed, setAllowed] = useState<{ allowed: boolean; reason: string }>({ allowed: true, reason: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const { data } = await rehearsalAPI.status()
      setStatus(data)
    } catch {
      // Keep the last known status when the backend is briefly unreachable.
    }
  }, [])

  const loadSamples = useCallback(async () => {
    try {
      const { data } = await rehearsalAPI.samples()
      setSamples(data.samples)
      setAllowed({ allowed: data.allowed, reason: data.reason })
    } catch {
      setSamples([])
    }
  }, [])

  useEffect(() => {
    refresh()
    loadSamples()
  }, [refresh, loadSamples])

  const running = Boolean(status?.running)
  useEffect(() => {
    const id = setInterval(refresh, running ? runningMs : idleMs)
    return () => clearInterval(id)
  }, [refresh, running, runningMs, idleMs])

  const act = useCallback(
    async (request: () => Promise<{ data: RehearsalStatus }>) => {
      setBusy(true)
      try {
        const { data } = await request()
        setStatus(data)
        setError(null)
      } catch (err) {
        const detail = isAxiosError<{ detail?: string }>(err) ? err.response?.data?.detail : undefined
        setError(detail ?? (err instanceof Error ? err.message : 'Rehearsal request failed'))
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  const start = useCallback((body: RehearsalStartRequest) => act(() => rehearsalAPI.start(body)), [act])
  const stop = useCallback(() => act(() => rehearsalAPI.stop()), [act])

  return { status, samples, allowed, error, busy, start, stop, refresh, loadSamples }
}
