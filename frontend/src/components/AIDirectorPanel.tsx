/**
 * AI Service Director panel
 * Shows mode (Manual/Assisted/AI Directed), current service state, speaker,
 * transcript, camera/ATEM/EasyWorship, and the latest AI decision. Lets the
 * operator switch modes and approve/reject pending (assisted-mode) actions.
 */

import React from 'react'
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

import { AiDirectorMode, useAIDirector } from '@/hooks/useAIDirector'

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

export function AIDirectorPanel() {
  const { status, setMode, approve, reject } = useAIDirector()

  const context = status?.context
  const decision = context?.last_decision

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
            <Stack direction="row" spacing={1} flexWrap="wrap">
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
                <Chip
                  sx={{ mt: 1 }}
                  size="small"
                  color={decision.decision === 'continue' ? 'default' : 'secondary'}
                  label={`${decision.decision} · ${Math.round((decision.confidence ?? 0) * 100)}%`}
                />
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

        {status && status.pending_actions.length > 0 && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="overline" color="text.secondary">
              Pending Actions (Assisted mode)
            </Typography>
            <Stack spacing={1} sx={{ mt: 1 }}>
              {status.pending_actions.map((action, index) => (
                <Paper key={index} variant="outlined" sx={{ p: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Box sx={{ flexGrow: 1 }}>
                    <Typography variant="body2">
                      {action.type} {action.target ? `→ ${action.target}` : ''}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {action.reason} ({Math.round(action.confidence * 100)}%)
                    </Typography>
                  </Box>
                  <Button size="small" color="success" startIcon={<CheckIcon />} onClick={() => approve(index)}>
                    Approve
                  </Button>
                  <Button size="small" color="error" startIcon={<CloseIcon />} onClick={() => reject(index)}>
                    Reject
                  </Button>
                </Paper>
              ))}
            </Stack>
          </>
        )}
      </CardContent>
    </Card>
  )
}
