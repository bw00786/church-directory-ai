/**
 * Live vision data hook
 * Polls the vision subsystem's aggregate state (camera quality, events,
 * recommendations, recent identity matches) for panels that show live
 * detection info when vision is running, and demo data when it isn't.
 */

import { useEffect, useState } from 'react'

import { visionAPI, VisionCameraQuality, VisionEvent, VisionRecommendation } from '@/api/atem'
import { identityAPI, IdentityObservation } from '@/api/identity'

export function useVisionLive() {
  const [active, setActive] = useState(false)
  const [cameras, setCameras] = useState<VisionCameraQuality[]>([])
  const [events, setEvents] = useState<VisionEvent[]>([])
  const [recommendations, setRecommendations] = useState<VisionRecommendation[]>([])
  const [identityObservations, setIdentityObservations] = useState<IdentityObservation[]>([])

  const refresh = async () => {
    try {
      const [statusRes, camerasRes, eventsRes, recsRes, obsRes] = await Promise.all([
        visionAPI.getStatus(),
        visionAPI.getCameras(),
        visionAPI.getEvents(),
        visionAPI.getRecommendations(),
        identityAPI.getRecentObservations(10),
      ])
      setActive(Boolean((statusRes.data as { active?: boolean }).active))
      setCameras(camerasRes.data.cameras)
      setEvents(eventsRes.data.events)
      setRecommendations(recsRes.data.recommendations)
      setIdentityObservations(obsRes.data.observations)
    } catch {
      setActive(false)
    }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 3000)
    return () => clearInterval(id)
  }, [])

  return { active, cameras, events, recommendations, identityObservations }
}
