import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import CameraAltIcon from '@mui/icons-material/CameraAlt'

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
  return (
    <Card>
      <CardHeader
        avatar={<CameraAltIcon color="primary" />}
        title="Camera Observation"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        <Stack spacing={1}>
          <Stat label="Camera 1" value="1 person detected" />
          <Stat label="Shot" value="Medium Close" />
          <Stat label="Composition" value="94%" />
          <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography variant="body2" color="text.secondary">
              Likely Speaker
            </Typography>
            <Chip label="Yes" color="success" size="small" />
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  )
}
