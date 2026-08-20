/**
 * Roster & identity memory panel
 * Manage known people (pastor, liturgist, vocalist, ...), enroll face/voice
 * samples, and review recent face/voice recognition activity.
 */

import React, { useRef, useState } from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import CardContent from '@mui/material/CardContent'
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import Button from '@mui/material/Button'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import MenuItem from '@mui/material/MenuItem'
import Alert from '@mui/material/Alert'
import Divider from '@mui/material/Divider'
import GroupIcon from '@mui/icons-material/Group'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import DeleteIcon from '@mui/icons-material/Delete'
import FaceIcon from '@mui/icons-material/Face'
import MicIcon from '@mui/icons-material/Mic'

import { useRoster } from '@/hooks/useRoster'

const ROLES = ['pastor', 'liturgist', 'vocalist', 'musician', 'tech', 'congregation', 'guest', 'unknown']

function formatTimestamp(value: string | number | null | undefined): string {
  if (value == null) return 'never'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? 'never' : date.toLocaleTimeString()
}

const activityColor: Record<string, 'success' | 'info' | 'default'> = {
  singing: 'success',
  speech: 'info',
  silence: 'default',
}

export function RosterPanel() {
  const {
    roster,
    loading,
    error,
    observations,
    audioStatus,
    voiceActivity,
    addPerson,
    removePerson,
    enrollFace,
    enrollVoice,
  } = useRoster()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState('unknown')
  const [notes, setNotes] = useState('')
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [pendingPersonId, setPendingPersonId] = useState<string | null>(null)

  const faceInputRef = useRef<HTMLInputElement>(null)
  const voiceInputRef = useRef<HTMLInputElement>(null)

  const handleAddPerson = async () => {
    if (!name.trim()) return
    try {
      await addPerson(name.trim(), role, notes.trim() || undefined)
      setStatusMessage(`Added ${name.trim()} to the roster`)
      setName('')
      setRole('unknown')
      setNotes('')
      setDialogOpen(false)
    } catch {
      setStatusMessage('Failed to add person')
    }
  }

  const handleRemove = async (personId: string, personName: string) => {
    try {
      await removePerson(personId)
      setStatusMessage(`Removed ${personName} from the roster`)
    } catch {
      setStatusMessage(`Failed to remove ${personName}`)
    }
  }

  const triggerFaceUpload = (personId: string) => {
    setPendingPersonId(personId)
    faceInputRef.current?.click()
  }

  const triggerVoiceUpload = (personId: string) => {
    setPendingPersonId(personId)
    voiceInputRef.current?.click()
  }

  const handleFaceFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !pendingPersonId) return
    try {
      await enrollFace(pendingPersonId, file)
      setStatusMessage('Face sample enrolled')
    } catch {
      setStatusMessage('Failed to enroll face sample (no face detected?)')
    }
  }

  const handleVoiceFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !pendingPersonId) return
    try {
      await enrollVoice(pendingPersonId, file)
      setStatusMessage('Voice sample enrolled (16-bit PCM WAV)')
    } catch {
      setStatusMessage('Failed to enroll voice sample')
    }
  }

  return (
    <Card>
      <CardHeader
        avatar={<GroupIcon color="primary" />}
        title="Roster & Identity Memory"
        subheader="Known people the system can recognize by face and voice"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <Button
            variant="contained"
            size="small"
            startIcon={<PersonAddIcon />}
            onClick={() => setDialogOpen(true)}
            sx={{ mt: 1, mr: 1 }}
          >
            Add Person
          </Button>
        }
      />
      <CardContent>
        {error && (
          <Alert severity="error" variant="outlined" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Role</TableCell>
              <TableCell align="right">Seen</TableCell>
              <TableCell align="right">Last seen</TableCell>
              <TableCell align="right">Enroll</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {roster.map((person) => (
              <TableRow key={person.id}>
                <TableCell>{person.name}</TableCell>
                <TableCell>
                  <Chip label={person.role} size="small" />
                </TableCell>
                <TableCell align="right">{person.appearance_count}</TableCell>
                <TableCell align="right">{formatTimestamp(person.last_seen_at)}</TableCell>
                <TableCell align="right">
                  <IconButton size="small" title="Enroll face photo" onClick={() => triggerFaceUpload(person.id)}>
                    <FaceIcon fontSize="small" />
                  </IconButton>
                  <IconButton size="small" title="Enroll voice sample (WAV)" onClick={() => triggerVoiceUpload(person.id)}>
                    <MicIcon fontSize="small" />
                  </IconButton>
                  <IconButton
                    size="small"
                    title="Remove from roster"
                    onClick={() => handleRemove(person.id, person.name)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
            {!loading && roster.length === 0 && (
              <TableRow>
                <TableCell colSpan={5}>
                  <Typography variant="body2" color="text.secondary">
                    No one enrolled yet. Add a person, then enroll a face photo and/or voice sample.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>

        <input ref={faceInputRef} type="file" accept="image/*" hidden onChange={handleFaceFile} />
        <input ref={voiceInputRef} type="file" accept="audio/wav" hidden onChange={handleVoiceFile} />

        {statusMessage && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            {statusMessage}
          </Typography>
        )}

        <Divider sx={{ my: 2 }} />

        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
          <Box>
            <Typography variant="overline" color="text.secondary">
              Recent face matches
            </Typography>
            <Stack spacing={0.5} sx={{ mt: 0.5 }}>
              {observations.length === 0 && (
                <Typography variant="body2" color="text.disabled">
                  No recognition activity yet
                </Typography>
              )}
              {observations.map((observation) => (
                <Stack key={observation.id} direction="row" sx={{ justifyContent: 'space-between' }}>
                  <Typography variant="body2">
                    {observation.person_name ?? 'unknown'}
                    {observation.role && ` (${observation.role})`}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {Math.round(observation.confidence * 100)}%
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Box>

          <Box>
            <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography variant="overline" color="text.secondary">
                Voice activity
              </Typography>
              <Chip
                label={audioStatus?.running ? 'capturing' : audioStatus?.enabled ? 'starting' : 'disabled'}
                color={audioStatus?.running ? 'success' : 'default'}
                size="small"
              />
            </Stack>
            <Stack spacing={0.5} sx={{ mt: 0.5 }}>
              {voiceActivity.length === 0 && (
                <Typography variant="body2" color="text.disabled">
                  {audioStatus?.enabled
                    ? 'Listening for audio...'
                    : 'Local audio capture disabled (set ENABLE_AUDIO_CAPTURE=true)'}
                </Typography>
              )}
              {voiceActivity.map((activity, index) => (
                <Stack key={index} direction="row" sx={{ justifyContent: 'space-between' }}>
                  <Typography variant="body2">{activity.name ?? 'silence'}</Typography>
                  <Chip
                    label={activity.activity}
                    size="small"
                    color={activityColor[activity.activity] ?? 'default'}
                  />
                </Stack>
              ))}
            </Stack>
          </Box>
        </Box>
      </CardContent>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Add Person</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              fullWidth
            />
            <TextField label="Role" select value={role} onChange={(e) => setRole(e.target.value)} fullWidth>
              {ROLES.map((option) => (
                <MenuItem key={option} value={option}>
                  {option}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Notes (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleAddPerson} disabled={!name.trim()}>
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  )
}
