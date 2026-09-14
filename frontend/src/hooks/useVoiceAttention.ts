import { useCallback, useEffect, useRef, useState } from 'react'
import {
  voiceAPI, voiceSocketURL, type VoiceAcknowledgement, type VoiceCommand, type VoiceEvent,
  type VoiceSettings, type VoiceSettingsUpdate, type VoiceSocketMessage, type VoiceStatus,
} from '@/api/voice'

const FALLBACK_MS = 5000
const messageTypes = new Set(['voice_attention', 'voice_status', 'voice_queue', 'voice_acknowledgement'])
const describeError = (error: unknown) => error instanceof Error ? error.message : 'Voice service unavailable.'
const mergeEvent = (events: VoiceEvent[], event: VoiceEvent) =>
  [event, ...events.filter((item) => item.id !== event.id)]
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)).slice(0, 100)

export function useVoiceAttention() {
  const [status, setStatus] = useState<VoiceStatus | null>(null)
  const [settings, setSettings] = useState<VoiceSettings | null>(null)
  const [events, setEvents] = useState<VoiceEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const lifecycle = useRef<AbortController | null>(null)
  const refreshRef = useRef<() => Promise<void>>(async () => {})
  const revision = useRef(0)
  const configRevision = useRef(0)
  const locked = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    lifecycle.current = controller
    let socket: WebSocket | null = null
    let reconnect: ReturnType<typeof setTimeout> | undefined
    let live = false
    let refreshing = false
    const active = () => !controller.signal.aborted

    const refresh = async () => {
      if (!active() || refreshing) return
      refreshing = true
      const version = revision.current
      const configVersion = configRevision.current
      const results = await Promise.allSettled([
        voiceAPI.status(controller.signal), voiceAPI.events(controller.signal), voiceAPI.config(controller.signal),
      ])
      refreshing = false
      if (!active()) return
      const [statusResult, eventsResult, configResult] = results
      // A slow snapshot must not overwrite a newer WS event or operator response.
      if (version === revision.current) {
        if (statusResult.status === 'fulfilled') setStatus(statusResult.value)
      }
      if (eventsResult.status === 'fulfilled') setEvents((previous) => {
        const snapshot = eventsResult.value.events
        const merged = version === revision.current ? snapshot :
          [...new Map([...snapshot, ...previous].map((event) => [event.id, event])).values()]
        return merged.slice().sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)).slice(0, 100)
      })
      if (configVersion === configRevision.current && configResult.status === 'fulfilled') setSettings(configResult.value)
      const failed = results.find((result) => result.status === 'rejected')
      setError(failed?.status === 'rejected' ? describeError(failed.reason) : null)
      setLoading(false)
    }
    refreshRef.current = refresh

    const connect = () => {
      if (!active()) return
      const retry = () => {
        if (!active()) return
        live = false
        setConnected(false)
        if (reconnect === undefined) reconnect = setTimeout(() => {
          reconnect = undefined
          connect()
        }, FALLBACK_MS)
      }
      try {
        const ws = new WebSocket(voiceSocketURL())
        socket = ws
        ws.onopen = () => { if (active()) void refresh() }
        ws.onmessage = ({ data }) => {
          if (!active() || socket !== ws) return
          try {
            const message = JSON.parse(data) as VoiceSocketMessage
            if (!messageTypes.has(message.type) || !message.status || !Array.isArray(message.status.pending)) return
            live = true
            setConnected(true)
            revision.current += 1
            setStatus(message.status)
            if (message.event) setEvents((previous) => mergeEvent(previous, message.event!))
          } catch {
            // Ignore unrelated/malformed frames; do not interrupt HTTP fallback.
          }
        }
        ws.onclose = retry
        ws.onerror = () => {
          retry()
          ws.close()
        }
      } catch {
        retry()
      }
    }

    void refresh()
    connect()
    const poll = setInterval(() => { if (!live) void refresh() }, FALLBACK_MS)
    return () => {
      controller.abort()
      clearInterval(poll)
      clearTimeout(reconnect)
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null
        socket.close()
      }
    }
  }, [])

  const run = useCallback(async (operation: (signal: AbortSignal) => Promise<void>): Promise<boolean> => {
    const controller = lifecycle.current
    if (!controller || controller.signal.aborted || locked.current) return false
    locked.current = true
    setBusy(true)
    setError(null)
    revision.current += 1
    try {
      await operation(controller.signal)
      return !controller.signal.aborted
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure))
      return false
    } finally {
      locked.current = false
      if (!controller.signal.aborted) setBusy(false)
    }
  }, [])

  const command = useCallback((name: VoiceCommand) => run(async (signal) => {
    const next = await voiceAPI.command(name, signal)
    if (!signal.aborted) { revision.current += 1; setStatus(next) }
  }), [run])

  const saveSettings = useCallback((update: VoiceSettingsUpdate) => run(async (signal) => {
    configRevision.current += 1
    const next = await voiceAPI.configure(update, signal)
    if (!signal.aborted) {
      revision.current += 1
      configRevision.current += 1
      setSettings(next)
      await refreshRef.current()
    }
  }), [run])

  const acknowledge = useCallback((id: string, acknowledgement: VoiceAcknowledgement) => run(async (signal) => {
    const event = await voiceAPI.acknowledge(id, acknowledgement, signal)
    if (signal.aborted) return
    revision.current += 1
    setEvents((previous) => mergeEvent(previous, event))
    setStatus((previous) => previous && ({
      ...previous,
      current: previous.current?.id === id ? event : previous.current,
      last_alert: previous.last_alert?.id === id ? event : previous.last_alert,
      pending: previous.pending.flatMap((item) => item.id !== id ? [item] :
        event.acknowledged || event.resolved ? [] : [event]),
    }))
  }), [run])

  return { status, settings, events, loading, connected, error, busy, command, saveSettings, acknowledge,
    refresh: useCallback(() => refreshRef.current(), []) }
}