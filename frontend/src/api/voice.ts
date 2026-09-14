const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export type VoiceMode = 'off' | 'attention_only' | 'testing' | 'emergency'
export type VoiceFeedback = 'useful' | 'not_useful' | 'too_sensitive' | 'too_late' | 'correct' | 'incorrect'
export type VoiceCommand = 'enable' | 'disable' | 'mute' | 'unmute' | 'repeat' | 'test'

export interface VoiceEvent {
  id: string
  timestamp: string
  input: {
    event_type: string
    source: string
    resource: string
    confidence: number
    operator_required: boolean
    approval_token?: string | null
  }
  priority: 0 | 1 | 2 | 3 | 4
  message: string
  spoken: boolean
  acknowledged: boolean
  feedback?: string | null
  result: string
  resolved: boolean
  mode: VoiceMode
}

export interface VoiceStatus {
  enabled: boolean
  mode: VoiceMode
  muted: boolean
  headset: { ready: boolean; device: string; verified: boolean; error: string | null }
  speech_state: 'idle' | 'synthesizing' | 'playing'
  current: VoiceEvent | null
  last_alert: VoiceEvent | null
  pending: VoiceEvent[]
  cooldowns: Record<string, number>
  metrics: Record<string, number>
  audit_available: boolean
  error: string | null
}

export interface VoiceSettings {
  enabled: boolean
  mode: VoiceMode
  operator_name: string
  output_device: string
  output_host_api: string
  routing_verified: boolean
  headset_only: true
  church_pa: false
  livestream: false
  recording: false
  cooldown_seconds: number
  repeat_interval_seconds: number
  aggregation_window_seconds: number
  max_event_age_seconds: number
  queue_limit: number
  persona: {
    gender: 'female'
    style: 'warm_conversational'
    language: 'en-US'
    voice_id: string
    speaking_rate: number
    expressiveness: number
    pitch: 'neutral'
    breathiness: 'subtle'
  }
}

export type VoiceSettingsUpdate = Partial<Pick<VoiceSettings,
  'mode' | 'operator_name' | 'cooldown_seconds' | 'repeat_interval_seconds' | 'aggregation_window_seconds'
>> & { persona?: Partial<VoiceSettings['persona']> }

export interface VoiceAcknowledgement {
  action: 'acknowledge' | 'dismiss'
  feedback?: VoiceFeedback
}

export interface VoiceSocketMessage {
  type: 'voice_attention' | 'voice_status' | 'voice_queue' | 'voice_acknowledgement'
  status: VoiceStatus
  event?: VoiceEvent
}

async function request<T>(path: string, signal?: AbortSignal, body?: unknown, post = false): Promise<T> {
  const response = await fetch(`${API_BASE}/api/voice${path}`, {
    method: post ? 'POST' : 'GET',
    signal,
    ...(body === undefined ? {} : {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  })
  if (!response.ok) throw new Error(`Voice request failed (${response.status} ${response.statusText}).`)
  return response.json() as Promise<T>
}

export function voiceSocketURL(): string {
  const url = new URL(`${API_BASE}/ws/voice`, window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export const voiceAPI = {
  status: (signal?: AbortSignal) => request<VoiceStatus>('/status', signal),
  events: (signal?: AbortSignal) => request<{ events: VoiceEvent[] }>('/events', signal),
  config: (signal?: AbortSignal) => request<VoiceSettings>('/config', signal),
  command: (command: VoiceCommand, signal?: AbortSignal) => request<VoiceStatus>(`/${command}`, signal, undefined, true),
  configure: (update: VoiceSettingsUpdate, signal?: AbortSignal) => {
    // Explicit allowlist: never send environment-owned hardware routing fields.
    const { mode, operator_name, cooldown_seconds, repeat_interval_seconds, aggregation_window_seconds, persona } = update
    return request<VoiceSettings>('/config', signal, {
      mode, operator_name, cooldown_seconds, repeat_interval_seconds, aggregation_window_seconds, persona,
    }, true)
  },
  acknowledge: (id: string, acknowledgement: VoiceAcknowledgement, signal?: AbortSignal) =>
    request<VoiceEvent>(`/events/${encodeURIComponent(id)}/ack`, signal, acknowledgement, true),
}