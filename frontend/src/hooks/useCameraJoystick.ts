/**
 * PTZ joystick hook
 * Press-and-hold camera control over the joystick WebSocket, with keepalives
 * to satisfy the backend dead-man watchdog.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const WS_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(
  'http',
  'ws',
)

// Must be shorter than the backend camera_joystick_hold_timeout (default 1.0s).
const KEEPALIVE_MS = 400

export interface DriveDirection {
  pan?: number // -1 | 0 | 1
  tilt?: number // -1 | 0 | 1
  zoom?: number // -1 | 0 | 1
  panSpeed?: number
  tiltSpeed?: number
  zoomSpeed?: number
}

export function useCameraJoystick(cameraId: number) {
  const [connected, setConnected] = useState(false)
  const [moving, setMoving] = useState(false)
  const ws = useRef<WebSocket | null>(null)
  const keepalive = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    const socket = new WebSocket(`${WS_BASE}/ws/cameras/${cameraId}/joystick`)
    ws.current = socket

    socket.onopen = () => setConnected(true)
    socket.onclose = () => {
      setConnected(false)
      setMoving(false)
    }
    socket.onerror = () => setConnected(false)
    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'timeout_stop') {
          setMoving(false)
        } else if (typeof msg.moving === 'boolean') {
          setMoving(msg.moving)
        }
      } catch {
        // ignore malformed frames
      }
    }

    return () => {
      if (keepalive.current) clearInterval(keepalive.current)
      socket.close()
    }
  }, [cameraId])

  const sendMessage = useCallback((message: Record<string, unknown>) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(message))
    }
  }, [])

  const stopKeepalive = useCallback(() => {
    if (keepalive.current) {
      clearInterval(keepalive.current)
      keepalive.current = null
    }
  }, [])

  const press = useCallback(
    (direction: DriveDirection) => {
      sendMessage({
        action: 'drive',
        pan: direction.pan ?? 0,
        tilt: direction.tilt ?? 0,
        zoom: direction.zoom ?? 0,
        pan_speed: direction.panSpeed ?? 12,
        tilt_speed: direction.tiltSpeed ?? 12,
        zoom_speed: direction.zoomSpeed ?? 4,
      })
      setMoving(true)
      stopKeepalive()
      keepalive.current = setInterval(() => sendMessage({ action: 'keepalive' }), KEEPALIVE_MS)
    },
    [sendMessage, stopKeepalive],
  )

  const release = useCallback(() => {
    stopKeepalive()
    sendMessage({ action: 'stop' })
    setMoving(false)
  }, [sendMessage, stopKeepalive])

  const recallPreset = useCallback(
    (presetId: number) => sendMessage({ action: 'preset', preset_id: presetId }),
    [sendMessage],
  )

  return { connected, moving, press, release, recallPreset }
}
