/**
 * ATEM Mini Pro ISO manual control panel
 * Camera switch buttons (up to 4 inputs), stream (on air/off air),
 * record, and mic mute toggles for the two mixer channels.
 */

import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import CircularProgress from '@mui/material/CircularProgress'
import Alert from '@mui/material/Alert'
import Divider from '@mui/material/Divider'
import SettingsInputHdmiIcon from '@mui/icons-material/SettingsInputHdmi'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import StopIcon from '@mui/icons-material/Stop'
import MicIcon from '@mui/icons-material/Mic'
import MicOffIcon from '@mui/icons-material/MicOff'
import SensorsIcon from '@mui/icons-material/Sensors'

import { useAtem } from '@/hooks/useAtem'

// Fallback slots so 4 camera buttons always render, even before /atem/status
// has loaded or if the ATEM only reports fewer configured inputs.
const CAMERA_SLOT_COUNT = 4

export function AtemPanel() {
  const { state, loading, error, setProgram, setPreview, performCut, performAuto, setStreaming, setRecording, setMicMuted } =
    useAtem()

  const inputs = state?.inputs ?? []
  const slots = Array.from({ length: CAMERA_SLOT_COUNT }, (_, i) => inputs[i])

  return (
    <Card>
      <CardHeader
        avatar={<SettingsInputHdmiIcon color="primary" />}
        title="ATEM Mini Pro ISO"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <Chip
            label={state?.connected ? 'CONNECTED' : 'DISCONNECTED'}
            color={state?.connected ? 'success' : 'default'}
            size="small"
            sx={{ mt: 1, mr: 1 }}
          />
        }
      />
      <CardContent>
        {loading && !state ? (
          <Stack sx={{ alignItems: 'center', py: 2 }}>
            <CircularProgress size={24} />
          </Stack>
        ) : (
          <Stack spacing={2}>
            {error && (
              <Alert severity="warning" variant="outlined">
                {error}
              </Alert>
            )}

            <Box>
              <Typography variant="caption" color="text.secondary" gutterBottom sx={{ display: 'block' }}>
                Cameras
              </Typography>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1 }}>
                {slots.map((input, i) => {
                  const isProgram = input && state?.program_input === input.id
                  const isPreview = input && state?.preview_input === input.id
                  return (
                    <Button
                      key={input?.id ?? `empty-${i}`}
                      variant={isProgram ? 'contained' : 'outlined'}
                      color={isProgram ? 'error' : isPreview ? 'success' : 'inherit'}
                      size="small"
                      disabled={!input || !state?.connected}
                      onClick={() => input && setProgram(input.id)}
                      onDoubleClick={() => input && setPreview(input.id)}
                      sx={{ flexDirection: 'column', py: 1, minWidth: 0 }}
                    >
                      <Typography variant="caption" sx={{ fontWeight: 700 }}>
                        Cam {i + 1}
                      </Typography>
                      <Typography variant="caption" noWrap sx={{ maxWidth: '100%' }}>
                        {input ? input.short_name || input.name : '—'}
                      </Typography>
                    </Button>
                  )
                })}
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                Click = program (live) &middot; double-click = preview
              </Typography>
            </Box>

            <Stack direction="row" spacing={1}>
              <Button
                variant="outlined"
                size="small"
                disabled={!state?.connected}
                onClick={performCut}
                sx={{ flex: 1 }}
              >
                Cut
              </Button>
              <Button
                variant="outlined"
                size="small"
                disabled={!state?.connected || state?.transition_in_progress}
                onClick={performAuto}
                sx={{ flex: 1 }}
              >
                Auto
              </Button>
            </Stack>

            <Divider />

            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                size="small"
                color={state?.streaming ? 'error' : 'inherit'}
                startIcon={<SensorsIcon />}
                disabled={!state?.connected}
                onClick={() => setStreaming(!state?.streaming)}
                sx={{ flex: 1 }}
              >
                {state?.streaming ? 'On Air' : 'Off Air'}
              </Button>
              <Button
                variant="contained"
                size="small"
                color={state?.recording ? 'error' : 'inherit'}
                startIcon={state?.recording ? <StopIcon /> : <FiberManualRecordIcon />}
                disabled={!state?.connected}
                onClick={() => setRecording(!state?.recording)}
                sx={{ flex: 1 }}
              >
                {state?.recording ? 'Stop Rec' : 'Record'}
              </Button>
            </Stack>

            <Divider />

            <Box>
              <Typography variant="caption" color="text.secondary" gutterBottom sx={{ display: 'block' }}>
                Mics
              </Typography>
              <Stack direction="row" spacing={1}>
                {(state?.audio_channels ?? [{ id: 1, name: 'Mic 1', muted: false }, { id: 2, name: 'Mic 2', muted: false }]).map(
                  (mic) => (
                    <Button
                      key={mic.id}
                      variant="outlined"
                      size="small"
                      color={mic.muted ? 'error' : 'success'}
                      startIcon={mic.muted ? <MicOffIcon /> : <MicIcon />}
                      disabled={!state?.connected}
                      onClick={() => setMicMuted(mic.id, !mic.muted)}
                      sx={{ flex: 1 }}
                    >
                      {mic.name}
                    </Button>
                  )
                )}
              </Stack>
            </Box>
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}
