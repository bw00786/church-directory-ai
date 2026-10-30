/**
 * Prompts for the control-plane API key when the backend answers 401.
 * Mount once near the app root; it renders nothing until needed.
 */

import { useEffect, useState } from 'react'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogContentText from '@mui/material/DialogContentText'
import DialogTitle from '@mui/material/DialogTitle'
import TextField from '@mui/material/TextField'

import { getApiKey, onUnauthorized, setApiKey } from '@/api/client'

export function ApiKeyGate() {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<string | null>(null)
  const [value, setValue] = useState('')

  useEffect(
    () =>
      onUnauthorized((message) => {
        setDetail(message)
        setOpen(true)
      }),
    [],
  )

  const hadKey = Boolean(getApiKey())
  const trimmed = value.trim()

  const save = () => {
    if (!trimmed) return
    setApiKey(trimmed)
    setOpen(false)
    // Open WebSockets were refused without the token; a reload reconnects everything with it.
    window.location.reload()
  }

  return (
    <Dialog open={open} aria-labelledby="api-key-gate-title">
      <DialogTitle id="api-key-gate-title">API key required</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          {hadKey
            ? 'The backend rejected the stored API key. Enter the current operator or admin key.'
            : 'This control surface is protected. Enter the operator or admin API key from the backend .env (API_KEY / API_ADMIN_KEY).'}
          {detail ? ` (${detail})` : ''}
        </DialogContentText>
        <TextField
          autoFocus
          fullWidth
          type="password"
          label="API key"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') save()
          }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={save} disabled={!trimmed} variant="contained">
          Save and reload
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default ApiKeyGate
