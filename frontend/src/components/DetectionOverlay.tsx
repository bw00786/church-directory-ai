import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import CropFreeIcon from '@mui/icons-material/CropFree'

export function DetectionOverlay() {
  return (
    <Card>
      <CardHeader
        avatar={<CropFreeIcon color="primary" />}
        title="Detection Overlay"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        <Box
          sx={{
            position: 'relative',
            height: 160,
            width: '100%',
            borderRadius: 1,
            border: '1px solid',
            borderColor: 'divider',
            bgcolor: '#0b1120',
            overflow: 'hidden',
          }}
        >
          <Box
            sx={{
              position: 'absolute',
              left: 48,
              top: 28,
              width: 80,
              height: 112,
              border: '2px solid',
              borderColor: 'success.main',
              borderRadius: 1,
            }}
          />
          <Typography
            variant="caption"
            sx={{ position: 'absolute', left: 64, top: 16, color: 'success.light' }}
          >
            PERSON 17
          </Typography>
          <Typography
            variant="caption"
            sx={{ position: 'absolute', right: 20, bottom: 20, color: 'primary.light' }}
          >
            Composition: 94%
          </Typography>
        </Box>
      </CardContent>
    </Card>
  )
}
