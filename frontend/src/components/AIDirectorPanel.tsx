/**
 * AI Service Director panel
 * Shows mode (Manual/Assisted/AI Directed), current service state, speaker,
 * transcript, camera/ATEM/EasyWorship, and the latest AI decision. Lets the
 * operator switch modes and approve/reject pending (assisted-mode) actions.
 */

import React, { useEffect, useState } from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Box from '@mui/material/Box'
import Paper from '@mui/material/Paper'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Button from '@mui/material/Button'
import ButtonGroup from '@mui/material/ButtonGroup'
import Divider from '@mui/material/Divider'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'

import { AiDirectorMode, PendingAction, useAIDirector } from '@/hooks/useAIDirector'

const MODES: { value: AiDirectorMode; label: string }[] = [
  { value: 'manual', label: 'Manual' },
  { value: 'assisted', label: 'Assisted' },
  { value: 'ai_directed', label: 'AI Directed' },
]

function formatState(state: string): string {
  return state
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/** Seconds left before a proposal expires, counted down locally between polls. */
export function remainingSeconds(action: PendingAction, fetchedAt: number | null, now: number): number | null {
  if (action.expires_in_seconds === undefined || action.expires_in_seconds === null) return null
  const elapsed = fetchedAt ? Math.max(0, (now - fetchedAt) / 1000) : 0
  return Math.max(0, Math.round(action.expires_in_seconds - elapsed))
}

interface PendingActionRowProps {
  action: PendingAction
  fetchedAt: number | null
  now: number
  onApprove: (id: string) => void
  onReject: (id: string) => void
}

export function PendingActionRow({ action, fetchedAt, now, onApprove, onReject }: PendingActionRowProps) {
  const ttl = remainingSeconds(action, fetchedAt, now)
  const expiring = ttl !== null && ttl <= 10
  return (
    <Paper
      role="listitem"
      aria-label={`${action.type}${action.target ? ` ${action.target}` : ''}`}
      variant="outlined"
      sx={{
        p: 1.5,
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        borderColor: action.held_reason ? 'warning.main' : undefined,
      }}
    >
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2">
          {action.type} {action.target ? `→ ${action.target}` : ''}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          {action.reason} ({Math.round(action.confidence * 100)}%)
        </Typography>
        <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap', mt: 0.5 }}>
          {action.source && <Chip size="small" variant="outlined" label={action.source} />}
          {ttl !== null && (
            <Chip
              size="small"
              variant="outlined"
              color={expiring ? 'error' : 'default'}
              label={`expires in ${ttl}s`}
              aria-label={`expires in ${ttl} seconds`}
            />
          )}
          {action.held_reason && (
            <Chip size="small" color="warning" label={`held: ${action.held_reason}`} title={action.held_reason} />
          )}
        </Stack>
      </Box>
      <Button size="small" color="success" startIcon={<CheckIcon />} onClick={() => onApprove(action.id)}>
        Approve
      </Button>
      <Button size="small" color="error" startIcon={<CloseIcon />} onClick={() => onReject(action.id)}>
        Reject
      </Button>
    </Paper>
  )
}

export function AIDirectorPanel() {
  const { status, fetchedAt, setMode, approve, reject } = useAIDirector()
  const [now, setNow] = useState(() => Date.now())
  const hasPending = (status?.pending_actions.length ?? 0) > 0

  // Tick once a second while proposals are pending so TTL countdowns move between polls.
  useEffect(() => {
    if (!hasPending) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [hasPending, fetchedAt])

  const context = status?.context
  const decision = context?.last_decision
  const lastCycle = status?.last_cycle

  return (
    <Card>
      <CardHeader
        avatar={<SmartToyIcon color="secondary" />}
        title="AI Service Director"
        subheader={context ? formatState(context.service_state) : 'Loading…'}
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <ButtonGroup size="small" sx={{ mt: 1, mr: 1 }}>
            {MODES.map((m) => (
              <Button
                key={m.value}
                variant={status?.mode === m.value ? 'contained' : 'outlined'}
                onClick={() => setMode(m.value)}
              >
                {m.label}
              </Button>
            ))}
          </ButtonGroup>
        }
      />
      <CardContent>
        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' },
          }}
        >
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="overline" color="text.secondary">
              Speaker
            </Typography>
            <Typography variant="subtitle2">
              {context?.speaker ?? '—'}{' '}
              {context?.speaking && <Chip label="speaking" size="small" color="success" />}
            </Typography>

            <Typography variant="overline" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
              Camera / ATEM / EasyWorship
            </Typography>
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
              <Chip label={`camera: ${context?.camera_role ?? '—'}`} size="small" />
              <Chip label={`atem: ${context?.atem_program ?? '—'}`} size="small" />
              <Chip label={`slide: ${context?.easyworship_item ?? '—'}`} size="small" />
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="overline" color="text.secondary">
              AI Decision
            </Typography>
            {decision?.reason ? (
              <>
                <Typography variant="body2">{decision.reason}</Typography>
                <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap', mt: 1 }}>
                  <Chip
                    size="small"
                    color={decision.decision === 'continue' ? 'default' : 'secondary'}
                    label={`${decision.decision} · ${Math.round((decision.confidence ?? 0) * 100)}%`}
                  />
                  {lastCycle?.latency_ms !== undefined && lastCycle?.latency_ms !== null && (
                    <Chip size="small" variant="outlined" label={`${lastCycle.latency_ms} ms`} />
                  )}
                  {lastCycle?.stale && <Chip size="small" color="warning" label="stale context" />}
                </Stack>
              </>
            ) : (
              <Typography variant="body2" color="text.disabled">
                No decision yet
              </Typography>
            )}
          </Paper>
        </Box>

        {context?.recent_transcript && (
          <Paper variant="outlined" sx={{ p: 2, mt: 2, bgcolor: 'background.default' }}>
            <Typography variant="overline" color="text.secondary">
              Transcript
            </Typography>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-line' }}>
              {context.recent_transcript}
            </Typography>
          </Paper>
        )}

        {status?.perception && (
          <Paper
            variant="outlined"
            sx={{
              p: 1.5,
              mt: 2,
              borderColor: Object.values(status.perception.channels).some((c) => c.source !== 'usb')
                ? 'error.main'
                : undefined,
            }}
          >
            <Typography variant="overline" color="text.secondary">
              Perception
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
              {Object.entries(status.perception.channels).map(([role, ch]) => (
                <Chip
                  key={role}
                  size="small"
                  color={ch.source === 'usb' ? 'success' : 'error'}
                  variant={ch.source === 'usb' ? 'filled' : 'outlined'}
                  label={`${role}: ${ch.source}/${ch.vad_provider}${ch.asr ? '+asr' : ''}`}
                />
              ))}
            </Stack>
          </Paper>
        )}

        {status?.vision && (status.vision.detector || Object.keys(status.vision.occupancy).length > 0) && (
          <Paper
            variant="outlined"
            sx={{
              p: 1.5,
              mt: 2,
              borderColor: status.vision.occupancy.program?.frame_health === 'black' ? 'error.main' : undefined,
            }}
          >
            <Typography variant="overline" color="text.secondary">
              Vision · detector {status.vision.detector ?? 'none'}
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
              {Object.entries(status.vision.occupancy).map(([key, o]) => (
                <Chip
                  key={key}
                  size="small"
                  color={o.frame_health !== 'ok' ? 'error' : o.person_in_roi ? 'success' : 'default'}
                  variant={o.person_in_roi ? 'filled' : 'outlined'}
                  label={`${key}: ${o.frame_health}${o.person_in_roi ? ' · in-roi' : ''}`}
                />
              ))}
            </Stack>
          </Paper>
        )}

        {status && hasPending && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="overline" color="text.secondary">
              {status.mode === 'ai_directed' ? 'Held Actions (waiting for pacing)' : 'Pending Actions (Assisted mode)'}
            </Typography>
            <Stack spacing={1} sx={{ mt: 1 }} role="list" aria-label="Pending AI proposals">
              {status.pending_actions.map((action) => (
                <PendingActionRow
                  key={action.id}
                  action={action}
                  fetchedAt={fetchedAt}
                  now={now}
                  onApprove={approve}
                  onReject={reject}
                />
              ))}
            </Stack>
          </>
        )}
      </CardContent>
    </Card>
  )
}
