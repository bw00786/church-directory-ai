import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import LinearProgress from '@mui/material/LinearProgress'
import SmartToyIcon from '@mui/icons-material/SmartToy'

import { useVisionLive } from '@/hooks/useVisionLive'

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
  const { active, recommendations, identityObservations } = useVisionLive()
  const latestRecommendation = recommendations[recommendations.length - 1]
  const latestFaceMatch = identityObservations.find((o) => o.modality === 'face' && o.person_name)

  const speakerName = latestFaceMatch?.person_name ?? 'Unknown'
  const recommendedCamera = latestRecommendation?.recommended_camera ?? '—'
  const confidence = latestRecommendation?.score ?? 0
  const reason = latestRecommendation?.reason ?? 'No recommendation yet'

  return (
    <Card>
      <CardHeader
        avatar={<SmartToyIcon color="secondary" />}
        title="AI Observation"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          !active ? (
            <Chip label="Demo data" size="small" color="warning" variant="outlined" sx={{ mt: 1, mr: 1 }} />
          ) : undefined
        }
      />
      <CardContent>
        <Stack spacing={1}>
          <Stat label="Likely speaker" value={active ? speakerName : 'Pastor'} />
          <Stat label="Recommended camera" value={active ? recommendedCamera : 1} />
          <Stack spacing={0.5}>
            <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
              <Typography variant="body2" color="text.secondary">
                Confidence
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {Math.round((active ? confidence : 0.91) * 100)}%
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={(active ? confidence : 0.91) * 100}
              color="secondary"
              sx={{ height: 6, borderRadius: 3 }}
            />
          </Stack>
          <Typography variant="caption" color="text.secondary">
            Reason: {active ? reason : 'Best medium-close speaker composition'}
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  )
}
