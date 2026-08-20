import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import CameraAltIcon from '@mui/icons-material/CameraAlt'

import { useVisionLive } from '@/hooks/useVisionLive'
import { useCameraTracks } from '@/hooks/useCameraTracks'

const CAMERA_ID = 1

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

export function CameraObservation() {
  const { active, cameras } = useVisionLive()
  const { data: trackData } = useCameraTracks(CAMERA_ID)
  const camera = cameras.find((c) => c.camera_id === CAMERA_ID)
  const likelySpeaker = (trackData?.identities.length ?? 0) > 0

  return (
    <Card>
      <CardHeader
        avatar={<CameraAltIcon color="primary" />}
        title="Camera Observation"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          !active ? (
            <Chip label="Demo data" size="small" color="warning" variant="outlined" sx={{ mt: 1, mr: 1 }} />
          ) : undefined
        }
      />
      <CardContent>
        <Stack spacing={1}>
          {active ? (
            camera ? (
              <>
                <Stat label={`Camera ${CAMERA_ID}`} value={`${camera.subject_count} person(s) detected`} />
                <Stat label="Shot" value={camera.shot} />
                <Stat label="Composition" value={`${Math.round(camera.overall_score * 100)}%`} />
                <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <Typography variant="body2" color="text.secondary">
                    Likely Speaker
                  </Typography>
                  <Chip
                    label={likelySpeaker ? 'Yes' : 'No'}
                    color={likelySpeaker ? 'success' : 'default'}
                    size="small"
                  />
                </Stack>
              </>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No data yet for camera {CAMERA_ID}
              </Typography>
            )
          ) : (
            <>
              <Stat label="Camera 1" value="1 person detected" />
              <Stat label="Shot" value="Medium Close" />
              <Stat label="Composition" value="94%" />
              <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  Likely Speaker
                </Typography>
                <Chip label="Yes" color="success" size="small" />
              </Stack>
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}
