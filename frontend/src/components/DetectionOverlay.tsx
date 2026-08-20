import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Typography from '@mui/material/Typography'
import CropFreeIcon from '@mui/icons-material/CropFree'

import { useCameraTracks } from '@/hooks/useCameraTracks'

const CAMERA_ID = 1

export function DetectionOverlay() {
  const { active, data } = useCameraTracks(CAMERA_ID)
  const track = data?.tracks[0]
  const identity = data?.identities[0]
  const frameSize = data?.frame_size

  const box =
    track && frameSize
      ? {
          left: (track.bbox[0] / frameSize.width) * 100,
          top: (track.bbox[1] / frameSize.height) * 100,
          width: (track.bbox[2] / frameSize.width) * 100,
          height: (track.bbox[3] / frameSize.height) * 100,
        }
      : null

  return (
    <Card>
      <CardHeader
        avatar={<CropFreeIcon color="primary" />}
        title="Detection Overlay"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          !active ? (
            <Chip label="Demo data" size="small" color="warning" variant="outlined" sx={{ mt: 1, mr: 1 }} />
          ) : undefined
        }
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
          {active ? (
            box && track ? (
              <>
                <Box
                  sx={{
                    position: 'absolute',
                    left: `${box.left}%`,
                    top: `${box.top}%`,
                    width: `${box.width}%`,
                    height: `${box.height}%`,
                    border: '2px solid',
                    borderColor: 'success.main',
                    borderRadius: 1,
                  }}
                />
                <Typography
                  variant="caption"
                  sx={{
                    position: 'absolute',
                    left: `${box.left}%`,
                    top: `max(4px, calc(${box.top}% - 16px))`,
                    color: 'success.light',
                  }}
                >
                  {identity ? identity.name.toUpperCase() : `PERSON ${track.person_id}`}
                </Typography>
              </>
            ) : (
              <Typography
                variant="caption"
                sx={{ position: 'absolute', left: 12, top: 12, color: 'text.secondary' }}
              >
                No person detected
              </Typography>
            )
          ) : (
            <>
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
            </>
          )}
        </Box>
      </CardContent>
    </Card>
  )
}
