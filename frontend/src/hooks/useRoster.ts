/**
 * Roster & identity memory hook
 * Manages the known-people roster plus recent face/voice recognition activity.
 */

import { useCallback, useEffect, useState } from 'react'

import {
  identityAPI,
  AudioCaptureStatus,
  IdentityObservation,
  Person,
  VoiceObservation,
} from '@/api/identity'

export function useRoster() {
  const [roster, setRoster] = useState<Person[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [observations, setObservations] = useState<IdentityObservation[]>([])
  const [audioStatus, setAudioStatus] = useState<AudioCaptureStatus | null>(null)
  const [voiceActivity, setVoiceActivity] = useState<VoiceObservation[]>([])

  const refreshRoster = useCallback(async () => {
    try {
      setLoading(true)
      const res = await identityAPI.getRoster()
      setRoster(res.data.roster)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load roster')
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshObservations = useCallback(async () => {
    try {
      const res = await identityAPI.getRecentObservations(20)
      setObservations(res.data.observations)
    } catch {
      // transient; keep last known values
    }
  }, [])

  const refreshAudio = useCallback(async () => {
    try {
      const [statusRes, activityRes] = await Promise.all([
        identityAPI.getAudioStatus(),
        identityAPI.getRecentVoiceActivity(20),
      ])
      setAudioStatus(statusRes.data)
      setVoiceActivity(activityRes.data.observations)
    } catch {
      // transient; keep last known values
    }
  }, [])

  useEffect(() => {
    refreshRoster()
    refreshObservations()
    refreshAudio()
    const id = setInterval(() => {
      refreshObservations()
      refreshAudio()
    }, 5000)
    return () => clearInterval(id)
  }, [refreshRoster, refreshObservations, refreshAudio])

  const addPerson = useCallback(
    async (name: string, role: string, notes?: string) => {
      await identityAPI.createPerson({ name, role, notes })
      await refreshRoster()
    },
    [refreshRoster],
  )

  const removePerson = useCallback(
    async (personId: string) => {
      await identityAPI.deletePerson(personId)
      await refreshRoster()
    },
    [refreshRoster],
  )

  const enrollFace = useCallback(
    async (personId: string, file: File) => {
      await identityAPI.enrollFace(personId, file)
      await refreshRoster()
    },
    [refreshRoster],
  )

  const enrollVoice = useCallback(
    async (personId: string, file: File) => {
      await identityAPI.enrollVoice(personId, file)
      await refreshRoster()
    },
    [refreshRoster],
  )

  return {
    roster,
    loading,
    error,
    observations,
    audioStatus,
    voiceActivity,
    addPerson,
    removePerson,
    enrollFace,
    enrollVoice,
    refreshRoster,
  }
}
