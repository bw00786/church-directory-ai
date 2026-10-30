/**
 * First-run setup wizard API (/api/setup).
 */

import client from './client'

export interface SetupItem {
  key: string
  label: string
  configured: boolean
  required: boolean
  missing: boolean
  mock: boolean | null
  value: string | null
  hint: string
}

export interface SetupStatus {
  enabled: boolean
  needs_setup: boolean
  missing: string[]
  all_mocked: boolean
  mocks: { atem: boolean; mixer: boolean; easyworship: boolean }
  api_host: string
  api_loopback: boolean
  keys: { operator: boolean; admin: boolean; auth_enabled: boolean }
  env_file: string
  items: SetupItem[]
}

export type DiscoverKind = 'atem' | 'easyworship' | 'ollama'

export interface DiscoverResult {
  found: string | { host: string; port: number; name: string } | null
  detail: string
  base_url?: string
  models?: string[]
  subnets?: string
}

export interface AudioDevice {
  index: number
  name: string
  max_input_channels: number
  default_samplerate: number | null
  hostapi: string | null
  matches_configured: boolean
}

export interface AudioDevicesResponse {
  devices: AudioDevice[]
  note: string | null
  configured_name?: string
}

export type TestComponent = 'atem' | 'camera' | 'easyworship' | 'ollama' | 'mixer' | 'database'

export interface TestRequest {
  host?: string
  port?: number
  username?: string
  password?: string
  visca_udp?: boolean
  device_name?: string
}

export interface TestResult {
  component: string
  ok: boolean
  detail: string
  mock?: boolean
  models?: string[]
}

export type EnvValues = Record<string, string | number | boolean>

export interface EnvWriteResult {
  written: string[]
  restart_required: boolean
  env_file: string
}

export const setupAPI = {
  async status() {
    return client.get<SetupStatus>('/api/setup/status')
  },
  async discover(kind: DiscoverKind) {
    // Discovery sweeps a subnet; allow for the backend's bounded timeout.
    return client.post<DiscoverResult>(`/api/setup/discover/${kind}`, undefined, { timeout: 45_000 })
  },
  async audioDevices() {
    return client.get<AudioDevicesResponse>('/api/setup/audio-devices')
  },
  async test(component: TestComponent, body: TestRequest = {}) {
    return client.post<TestResult>(`/api/setup/test/${component}`, body, { timeout: 20_000 })
  },
  async saveEnv(values: EnvValues) {
    return client.put<EnvWriteResult>('/api/setup/env', { values })
  },
}
