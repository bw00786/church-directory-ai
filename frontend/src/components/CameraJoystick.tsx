/**
 * PTZ camera joystick control
 * Press-and-hold pan/tilt/zoom plus preset recall/save for a PTZOptics camera.
 */

import React, { useState } from 'react'

import { cameraAPI } from '@/api/atem'
import { useCameraJoystick, DriveDirection } from '@/hooks/useCameraJoystick'

interface CameraJoystickProps {
  cameraId?: number
  presets?: number[]
}

export function CameraJoystick({ cameraId = 1, presets = [1, 2, 3, 4, 5, 6] }: CameraJoystickProps) {
  const { connected, moving, press, release, recallPreset } = useCameraJoystick(cameraId)
  const [speed, setSpeed] = useState(12)
  const [setMode, setSetMode] = useState(false)
  const [status, setStatus] = useState<string | null>(null)

  const holdHandlers = (direction: DriveDirection) => ({
    onPointerDown: (e: React.PointerEvent) => {
      e.preventDefault()
      press({ ...direction, panSpeed: speed, tiltSpeed: speed })
    },
    onPointerUp: () => release(),
    onPointerLeave: () => release(),
    onPointerCancel: () => release(),
  })

  const handlePreset = async (presetId: number) => {
    if (setMode) {
      try {
        await cameraAPI.savePreset(cameraId, presetId)
        setStatus(`Saved preset ${presetId}`)
      } catch {
        setStatus(`Failed to save preset ${presetId}`)
      }
      setSetMode(false)
    } else {
      recallPreset(presetId)
      setStatus(`Recalled preset ${presetId}`)
    }
  }

  const dpadButton =
    'bg-gray-700 hover:bg-gray-600 active:bg-blue-600 rounded text-xl font-bold h-12 flex items-center justify-center select-none touch-none'

  return (
    <div className="bg-gray-800 p-4 rounded border border-gray-700 w-64">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Camera {cameraId}</h2>
        <span
          className={`text-xs px-2 py-0.5 rounded ${
            connected ? 'bg-green-700' : 'bg-red-700'
          }`}
        >
          {connected ? (moving ? 'moving' : 'ready') : 'offline'}
        </span>
      </div>

      {/* D-pad: tilt/pan */}
      <div className="grid grid-cols-3 gap-1 mb-3">
        <div />
        <button className={dpadButton} aria-label="Tilt up" {...holdHandlers({ tilt: 1 })}>
          ▲
        </button>
        <div />

        <button className={dpadButton} aria-label="Pan left" {...holdHandlers({ pan: -1 })}>
          ◀
        </button>
        <button
          className="bg-gray-900 hover:bg-gray-700 rounded text-sm h-12 select-none touch-none"
          aria-label="Stop"
          onClick={() => release()}
        >
          ■
        </button>
        <button className={dpadButton} aria-label="Pan right" {...holdHandlers({ pan: 1 })}>
          ▶
        </button>

        <div />
        <button className={dpadButton} aria-label="Tilt down" {...holdHandlers({ tilt: -1 })}>
          ▼
        </button>
        <div />
      </div>

      {/* Zoom */}
      <div className="grid grid-cols-2 gap-1 mb-3">
        <button className={dpadButton} aria-label="Zoom out" {...holdHandlers({ zoom: -1 })}>
          − Zoom
        </button>
        <button className={dpadButton} aria-label="Zoom in" {...holdHandlers({ zoom: 1 })}>
          + Zoom
        </button>
      </div>

      {/* Speed */}
      <div className="mb-3">
        <label className="text-xs text-gray-400 flex justify-between">
          <span>Speed</span>
          <span>{speed}</span>
        </label>
        <input
          type="range"
          min={1}
          max={24}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="w-full"
        />
      </div>

      {/* Presets */}
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-400">Presets</span>
        <button
          className={`text-xs px-2 py-0.5 rounded ${
            setMode ? 'bg-amber-600' : 'bg-gray-700 hover:bg-gray-600'
          }`}
          onClick={() => setSetMode((v) => !v)}
        >
          {setMode ? 'Tap a preset to save' : 'Set'}
        </button>
      </div>
      <div className="grid grid-cols-3 gap-1">
        {presets.map((presetId) => (
          <button
            key={presetId}
            className="bg-gray-700 hover:bg-gray-600 active:bg-blue-600 rounded h-10 font-semibold"
            onClick={() => handlePreset(presetId)}
          >
            {presetId}
          </button>
        ))}
      </div>

      {status && <p className="text-xs text-gray-400 mt-2">{status}</p>}
    </div>
  )
}
