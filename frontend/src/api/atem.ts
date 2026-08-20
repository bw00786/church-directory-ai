/**
 * ATEM API client
 * Communicates with FastAPI backend
 */

import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const client = axios.create({
  baseURL: API_BASE,
})

export const atemAPI = {
  async getStatus() {
    return client.get('/atem/status')
  },

  async connect() {
    return client.post('/atem/connect')
  },

  async disconnect() {
    return client.post('/atem/disconnect')
  },

  async setProgram(inputId: number) {
    return client.post('/atem/program', { input_id: inputId })
  },

  async setPreview(inputId: number) {
    return client.post('/atem/preview', { input_id: inputId })
  },

  async cut() {
    return client.post('/atem/cut')
  },

  async auto() {
    return client.post('/atem/auto')
  },

  async startStream() {
    return client.post('/atem/stream/start')
  },

  async stopStream() {
    return client.post('/atem/stream/stop')
  },

  async startRecording() {
    return client.post('/atem/record/start')
  },

  async stopRecording() {
    return client.post('/atem/record/stop')
  },

  async setMicMuted(micId: number, muted: boolean) {
    return client.post(`/atem/mic/${micId}/mute`, { muted })
  },
}

export const cameraAPI = {
  async listCameras() {
    return client.get('/cameras')
  },

  async getCameraState(cameraId: number) {
    return client.get(`/cameras/${cameraId}`)
  },

  async moveToPreset(cameraId: number, presetId: number) {
    return client.post(`/cameras/${cameraId}/preset/${presetId}`)
  },

  async savePreset(cameraId: number, presetId: number) {
    return client.post(`/cameras/${cameraId}/preset/${presetId}/save`)
  },

  async stop(cameraId: number) {
    return client.post(`/cameras/${cameraId}/stop`)
  },
}

export const productionAPI = {
  async getStatus() {
    return client.get('/production')
  },
}

export const directorAPI = {
  async getStatus() {
    return client.get('/director/status')
  },
  async getScript() {
    return client.get('/director/script')
  },

  async start(autonomous = true) {
    return client.post('/director/start', { autonomous })
  },

  async stop() {
    return client.post('/director/stop')
  },

  async next() {
    return client.post('/director/next')
  },

  async goto(index: number) {
    return client.post(`/director/goto/${index}`)
  },

  async getSchedule() {
    return client.get('/director/schedule')
  },

  async setSchedule(payload: {
    enabled?: boolean
    time?: string
    days?: string
    autonomous?: boolean
  }) {
    return client.post('/director/schedule', payload)
  },
}

export const easyworshipAPI = {
  async getStatus() {
    return client.get('/easyworship/status')
  },

  async action(name: string) {
    return client.post(`/easyworship/action/${name}`)
  },

  async next() {
    return client.post('/easyworship/next')
  },

  async previous() {
    return client.post('/easyworship/previous')
  },
}

export interface VisionTrack {
  person_id: number
  bbox: [number, number, number, number]
  confidence: number
  position: string
}

export interface VisionIdentityMatch {
  person_id: string | null
  name: string
  role: string | null
  confidence: number
  live?: boolean
}

export interface VisionCameraTracks {
  tracks: VisionTrack[]
  identities: VisionIdentityMatch[]
  frame_size: { width: number; height: number } | null
}

export interface VisionCameraQuality {
  camera_id: number
  overall_score: number
  shot: string
  subject_count: number
}

export interface VisionRecommendation {
  recommended_camera: number
  score: number
  reason: string
  current_program: number
}

export interface VisionEvent {
  id: string
  timestamp: number
  type: string
  camera_id: number
  confidence: number
  payload: Record<string, unknown>
}

export const visionAPI = {
  async getStatus() {
    return client.get('/api/vision/status')
  },

  async getCameras() {
    return client.get<{ cameras: VisionCameraQuality[] }>('/api/vision/cameras')
  },

  async getCameraTracks(cameraId: number) {
    return client.get<VisionCameraTracks>(`/api/vision/cameras/${cameraId}/tracks`)
  },

  async getEvents() {
    return client.get<{ events: VisionEvent[] }>('/api/vision/events')
  },

  async getRecommendations() {
    return client.get<{ recommendations: VisionRecommendation[] }>('/api/vision/recommendations')
  },
}

export default client
