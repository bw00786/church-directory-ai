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
}

export const productionAPI = {
  async getStatus() {
    return client.get('/production')
  },
}

export default client
