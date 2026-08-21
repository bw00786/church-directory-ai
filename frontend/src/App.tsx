import React from 'react'
import AppBar from '@mui/material/AppBar'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import Box from '@mui/material/Box'
import Container from '@mui/material/Container'
import Stack from '@mui/material/Stack'
import Chip from '@mui/material/Chip'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import ChurchIcon from '@mui/icons-material/Church'

import { AiObservation } from './components/AiObservation'
import { AssistantChat } from './components/AssistantChat'
import { AtemPanel } from './components/AtemPanel'
import { CameraJoystick } from './components/CameraJoystick'
import { CameraObservation } from './components/CameraObservation'
import { CueSheet } from './components/CueSheet'
import { DetectionOverlay } from './components/DetectionOverlay'
import { EventTimeline } from './components/EventTimeline'
import { RosterPanel } from './components/RosterPanel'
import { SlidesPanel } from './components/SlidesPanel'
import { VisionPanel } from './components/VisionPanel'

export function App() {
  return (
    <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
      <AppBar position="static" color="transparent" elevation={0} sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Toolbar sx={{ py: 1.5, gap: 2 }}>
          <ChurchIcon color="primary" fontSize="large" />
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" component="h1" sx={{ lineHeight: 1.2 }}>
              Church Production Director
            </Typography>
            <Typography variant="body2" color="text.secondary">
              AI-assisted worship production control
            </Typography>
          </Box>
          <Chip
            icon={<FiberManualRecordIcon sx={{ fontSize: 12 }} />}
            label="System Online"
            color="success"
            variant="outlined"
            size="small"
          />
        </Toolbar>
      </AppBar>

      <Container maxWidth="xl" sx={{ py: 3 }}>
        <Stack spacing={3}>
          <Box
            sx={{
              display: 'grid',
              gap: 3,
              gridTemplateColumns: {
                xs: '1fr',
                md: 'repeat(2, 1fr)',
                lg: 'repeat(4, 1fr)',
              },
            }}
          >
            <VisionPanel />
            <CameraObservation />
            <DetectionOverlay />
            <AiObservation />
          </Box>

          <Box
            sx={{
              display: 'grid',
              gap: 3,
              alignItems: 'start',
              gridTemplateColumns: {
                xs: '1fr',
                md: '1fr 1fr',
                lg: '2fr 1fr 1fr 1fr',
              },
            }}
          >
            <CueSheet />
            <AtemPanel />
            <CameraJoystick cameraId={1} />
            <SlidesPanel />
          </Box>

          <RosterPanel />

          <AssistantChat />

          <EventTimeline />
        </Stack>
      </Container>
    </Box>
  )
}

export default App
