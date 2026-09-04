/**
 * EasyWorship slides panel
 * Manual slide control (Next / Prev / Item nav / Clear / Live) + connection status.
 */

import React, { useEffect, useState } from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Typography from '@mui/material/Typography'
import SlideshowIcon from '@mui/icons-material/Slideshow'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'
import SkipPreviousIcon from '@mui/icons-material/SkipPrevious'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import ClearIcon from '@mui/icons-material/Clear'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'

import { easyworshipAPI } from '@/api/atem'

export function SlidesPanel() {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [lastAction, setLastAction] = useState<string | null>(null)
  const [driver, setDriver] = useState<string | null>(null)
  const [position, setPosition] = useState<{ item: number | null; slide: number | null } | null>(null)

  const refresh = async () => {
    try {
      const res = await easyworshipAPI.getStatus()
      setConnected(Boolean(res.data.connected))
      setLastAction(res.data.last_action ?? null)
      setDriver(res.data.driver ?? null)
      const remote = res.data.remote_state
      setPosition(remote ? { item: remote.pres_no ?? null, slide: remote.slide_no ?? null } : null)
    } catch {
      setConnected(false)
    }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [])

  const run = async (name: string) => {
    try {
      await easyworshipAPI.action(name)
      setLastAction(name)
    } catch {
      setLastAction(`${name} (failed)`)
    }
  }

  return (
    <Card>
      <CardHeader
        avatar={<SlideshowIcon color="primary" />}
        title="Slides"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <Chip
            label={connected == null ? '…' : connected ? 'connected' : 'offline'}
            color={connected == null ? 'default' : connected ? 'success' : 'error'}
            size="small"
            sx={{ mt: 1, mr: 1 }}
          />
        }
      />
      <CardContent>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1, mb: 1 }}>
          <Button variant="outlined" startIcon={<ArrowBackIcon />} onClick={() => run('prev_slide')}>
            Prev
          </Button>
          <Button
            variant="outlined"
            endIcon={<ArrowForwardIcon />}
            onClick={() => run('next_slide')}
          >
            Next
          </Button>
          <Button
            variant="outlined"
            startIcon={<SkipPreviousIcon />}
            onClick={() => run('prev_item')}
          >
            Item
          </Button>
          <Button variant="outlined" endIcon={<SkipNextIcon />} onClick={() => run('next_item')}>
            Item
          </Button>
        </Box>

        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1 }}>
          <Button variant="contained" color="success" startIcon={<PlayArrowIcon />} onClick={() => run('live')}>
            Live
          </Button>
          <Button variant="outlined" startIcon={<ClearIcon />} onClick={() => run('clear')}>
            Clear
          </Button>
          <Button variant="outlined" startIcon={<VisibilityOffIcon />} onClick={() => run('black')}>
            Black
          </Button>
        </Box>

        {position && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: 'block' }}>
            EasyWorship: item {position.item ?? '—'} / slide {position.slide ?? '—'}
          </Typography>
        )}
        {(lastAction || driver) && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: position ? 0 : 2, display: 'block' }}>
            {lastAction ? `last: ${lastAction}` : ''}
            {lastAction && driver ? ' · ' : ''}
            {driver ?? ''}
          </Typography>
        )}
      </CardContent>
    </Card>
  )
}
