/**
 * AI assistant chat panel
 * Answers questions about past services/roster and controls production
 * subsystems. High-risk actions (streaming/recording/mic) show a
 * Confirm/Cancel prompt instead of executing immediately.
 */

import React, { useState } from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Box from '@mui/material/Box'
import TextField from '@mui/material/TextField'
import IconButton from '@mui/material/IconButton'
import Typography from '@mui/material/Typography'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import CircularProgress from '@mui/material/CircularProgress'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import SendIcon from '@mui/icons-material/Send'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'

import { useAssistant } from '@/hooks/useAssistant'

export function AssistantChat() {
  const { messages, pending, sending, error, send, confirmPending, cancelPending } = useAssistant()
  const [input, setInput] = useState('')

  const handleSend = () => {
    const text = input
    setInput('')
    send(text)
  }

  return (
    <Card>
      <CardHeader
        avatar={<SmartToyIcon color="secondary" />}
        title="Assistant"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        <Stack spacing={1.5}>
          <Box sx={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 }}>
            {messages.length === 0 && (
              <Typography variant="caption" color="text.secondary">
                Ask about past services ("who preached last Sunday?"), or control cameras, slides, and the
                cue sheet.
              </Typography>
            )}
            {messages.map((m, i) => (
              <Box
                key={i}
                sx={{
                  alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                  bgcolor: m.role === 'user' ? 'primary.dark' : 'action.hover',
                  borderRadius: 2,
                  px: 1.5,
                  py: 0.75,
                  maxWidth: '85%',
                }}
              >
                <Typography variant="body2">{m.content}</Typography>
              </Box>
            ))}
            {sending && (
              <Stack direction="row" sx={{ alignItems: 'center', gap: 1, alignSelf: 'flex-start' }}>
                <CircularProgress size={14} />
                <Typography variant="caption" color="text.secondary">
                  Thinking…
                </Typography>
              </Stack>
            )}
          </Box>

          {error && (
            <Alert severity="warning" variant="outlined">
              {error}
            </Alert>
          )}

          {pending && (
            <Alert
              severity="warning"
              variant="outlined"
              icon={<WarningAmberIcon />}
              action={
                <Stack direction="row" spacing={1}>
                  <Button size="small" color="inherit" onClick={cancelPending}>
                    Cancel
                  </Button>
                  <Button size="small" color="error" variant="contained" onClick={confirmPending}>
                    Confirm
                  </Button>
                </Stack>
              }
            >
              Needs confirmation: {pending.description}
            </Alert>
          )}

          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              fullWidth
              placeholder="Ask or command the system…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleSend()
                }
              }}
            />
            <IconButton color="primary" onClick={handleSend} disabled={sending || !input.trim()}>
              <SendIcon />
            </IconButton>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  )
}
