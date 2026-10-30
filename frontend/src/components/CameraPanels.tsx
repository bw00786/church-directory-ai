/**
 * One PTZ joystick per registered camera (GET /cameras), plus a small
 * rig-profile summary card. A church with two PTZ cameras gets two panels
 * without touching App.tsx; an empty camera list falls back to camera 1.
 */

import React, { useCallback, useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import CardHeader from '@mui/material/CardHeader'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import SettingsInputComponentIcon from '@mui/icons-material/SettingsInputComponent'

import { cameraAPI } from '@/api/atem'
import { useRig } from '@/hooks/useRig'
import { CameraJoystick } from './CameraJoystick'

export interface RegisteredCamera {
  camera_id: number
  name: string
  host: string | null
  connected: boolean
}

const FALLBACK_CAMERAS: RegisteredCamera[] = [{ camera_id: 1, name: 'Camera 1', host: null, connected: false }]
const POLL_MS = 5000

/** Registered cameras from the backend, refreshed every few seconds; camera 1 when the list is empty. */
export function useRegisteredCameras(pollMs = POLL_MS) {
  const [cameras, setCameras] = useState<RegisteredCamera[]>(FALLBACK_CAMERAS)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const response = await cameraAPI.listCameras()
      const list = (response.data?.cameras ?? []) as RegisteredCamera[]
      setCameras(list.length > 0 ? [...list].sort((a, b) => a.camera_id - b.camera_id) : FALLBACK_CAMERAS)
    } catch {
      // Backend unreachable: keep whatever we had (camera 1 by default).
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
    if (pollMs <= 0) return
    const id = setInterval(() => void refresh(), pollMs)
    return () => clearInterval(id)
  }, [refresh, pollMs])

  return { cameras, loaded, refresh }
}

interface CameraPanelsProps {
  presets?: number[]
}

export function CameraPanels({ presets }: CameraPanelsProps) {
  const { cameras } = useRegisteredCameras()
  return (
    <Stack spacing={2} data-testid="camera-panels">
      {cameras.map((camera) => (
        <Box key={camera.camera_id} data-testid={`camera-panel-${camera.camera_id}`}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.5, px: 0.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }} noWrap title={camera.host ?? undefined}>
              {camera.name}
            </Typography>
            <Chip
              size="small"
              variant="outlined"
              label={camera.connected ? 'connected' : 'not connected'}
              color={camera.connected ? 'success' : 'default'}
            />
          </Stack>
          <CameraJoystick cameraId={camera.camera_id} presets={presets} />
        </Box>
      ))}
    </Stack>
  )
}

/** Church name, cameras, PTZ roles and mixer channels from GET /api/rig. */
export function RigSummary() {
  const { summary, loading, error } = useRig()

  return (
    <Card data-testid="rig-summary">
      <CardHeader
        avatar={<SettingsInputComponentIcon color="primary" />}
        title={summary?.church ?? 'Rig profile'}
        titleTypographyProps={{ variant: 'subtitle2' }}
        subheader={summary ? (summary.source === 'file' ? summary.path ?? 'rig.yaml' : 'defaults from .env') : undefined}
        subheaderTypographyProps={{ variant: 'caption' }}
      />
      <CardContent sx={{ pt: 0 }}>
        {error && (
          <Typography variant="caption" color="error">
            {error}
          </Typography>
        )}
        {!error && loading && !summary && (
          <Typography variant="caption" color="text.secondary">
            Loading…
          </Typography>
        )}
        {summary && (
          <Stack spacing={1.5}>
            <Section label="Cameras">
              {summary.cameras.length === 0 && <Muted>none registered</Muted>}
              {summary.cameras.map((camera) => (
                <Chip
                  key={camera.id}
                  size="small"
                  label={`${camera.id}: ${camera.name} → ATEM ${camera.atem_input}`}
                  title={camera.host ?? 'no host'}
                />
              ))}
              <Chip size="small" variant="outlined" label={`slides → ATEM ${summary.slides_input}`} />
            </Section>
            <Section label="PTZ roles">
              {Object.keys(summary.roles).length === 0 && <Muted>none</Muted>}
              {Object.entries(summary.roles).map(([role, shot]) => (
                <Chip key={role} size="small" variant="outlined" label={`${role}: cam ${shot.camera} / preset ${shot.preset}`} />
              ))}
            </Section>
            <Section label="Mixer channels">
              {Object.keys(summary.mixer_roles).length === 0 && <Muted>none</Muted>}
              {Object.entries(summary.mixer_roles).map(([role, channel]) => (
                <Chip key={role} size="small" variant="outlined" label={`${role}: ch ${channel}`} />
              ))}
            </Section>
            {summary.cue_overrides.length > 0 && (
              <Typography variant="caption" color="text.secondary">
                {summary.cue_overrides.length} cue override{summary.cue_overrides.length === 1 ? '' : 's'}:{' '}
                {summary.cue_overrides.join(', ')}
              </Typography>
            )}
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        {label}
      </Typography>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>{children}</Box>
    </Box>
  )
}

function Muted({ children }: { children: React.ReactNode }) {
  return (
    <Typography variant="caption" color="text.disabled">
      {children}
    </Typography>
  )
}

export default CameraPanels
