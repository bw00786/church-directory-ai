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

interface AtemAudioChannel {
  id: number
  name: string
  muted: boolean
}

interface AtemState {
  connected: boolean
  program_input: number
  preview_input: number
  streaming: boolean
  recording: boolean
  inputs: AtemInput[]
  audio_channels: AtemAudioChannel[]
  transition_in_progress: boolean
}

export function useAtem() {
  const [state, setState] = useState<AtemState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    refreshStatus()
    const id = setInterval(refreshStatus, 3000)
    return () => clearInterval(id)
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

  const setStreaming = async (streaming: boolean) => {
    try {
      await (streaming ? atemAPI.startStream() : atemAPI.stopStream())
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change streaming state')
    }
  }

  const setRecording = async (recording: boolean) => {
    try {
      await (recording ? atemAPI.startRecording() : atemAPI.stopRecording())
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change recording state')
    }
  }

  const setMicMuted = async (micId: number, muted: boolean) => {
    try {
      await atemAPI.setMicMuted(micId, muted)
      await refreshStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change mic mute state')
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
    setStreaming,
    setRecording,
    setMicMuted,
  }
}
