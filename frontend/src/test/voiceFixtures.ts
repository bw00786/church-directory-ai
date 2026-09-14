import type { VoiceEvent, VoiceSettings, VoiceStatus } from '@/api/voice'

export const event: VoiceEvent = {
  id: 'alert-1', timestamp: '2026-09-13T10:00:00Z', priority: 4,
  input: { event_type: 'hardware_failure', source: 'atem', resource: 'program', confidence: 0.98,
    operator_required: true, approval_token: 'approval-123' },
  message: 'Program output needs attention.', spoken: false, acknowledged: false,
  feedback: null, result: 'queued', resolved: false, mode: 'attention_only',
}

export const status: VoiceStatus = {
  enabled: false, mode: 'attention_only', muted: false,
  headset: { ready: true, device: 'Dedicated USB headset', verified: false, error: null },
  speech_state: 'idle', current: null, last_alert: event, pending: [event],
  cooldowns: { atem: 10 }, metrics: { spoken: 2 }, audit_available: true, error: null,
}

export const settings: VoiceSettings = {
  enabled: false, mode: 'attention_only', operator_name: 'Alex', output_device: 'Dedicated USB headset',
  output_host_api: 'CoreAudio', routing_verified: false, headset_only: true,
  church_pa: false, livestream: false, recording: false, cooldown_seconds: 30,
  repeat_interval_seconds: 60, aggregation_window_seconds: 2, max_event_age_seconds: 120, queue_limit: 20,
  persona: { gender: 'female', style: 'warm_conversational', language: 'en-US', voice_id: 'operator',
    speaking_rate: 0.94, expressiveness: 0.7, pitch: 'neutral', breathiness: 'subtle' },
}

export class MockWebSocket {
  static instances: MockWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { MockWebSocket.instances.push(this) }
  close() { this.closed = true }
  open() { this.onopen?.() }
  disconnect() { this.onclose?.() }
  receive(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }) }
}