/**
 * PTZ camera joystick control
 * Press-and-hold pan/tilt/zoom plus preset recall/save for a PTZOptics camera.
 */

import React, { useState } from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import Button from '@mui/material/Button'
import Slider from '@mui/material/Slider'
import ToggleButton from '@mui/material/ToggleButton'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'
import StopIcon from '@mui/icons-material/Stop'
import ZoomInIcon from '@mui/icons-material/ZoomIn'
import ZoomOutIcon from '@mui/icons-material/ZoomOut'
import VideocamIcon from '@mui/icons-material/Videocam'

import { cameraAPI } from '@/api/atem'
import { useCameraJoystick, DriveDirection } from '@/hooks/useCameraJoystick'

interface CameraJoystickProps {
  cameraId?: number
  presets?: number[]
}

export function CameraJoystick({ cameraId = 1, presets = [1, 2, 3, 4, 5, 6] }: CameraJoystickProps) {
  const { connected, moving, press, release, recallPreset } = useCameraJoystick(cameraId)
  const [speed, setSpeed] = useState(12)
  const [setMode, setSetMode] = useState(false)
  const [status, setStatus] = useState<string | null>(null)

  const holdHandlers = (direction: DriveDirection) => ({
    onPointerDown: (e: React.PointerEvent) => {
      e.preventDefault()
      press({ ...direction, panSpeed: speed, tiltSpeed: speed })
    },
    onPointerUp: () => release(),
    onPointerLeave: () => release(),
    onPointerCancel: () => release(),
  })

  const handlePreset = async (presetId: number) => {
    if (setMode) {
      try {
        await cameraAPI.savePreset(cameraId, presetId)
        setStatus(`Saved preset ${presetId}`)
      } catch {
        setStatus(`Failed to save preset ${presetId}`)
      }
      setSetMode(false)
    } else {
      recallPreset(presetId)
      setStatus(`Recalled preset ${presetId}`)
    }
  }

  const dpadButtonSx = {
    bgcolor: 'action.hover',
    borderRadius: 1,
    height: 48,
    '&:hover': { bgcolor: 'action.selected' },
  }

  return (
    <Card>
      <CardHeader
        avatar={<VideocamIcon color="primary" />}
        title={`Camera ${cameraId}`}
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <Chip
            label={connected ? (moving ? 'moving' : 'ready') : 'offline'}
            color={connected ? (moving ? 'warning' : 'success') : 'error'}
            size="small"
            sx={{ mt: 1, mr: 1 }}
          />
        }
      />
      <CardContent>
        {/* D-pad: tilt/pan */}
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1, mb: 2 }}>
          <Box />
          <IconButton sx={dpadButtonSx} aria-label="Tilt up" {...holdHandlers({ tilt: 1 })}>
            <ArrowUpwardIcon />
          </IconButton>
          <Box />

          <IconButton sx={dpadButtonSx} aria-label="Pan left" {...holdHandlers({ pan: -1 })}>
            <ArrowBackIcon />
          </IconButton>
          <IconButton
            sx={{ ...dpadButtonSx, bgcolor: 'background.default' }}
            aria-label="Stop"
            onClick={() => release()}
          >
            <StopIcon fontSize="small" />
          </IconButton>
          <IconButton sx={dpadButtonSx} aria-label="Pan right" {...holdHandlers({ pan: 1 })}>
            <ArrowForwardIcon />
          </IconButton>

          <Box />
          <IconButton sx={dpadButtonSx} aria-label="Tilt down" {...holdHandlers({ tilt: -1 })}>
            <ArrowDownwardIcon />
          </IconButton>
          <Box />
        </Box>

        {/* Zoom */}
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <Button
            fullWidth
            variant="outlined"
            startIcon={<ZoomOutIcon />}
            aria-label="Zoom out"
            {...holdHandlers({ zoom: -1 })}
          >
            Zoom
          </Button>
          <Button
            fullWidth
            variant="outlined"
            startIcon={<ZoomInIcon />}
            aria-label="Zoom in"
            {...holdHandlers({ zoom: 1 })}
          >
            Zoom
          </Button>
        </Stack>

        {/* Speed */}
        <Box sx={{ mb: 2 }}>
          <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
            <Typography variant="caption" color="text.secondary">
              Speed
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {speed}
            </Typography>
          </Stack>
          <Slider
            size="small"
            min={1}
            max={24}
            value={speed}
            onChange={(_, value) => setSpeed(value as number)}
          />
        </Box>

        {/* Presets */}
        <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Presets
          </Typography>
          <ToggleButton
            value="set"
            selected={setMode}
            onChange={() => setSetMode((v) => !v)}
            size="small"
            color="warning"
          >
            {setMode ? 'Tap a preset to save' : 'Set'}
          </ToggleButton>
        </Stack>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1 }}>
          {presets.map((presetId) => (
            <Button
              key={presetId}
              variant="outlined"
              onClick={() => handlePreset(presetId)}
            >
              {presetId}
            </Button>
          ))}
        </Box>

        {status && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: 'block' }}>
            {status}
          </Typography>
        )}
      </CardContent>
    </Card>
  )
}
