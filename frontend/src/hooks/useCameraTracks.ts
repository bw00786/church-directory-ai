/**
 * Live per-camera detection hook
 * Polls a single camera's current tracks/identity matches/frame size, for
 * the detection overlay to draw a real bounding box when vision is running.
 */

import { useEffect, useState } from 'react'

import { visionAPI, VisionCameraTracks } from '@/api/atem'

export function useCameraTracks(cameraId: number) {
  const [active, setActive] = useState(false)
  const [data, setData] = useState<VisionCameraTracks | null>(null)

  useEffect(() => {
    let cancelled = false

    const refresh = async () => {
      try {
        const [statusRes, tracksRes] = await Promise.all([
          visionAPI.getStatus(),
          visionAPI.getCameraTracks(cameraId),
        ])
        if (cancelled) return
        setActive(Boolean((statusRes.data as { active?: boolean }).active))
        setData(tracksRes.data)
      } catch {
        if (!cancelled) setActive(false)
      }
    }

    refresh()
    const id = setInterval(refresh, 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [cameraId])

  return { active, data }
}
