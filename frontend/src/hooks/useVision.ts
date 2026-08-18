import { useEffect, useState } from 'react'

export interface VisionStatus {
  enabled: boolean
  active: boolean
  cameras: number
  vision_fps: number
  event_threshold: number
}

export function useVision() {
  const [status, setStatus] = useState<VisionStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    try {
      setLoading(true)
      const res = await fetch('http://localhost:8000/api/vision/status')
      if (!res.ok) {
        throw new Error('Failed to fetch vision status')
      }
      const payload = await res.json()
      setStatus(payload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Vision status unavailable')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  return { status, loading, error, refresh }
}
