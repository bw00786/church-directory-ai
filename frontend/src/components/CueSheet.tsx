/**
 * Service cue-sheet panel
 * Shows current/next cue with Start / Next / Stop controls, wired to /ws/director.
 */

import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import CardActions from '@mui/material/CardActions'
import Box from '@mui/material/Box'
import Paper from '@mui/material/Paper'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Button from '@mui/material/Button'
import Alert from '@mui/material/Alert'
import EventNoteIcon from '@mui/icons-material/EventNote'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import StopIcon from '@mui/icons-material/Stop'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'

import { useDirector, Cue } from '@/hooks/useDirector'

function CueCard({ label, cue }: { label: string; cue: Cue | null }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, bgcolor: 'background.default' }}>
      <Typography variant="overline" color="text.secondary">
        {label}
      </Typography>
      {cue ? (
        <Stack spacing={1}>
          <Typography variant="subtitle2">{cue.name}</Typography>
          {cue.description && (
            <Typography variant="body2" color="text.secondary">
              {cue.description}
            </Typography>
          )}
          {cue.actions?.length > 0 && (
            <Box component="ul" sx={{ m: 0, pl: 2.5, color: 'text.secondary' }}>
              {cue.actions.map((a, i) => (
                <Typography key={i} component="li" variant="caption">
                  {a.description || a.type}
                </Typography>
              ))}
            </Box>
          )}
          <Stack direction="row" spacing={1}>
            <Chip label={`advance: ${cue.advance}`} size="small" />
            {cue.ai_enabled && <Chip label="AI" color="secondary" size="small" />}
          </Stack>
        </Stack>
      ) : (
        <Typography variant="body2" color="text.disabled">
          —
        </Typography>
      )}
    </Paper>
  )
}

export function CueSheet() {
  const { status, connected, lastAction, start, stop, next } = useDirector()

  const running = status?.running ?? false
  const index = status?.cue_index ?? -1
  const total = status?.total_cues ?? 0
  const suggestion = status?.pending_suggestion as
    | { reason?: string; confidence?: number }
    | null
    | undefined

  return (
    <Card>
      <CardHeader
        avatar={<EventNoteIcon color="primary" />}
        title="Service Cue Sheet"
        subheader={status?.script_name ?? 'No script'}
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <Chip
            label={connected ? (running ? `cue ${index + 1}/${total}` : 'idle') : 'offline'}
            color={connected ? (running ? 'primary' : 'default') : 'error'}
            size="small"
            sx={{ mt: 1, mr: 1 }}
          />
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
          <CueCard label="Now" cue={status?.current_cue ?? null} />
          <CueCard label="Next" cue={status?.next_cue ?? null} />
        </Box>

        {suggestion && (
          <Alert severity="info" icon={<AutoAwesomeIcon fontSize="inherit" />} sx={{ mt: 2 }}>
            AI suggests advancing: {suggestion.reason}
            {typeof suggestion.confidence === 'number' &&
              ` (${Math.round(suggestion.confidence * 100)}%)`}
          </Alert>
        )}

        {lastAction && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: 'block' }}>
            {lastAction.action}: {lastAction.detail}
          </Typography>
        )}
      </CardContent>
      <CardActions sx={{ px: 2, pb: 2, gap: 1 }}>
        <Button
          fullWidth
          variant="contained"
          color="success"
          startIcon={<PlayArrowIcon />}
          onClick={() => start(true)}
          disabled={running}
        >
          Start
        </Button>
        <Button
          fullWidth
          variant="contained"
          startIcon={<SkipNextIcon />}
          onClick={() => next()}
          disabled={!running}
        >
          Next
        </Button>
        <Button
          fullWidth
          variant="contained"
          color="error"
          startIcon={<StopIcon />}
          onClick={() => stop()}
          disabled={!running}
        >
          Stop
        </Button>
      </CardActions>
    </Card>
  )
}
