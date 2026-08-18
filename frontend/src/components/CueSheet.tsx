/**
 * Service cue-sheet panel
 * Shows current/next cue with Start / Next / Stop controls, wired to /ws/director.
 */

import React from 'react'

import { useDirector, Cue } from '@/hooks/useDirector'

function CueCard({ label, cue }: { label: string; cue: Cue | null }) {
  return (
    <div className="bg-gray-900 rounded p-3 border border-gray-700">
      <div className="text-xs uppercase text-gray-500 mb-1">{label}</div>
      {cue ? (
        <>
          <div className="font-semibold">{cue.name}</div>
          {cue.description && <p className="text-sm text-gray-400">{cue.description}</p>}
          {cue.actions?.length > 0 && (
            <ul className="mt-2 text-xs text-gray-400 list-disc list-inside">
              {cue.actions.map((a, i) => (
                <li key={i}>{a.description || a.type}</li>
              ))}
            </ul>
          )}
          <div className="mt-2 flex gap-2 text-xs">
            <span className="px-2 py-0.5 rounded bg-gray-700">advance: {cue.advance}</span>
            {cue.ai_enabled && <span className="px-2 py-0.5 rounded bg-purple-700">AI</span>}
          </div>
        </>
      ) : (
        <div className="text-gray-600 text-sm">—</div>
      )}
    </div>
  )
}

export function CueSheet() {
  const { status, connected, lastAction, start, stop, next } = useDirector()

  const running = status?.running ?? false
  const index = status?.cue_index ?? -1
  const total = status?.total_cues ?? 0
  const suggestion = status?.pending_suggestion as
    | { reason?: string; confidence?: number }
    | null
    | undefined

  return (
    <div className="bg-gray-800 p-4 rounded border border-gray-700 w-full max-w-xl">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-lg font-semibold">Service Cue Sheet</h2>
          <p className="text-xs text-gray-400">{status?.script_name ?? 'No script'}</p>
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded ${connected ? 'bg-green-700' : 'bg-red-700'}`}
        >
          {connected ? (running ? `cue ${index + 1}/${total}` : 'idle') : 'offline'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <CueCard label="Now" cue={status?.current_cue ?? null} />
        <CueCard label="Next" cue={status?.next_cue ?? null} />
      </div>

      {suggestion && (
        <div className="mt-3 p-2 rounded bg-purple-900/50 border border-purple-700 text-sm">
          AI suggests advancing: {suggestion.reason}
          {typeof suggestion.confidence === 'number' &&
            ` (${Math.round(suggestion.confidence * 100)}%)`}
        </div>
      )}

      <div className="flex gap-2 mt-4">
        <button
          className="flex-1 bg-green-700 hover:bg-green-600 disabled:opacity-40 rounded py-2 font-semibold"
          onClick={() => start(true)}
          disabled={running}
        >
          Start
        </button>
        <button
          className="flex-1 bg-blue-700 hover:bg-blue-600 disabled:opacity-40 rounded py-2 font-semibold"
          onClick={() => next()}
          disabled={!running}
        >
          Next ▶
        </button>
        <button
          className="flex-1 bg-red-700 hover:bg-red-600 disabled:opacity-40 rounded py-2 font-semibold"
          onClick={() => stop()}
          disabled={!running}
        >
          Stop
        </button>
      </div>

      {lastAction && (
        <p className="text-xs text-gray-500 mt-2">
          {lastAction.action}: {lastAction.detail}
        </p>
      )}
    </div>
  )
}
