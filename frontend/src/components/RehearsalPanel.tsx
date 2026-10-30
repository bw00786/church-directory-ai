/**
 * Rehearsal (replay) panel: pick a recording, speed and mode, start/stop, and
 * watch the AI's per-step decisions. Hardware is never touched (mock dispatch).
 */

import React, { useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import CardHeader from '@mui/material/CardHeader'
import Chip from '@mui/material/Chip'
import LinearProgress from '@mui/material/LinearProgress'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import StopIcon from '@mui/icons-material/Stop'
import TheaterComedyIcon from '@mui/icons-material/TheaterComedy'

import type { RehearsalDecision, RehearsalMode } from '@/api/rehearsal'
import { useRehearsal } from '@/hooks/useRehearsal'

const SPEEDS = [0.5, 1, 2, 5, 10]
const MODES: { value: RehearsalMode; label: string }[] = [
  { value: 'assisted', label: 'Assisted (queue proposals)' },
  { value: 'ai_directed', label: 'AI Directed (mock execute)' },
]
const MAX_ROWS = 25

export function DecisionRow({ decision }: { decision: RehearsalDecision }) {
  const acting = decision.decision !== 'continue' || decision.actions.length > 0
  return (
    <Paper role="listitem" variant="outlined" sx={{ p: 1, borderColor: acting ? 'secondary.main' : undefined }}>
      <Typography variant="caption" color="text.secondary">
        #{decision.index + 1} · {decision.observation.speaker_role || 'unknown'}
        {decision.observation.transcript ? `: “${decision.observation.transcript}”` : ' (silence)'}
      </Typography>
      <Typography variant="body2">
        {decision.decision} ({Math.round(decision.confidence * 100)}%) — {decision.reason}
      </Typography>
      <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap', mt: 0.5 }}>
        <Chip size="small" variant="outlined" label={decision.service_state} />
        <Chip size="small" variant="outlined" label={`${decision.latency_ms} ms`} />
        {decision.actions.map((a, i) => (
          <Chip key={i} size="small" color="secondary" label={`${a.type}${a.target ? ` → ${a.target}` : ''}`} />
        ))}
        {decision.dropped > 0 && <Chip size="small" color="warning" label={`${decision.dropped} dropped`} />}
      </Stack>
    </Paper>
  )
}

export function RehearsalPanel() {
  const { status, samples, allowed, error, busy, start, stop } = useRehearsal()
  const [sample, setSample] = useState('')
  const [speed, setSpeed] = useState(1)
  const [mode, setMode] = useState<RehearsalMode>('ai_directed')

  useEffect(() => {
    if (!sample && samples.length > 0) setSample(samples[0].id)
  }, [sample, samples])

  const running = Boolean(status?.running)
  const decisions = [...(status?.decisions ?? [])].reverse().slice(0, MAX_ROWS)
  const executed = status?.executed.length ?? 0
  const proposals = status?.proposals.length ?? 0

  return (
    <Card>
      <CardHeader
        avatar={<TheaterComedyIcon color="secondary" />}
        title="Rehearsal (replay)"
        subheader="Replays a recorded service through the AI with hardware mocked"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          running ? (
            <Button size="small" color="error" variant="contained" startIcon={<StopIcon />} disabled={busy} onClick={() => stop()} sx={{ mt: 1, mr: 1 }}>
              Stop
            </Button>
          ) : (
            <Button
              size="small"
              variant="contained"
              startIcon={<PlayArrowIcon />}
              disabled={busy || !sample || !allowed.allowed}
              onClick={() => start({ sample, speed, mode })}
              sx={{ mt: 1, mr: 1 }}
            >
              Start
            </Button>
          )
        }
      />
      <CardContent>
        <Stack spacing={2}>
          {!allowed.allowed && <Alert severity="warning">Rehearsal unavailable: {allowed.reason}</Alert>}
          {error && <Alert severity="error">{error}</Alert>}
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: 'wrap' }}>
            <TextField
              select
              size="small"
              label="Recording"
              value={sample}
              onChange={(e) => setSample(e.target.value)}
              disabled={running}
              slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Recording' } }}
              sx={{ minWidth: 260 }}
            >
              {samples.length === 0 && <option value="">No recordings</option>}
              {samples.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.entries} steps{s.kind === 'recording' ? ', recorded' : ''})
                </option>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Speed"
              value={String(speed)}
              onChange={(e) => setSpeed(Number(e.target.value))}
              disabled={running}
              slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Speed' } }}
            >
              {SPEEDS.map((s) => (
                <option key={s} value={String(s)}>
                  {s}×
                </option>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Mode"
              value={mode}
              onChange={(e) => setMode(e.target.value as RehearsalMode)}
              disabled={running}
              slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Mode' } }}
            >
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </TextField>
          </Stack>

          {status && status.state !== 'idle' && (
            <>
              <LinearProgress
                variant="determinate"
                value={Math.round(status.progress * 100)}
                aria-label="rehearsal progress"
                aria-valuenow={Math.round(status.progress * 100)}
              />
              <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                <Chip size="small" color={running ? 'secondary' : status.state === 'failed' ? 'error' : 'default'} label={`${status.state} · ${status.position}/${status.total}`} />
                {status.name && <Chip size="small" variant="outlined" label={status.name} />}
                {status.service_state && <Chip size="small" variant="outlined" label={`state: ${status.service_state}`} />}
                <Chip size="small" variant="outlined" label={`${executed} executed (mock)`} />
                <Chip size="small" variant="outlined" label={`${proposals} pending`} />
                {status.error && <Chip size="small" color="error" label={status.error} />}
              </Stack>
            </>
          )}

          {decisions.length > 0 && (
            <Stack spacing={1} role="list" aria-label="Rehearsal decisions">
              {decisions.map((d) => (
                <DecisionRow key={d.index} decision={d} />
              ))}
            </Stack>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}

export default RehearsalPanel
