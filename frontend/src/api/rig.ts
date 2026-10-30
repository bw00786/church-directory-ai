/**
 * Rig profile API client (GET/PUT /api/rig).
 * The per-church rig profile: cameras, switcher inputs, mixer roles, PTZ role
 * shots and cue-sheet overrides (docs/rig-profile.md).
 */

import client from './client'

export interface RigCamera {
  id: number
  name?: string
  driver: 'ptzoptics' | 'mock'
  host?: string | null
  port?: number
  username?: string | null
  password?: string | null // always masked ("***") in GET responses
  visca_port?: number
  visca_udp?: boolean
  atem_input: number
  snapshot_url?: string | null
  rtsp_url?: string | null
}

export interface RigRoleShot {
  camera: number
  preset: number
}

export interface RigProfile {
  version: number
  church: { name: string; timezone?: string | null; service_time: string; service_days: string[] }
  cameras: RigCamera[]
  switcher: { driver: 'atem' | 'mock'; inputs: Record<string, number>; slides_input: number }
  mixer: { driver: 'mgx16_usb' | 'meter_feed' | 'mock'; roles: Record<string, number> }
  presentation: { driver: 'easyworship' | 'mock'; host?: string | null; schedule_offset: number }
  roles: Record<string, RigRoleShot>
  cues: Array<{ id: string; enabled?: boolean; label?: string | null } & Record<string, unknown>>
  heading_synonyms: Record<string, string[]>
  follower: { cues?: string[] | null; roles?: string[] | null }
}

export interface RigSummary {
  church: string
  source: 'defaults' | 'file' | 'memory'
  path: string | null
  cameras: Array<{ id: number; name: string; driver: string; atem_input: number; host: string | null }>
  slides_input: number
  roles: Record<string, RigRoleShot>
  mixer_roles: Record<string, number>
  cue_overrides: string[]
  follower_cues: string[]
}

export interface RigResponse {
  profile: RigProfile
  summary: RigSummary
  source: RigSummary['source']
  path: string
}

export interface RigValidation {
  ok: boolean
  errors: string[]
  warnings: string[]
  summary?: RigSummary
  changed_sections?: string[]
}

export interface RigPutResult {
  ok: boolean
  path: string
  changed_sections: string[]
  applied_live: Record<string, string>
  restart_required: Record<string, string>
  warnings: string[]
  summary: RigSummary
}

export const rigAPI = {
  async get() {
    return client.get<RigResponse>('/api/rig')
  },

  async example() {
    return client.get<string>('/api/rig/example', { responseType: 'text' })
  },

  /** Dry-run validation of YAML text. */
  async validate(yaml: string) {
    return client.post<RigValidation>('/api/rig/validate', { yaml })
  },

  /** Replace the profile (admin key). Returns which sections applied live vs need a restart. */
  async put(yaml: string) {
    return client.put<RigPutResult>('/api/rig', { yaml })
  },
}

export default rigAPI
