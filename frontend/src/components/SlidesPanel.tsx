/**
 * EasyWorship slides panel
 * Manual slide control (Next / Prev / Item nav / Clear / Live) + connection status.
 */

import React, { useEffect, useState } from 'react'

import { easyworshipAPI } from '@/api/atem'

export function SlidesPanel() {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [lastAction, setLastAction] = useState<string | null>(null)

  const refresh = async () => {
    try {
      const res = await easyworshipAPI.getStatus()
      setConnected(Boolean(res.data.connected))
      setLastAction(res.data.last_action ?? null)
    } catch {
      setConnected(false)
    }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [])

  const run = async (name: string) => {
    try {
      await easyworshipAPI.action(name)
      setLastAction(name)
    } catch {
      setLastAction(`${name} (failed)`)
    }
  }

  const btn = 'bg-gray-700 hover:bg-gray-600 active:bg-blue-600 rounded py-2 font-semibold'

  return (
    <div className="bg-gray-800 p-4 rounded border border-gray-700 w-64">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Slides</h2>
        <span
          className={`text-xs px-2 py-0.5 rounded ${
            connected ? 'bg-green-700' : 'bg-red-700'
          }`}
        >
          {connected == null ? '…' : connected ? 'connected' : 'offline'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 mb-2">
        <button className={btn} onClick={() => run('prev_slide')}>
          ◀ Prev
        </button>
        <button className={btn} onClick={() => run('next_slide')}>
          Next ▶
        </button>
        <button className={btn} onClick={() => run('prev_item')}>
          ◀◀ Item
        </button>
        <button className={btn} onClick={() => run('next_item')}>
          Item ▶▶
        </button>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <button className={btn} onClick={() => run('live')}>
          Live
        </button>
        <button className={btn} onClick={() => run('clear')}>
          Clear
        </button>
        <button className={btn} onClick={() => run('black')}>
          Black
        </button>
      </div>

      {lastAction && <p className="text-xs text-gray-500 mt-2">last: {lastAction}</p>}
    </div>
  )
}
