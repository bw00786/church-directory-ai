import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import LinearProgress from '@mui/material/LinearProgress'
import SmartToyIcon from '@mui/icons-material/SmartToy'

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

export function AiObservation() {
  const confidence = 0.91

  return (
    <Card>
      <CardHeader
        avatar={<SmartToyIcon color="secondary" />}
        title="AI Observation"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        <Stack spacing={1}>
          <Stat label="Likely speaker" value="Pastor" />
          <Stat label="Recommended camera" value={1} />
          <Stack spacing={0.5}>
            <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
              <Typography variant="body2" color="text.secondary">
                Confidence
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {Math.round(confidence * 100)}%
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={confidence * 100}
              color="secondary"
              sx={{ height: 6, borderRadius: 3 }}
            />
          </Stack>
          <Typography variant="caption" color="text.secondary">
            Reason: Best medium-close speaker composition
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  )
}
