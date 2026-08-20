/**
 * Identity API client
 * Roster management (face/voice enrollment) and recognition observations.
 */

import client from './atem'

export interface Person {
  id: string
  name: string
  role: string
  notes: string | null
  appearance_count: number
  last_seen_at: string | null
  created_at: string | null
}

export interface IdentityObservation {
  id: string
  timestamp: string
  modality: 'face' | 'voice'
  person_id: string | null
  person_name: string | null
  role: string | null
  confidence: number
  source: string
  detail: string | null
}

export interface AudioCaptureStatus {
  available: boolean
  enabled: boolean
  running: boolean
  device: string | null
  sample_rate: number
  window_seconds: number
  channel_name: string
}

export interface VoiceObservation {
  person_id: string | null
  name: string | null
  role: string | null
  confidence: number
  activity: string
  is_known: boolean
  is_new_provisional_speaker?: boolean
  timestamp?: number
}

export const identityAPI = {
  async getRoster() {
    return client.get<{ roster: Person[] }>('/api/identity/roster')
  },

  async createPerson(payload: { name: string; role: string; notes?: string }) {
    return client.post<Person>('/api/identity/roster', payload)
  },

  async deletePerson(personId: string) {
    return client.delete(`/api/identity/roster/${personId}`)
  },

  async enrollFace(personId: string, file: File) {
    const formData = new FormData()
    formData.append('file', file)
    return client.post(`/api/identity/roster/${personId}/faces`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  async enrollVoice(personId: string, file: File) {
    const formData = new FormData()
    formData.append('file', file)
    return client.post(`/api/identity/roster/${personId}/voice`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  async getRecentObservations(limit = 20) {
    return client.get<{ observations: IdentityObservation[] }>('/api/identity/observations/recent', {
      params: { limit },
    })
  },

  async getAudioStatus() {
    return client.get<AudioCaptureStatus>('/api/identity/audio/status')
  },

  async getRecentVoiceActivity(limit = 20) {
    return client.get<{ observations: VoiceObservation[] }>('/api/identity/audio/recent', {
      params: { limit },
    })
  },
}

export default identityAPI
