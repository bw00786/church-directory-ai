import { createElement } from 'react'

export { VisionPanel } from './VisionPanel'
export { DetectionOverlay } from './DetectionOverlay'
export { EventTimeline } from './EventTimeline'
export { CameraObservation } from './CameraObservation'
export { AiObservation } from './AiObservation'
export { CameraJoystick } from './CameraJoystick'
export { CueSheet } from './CueSheet'
export { SlidesPanel } from './SlidesPanel'
export { AtemPanel } from './AtemPanel'

export function CameraGrid() {
  return createElement('div', null, 'Camera Grid - TODO')
}

export function ProgramPreview() {
  return createElement('div', null, 'Program/Preview - TODO')
}

export function StreamControl() {
  return createElement('div', null, 'Stream Control - TODO')
}

export function RecordingControl() {
  return createElement('div', null, 'Recording Control - TODO')
}

export function AiDirectorPanel() {
  return createElement('div', null, 'AI Director - TODO')
}

export function EventLog() {
  return createElement('div', null, 'Event Log - TODO')
}

export function SystemStatus() {
  return createElement('div', null, 'System Status - TODO')
}
