import React from 'react'
import { AiObservation } from './components/AiObservation'
import { CameraJoystick } from './components/CameraJoystick'
import { CameraObservation } from './components/CameraObservation'
import { CueSheet } from './components/CueSheet'
import { DetectionOverlay } from './components/DetectionOverlay'
import { EventTimeline } from './components/EventTimeline'
import { VisionPanel } from './components/VisionPanel'

export function App() {
  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <header className="bg-gray-800 border-b border-gray-700 p-4">
        <h1 className="text-2xl font-bold">Church Production Director</h1>
        <p className="text-gray-400">AI-assisted worship production control</p>
      </header>

      <main className="p-4 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <VisionPanel />
          <CameraObservation />
          <DetectionOverlay />
          <AiObservation />
        </div>

        <div className="flex flex-wrap gap-4">
          <CueSheet />
          <CameraJoystick cameraId={1} />
        </div>

        <EventTimeline />
      </main>
    </div>
  )
}

export default App
