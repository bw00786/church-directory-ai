import React from 'react'
import { useVision } from '../hooks/useVision'

export function VisionPanel() {
  const { status, loading, error } = useVision()

  if (loading) return <div className="bg-gray-800 p-4 rounded border border-gray-700">Loading vision...</div>
  if (error) return <div className="bg-gray-800 p-4 rounded border border-red-500">Vision unavailable: {error}</div>

  return (
    <div className="bg-gray-800 p-4 rounded border border-gray-700">
      <h2 className="text-lg font-semibold mb-2">Vision System</h2>
      <p><strong>Status:</strong> {status?.active ? 'ACTIVE' : 'IDLE'}</p>
      <p><strong>Cameras:</strong> {status?.cameras ?? 0}</p>
      <p><strong>Vision FPS:</strong> {status?.vision_fps ?? 0}</p>
      <p><strong>Event threshold:</strong> {status?.event_threshold ?? 0}</p>
    </div>
  )
}
