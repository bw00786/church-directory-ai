import React from 'react'

export function EventTimeline() {
  const events = [
    { time: '10:31:22', type: 'LIKELY_SPEAKER', confidence: '91%' },
    { time: '10:31:28', type: 'GOOD_COMPOSITION', confidence: '94%' },
    { time: '10:32:14', type: 'CAMERA_QUALITY_CHANGE', confidence: '87%' },
    { time: '10:35:01', type: 'CONGREGATION_ACTIVE', confidence: '90%' },
  ]

  return (
    <div className="bg-gray-800 p-4 rounded border border-gray-700">
      <h2 className="text-lg font-semibold mb-2">Event Timeline</h2>
      <ul className="space-y-2 text-sm text-gray-200">
        {events.map((event, index) => (
          <li key={index} className="flex justify-between border-b border-gray-700 pb-2">
            <span>{event.time}</span>
            <span>{event.type}</span>
            <span>{event.confidence}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
