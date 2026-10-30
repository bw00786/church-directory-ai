/**
 * Rig profile hook: loads GET /api/rig once (refresh on demand) and exposes
 * validate/save for an admin editor.
 */

import { useCallback, useEffect, useState } from 'react'
import type { AxiosError } from 'axios'

import { rigAPI, type RigPutResult, type RigResponse, type RigValidation } from '@/api/rig'

function errorMessage(e: unknown, fallback: string): string {
  const axiosError = e as AxiosError<{ detail?: string | { errors?: string[] } }>
  const detail = axiosError?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && Array.isArray(detail.errors)) return detail.errors.join('\n')
  return e instanceof Error ? e.message : fallback
}

export function useRig() {
  const [rig, setRig] = useState<RigResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setLoading(true)
      const response = await rigAPI.get()
      setRig(response.data)
      setError(null)
    } catch (e) {
      setError(errorMessage(e, 'Failed to load the rig profile'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const validate = useCallback(async (yaml: string): Promise<RigValidation> => {
    try {
      const response = await rigAPI.validate(yaml)
      return response.data
    } catch (e) {
      return { ok: false, errors: [errorMessage(e, 'Validation request failed')], warnings: [] }
    }
  }, [])

  const save = useCallback(
    async (yaml: string): Promise<RigPutResult> => {
      const response = await rigAPI.put(yaml)
      await refresh()
      return response.data
    },
    [refresh],
  )

  return { rig, summary: rig?.summary ?? null, loading, error, refresh, validate, save }
}

export default useRig
