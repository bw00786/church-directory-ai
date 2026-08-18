import React from 'react'

export function DetectionOverlay() {
  return (
    <div className="bg-gray-800 p-4 rounded border border-gray-700">
      <h2 className="text-lg font-semibold mb-2">Detection Overlay</h2>
      <div className="relative h-40 w-full rounded border border-gray-600 bg-slate-900 overflow-hidden">
        <div className="absolute left-12 top-7 w-20 h-28 border-2 border-green-400 rounded" />
        <div className="absolute left-16 top-4 text-xs text-green-300">PERSON 17</div>
        <div className="absolute right-5 bottom-5 text-xs text-blue-300">Composition: 94%</div>
      </div>
    </div>
  )
}
