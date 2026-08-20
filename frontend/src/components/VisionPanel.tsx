import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import CircularProgress from '@mui/material/CircularProgress'
import Alert from '@mui/material/Alert'
import VideocamIcon from '@mui/icons-material/Videocam'

import { useVision } from '../hooks/useVision'

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {value}
      </Typography>
    </Stack>
  )
}

export function VisionPanel() {
  const { status, loading, error } = useVision()

  return (
    <Card>
      <CardHeader
        avatar={<VideocamIcon color="primary" />}
        title="Vision System"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        {loading ? (
          <Stack sx={{ alignItems: 'center', py: 2 }}>
            <CircularProgress size={24} />
          </Stack>
        ) : error ? (
          <Alert severity="error" variant="outlined">
            Vision unavailable: {error}
          </Alert>
        ) : (
          <Stack spacing={1}>
            <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                Status
              </Typography>
              <Chip
                label={status?.active ? 'ACTIVE' : 'IDLE'}
                color={status?.active ? 'success' : 'default'}
                size="small"
              />
            </Stack>
            <Stat label="Cameras" value={status?.cameras ?? 0} />
            <Stat label="Vision FPS" value={status?.vision_fps ?? 0} />
            <Stat label="Event threshold" value={status?.event_threshold ?? 0} />
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}
