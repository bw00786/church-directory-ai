/**
 * ATEM state hook
 * Manages ATEM status and operations
 */

import { useState, useEffect } from 'react'
import { atemAPI } from '@/api/atem'

interface AtemInput {
  id: number
  name: string
  short_name: string
  type: string
  connected: boolean
}

interface AtemState {
  connected: boolean
  program_input: number
  preview_input: number
  streaming: boolean
  recording: boolean
  inputs: AtemInput[]
  transition_in_progress: boolean
}

export function useAtem() {
  const [state, setState] = useState<AtemState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    refreshStatus()
  }, [])

  const refreshStatus = async () => {
    try {
      setLoading(true)
      const response = await atemAPI.getStatus()
      setState(response.data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to get ATEM status')
      setState(null)
    } finally {
      setLoading(false)
    }
  }

  const setProgram = async (inputId: number) => {
    try {
      await atemAPI.setProgram(inputId)
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to set program')
    }
  }

  const setPreview = async (inputId: number) => {
    try {
      await atemAPI.setPreview(inputId)
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to set preview')
    }
  }

  const performCut = async () => {
    try {
      await atemAPI.cut()
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to perform cut')
    }
  }

  const performAuto = async () => {
    try {
      await atemAPI.auto()
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to perform auto')
    }
  }

  return {
    state,
    loading,
    error,
    refreshStatus,
    setProgram,
    setPreview,
    performCut,
    performAuto,
  }
}
