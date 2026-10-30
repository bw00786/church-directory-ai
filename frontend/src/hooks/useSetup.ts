/**
 * Setup wizard state: GET /api/setup/status once (and on demand), plus the
 * discovery / test / save actions. `needsSetup` drives the auto-open wizard.
 */

import { useCallback, useEffect, useState } from 'react'
import { isAxiosError } from 'axios'

import {
  setupAPI,
  type AudioDevicesResponse,
  type DiscoverKind,
  type DiscoverResult,
  type EnvValues,
  type EnvWriteResult,
  type SetupStatus,
  type TestComponent,
  type TestRequest,
  type TestResult,
} from '@/api/setup'

export function errorDetail(error: unknown, fallback: string): string {
  if (isAxiosError<{ detail?: string }>(error)) {
    if (error.response?.status === 403) return 'Admin API key required'
    return error.response?.data?.detail ?? error.message ?? fallback
  }
  return error instanceof Error ? error.message : fallback
}

export function useSetup() {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const { data } = await setupAPI.status()
      setStatus(data)
      setError(null)
    } catch (err) {
      setError(errorDetail(err, 'Setup status unavailable'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const discover = useCallback(async (kind: DiscoverKind): Promise<DiscoverResult> => {
    const { data } = await setupAPI.discover(kind)
    return data
  }, [])

  const test = useCallback(async (component: TestComponent, body: TestRequest = {}): Promise<TestResult> => {
    const { data } = await setupAPI.test(component, body)
    return data
  }, [])

  const audioDevices = useCallback(async (): Promise<AudioDevicesResponse> => {
    const { data } = await setupAPI.audioDevices()
    return data
  }, [])

  const saveEnv = useCallback(
    async (values: EnvValues): Promise<EnvWriteResult> => {
      const { data } = await setupAPI.saveEnv(values)
      await refresh()
      return data
    },
    [refresh],
  )

  return {
    status,
    loading,
    error,
    needsSetup: Boolean(status?.needs_setup),
    refresh,
    discover,
    test,
    audioDevices,
    saveEnv,
  }
}
