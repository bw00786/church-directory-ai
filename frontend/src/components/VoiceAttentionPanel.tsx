import { useEffect, useState } from 'react'
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Card, CardContent,
  CardHeader, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, FormControlLabel, MenuItem, Paper, Stack, TextField, Typography,
} from '@mui/material'
import HeadsetMicIcon from '@mui/icons-material/HeadsetMic'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import {
  type VoiceAcknowledgement, type VoiceEvent, type VoiceFeedback, type VoiceMode,
  type VoiceSettings as VoiceSettingsData, type VoiceSettingsUpdate,
} from '@/api/voice'
import { useVoiceAttention } from '@/hooks/useVoiceAttention'

const priorities = ['Silent', 'Background', 'Attention', 'Warning', 'Critical'] as const
const feedbackOptions: VoiceFeedback[] = ['useful', 'not_useful', 'too_sensitive', 'too_late', 'correct', 'incorrect']
const modes: VoiceMode[] = ['attention_only', 'off', 'testing', 'emergency']
const label = (value: string) => value.replace(/_/g, ' ')
const routingCaution = 'Backend dedicated headset only. Physical PA/ATEM/stream and recording isolation must be verified onsite. Software routing flags and a queued test do not prove physical isolation. No browser audio is used.'

interface EventProps {
  event: VoiceEvent
  busy: boolean
  onAcknowledge: (id: string, acknowledgement: VoiceAcknowledgement) => Promise<boolean>
}

export function VoiceAlert({ event, busy, onAcknowledge }: EventProps) {
  const [feedback, setFeedback] = useState<VoiceFeedback | ''>('')
  const critical = event.priority === 4
  const reviewTarget = event.input.approval_token ? 'assistant-confirmation' : 'ai-director-panel'
  return (
    <Paper component="article" aria-label={`${priorities[event.priority]} alert`} variant="outlined"
      sx={{ p: 1.5, borderColor: critical ? 'error.main' : 'divider' }}>
      <Stack spacing={1}>
        <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
          <Chip size="small" label={`P${event.priority} · ${priorities[event.priority]}`}
            color={critical ? 'error' : event.priority === 3 ? 'warning' : 'default'} />
          <Typography variant="caption" color="text.secondary">
            <time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleString()}</time>
          </Typography>
          {event.resolved && <Chip size="small" label="Resolved" />}
          {event.acknowledged && <Chip size="small" label="Acknowledged" />}
        </Stack>
        <Typography variant="body2" role={critical && !event.acknowledged && !event.resolved ? 'alert' : undefined}>
          {event.message}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {event.input.event_type} · {event.input.source} / {event.input.resource} · {Math.round(event.input.confidence * 100)}% confidence
          {' · '}{event.spoken ? 'Spoken (backend reported)' : 'Not spoken'} · {event.result} · {label(event.mode)}
        </Typography>
        <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', gap: 1 }}>
          <Button size="small" disabled={busy || event.acknowledged || event.resolved}
            onClick={() => void onAcknowledge(event.id, { action: 'acknowledge' })}>Acknowledge</Button>
          <Button size="small" disabled={busy || event.acknowledged || event.resolved}
            onClick={() => void onAcknowledge(event.id, { action: 'dismiss' })}>Dismiss</Button>
          {(event.input.approval_token || event.input.operator_required) && (
            <Button size="small" component="a" href={`#${reviewTarget}`} onClick={() => {
              document.getElementById(reviewTarget)?.focus({ preventScroll: true })
            }}>Review in {event.input.approval_token ? 'Assistant' : 'Director'}</Button>
          )}
        </Stack>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <TextField select size="small" label="Alert feedback" value={feedback} disabled={busy}
            onChange={(e) => setFeedback(e.target.value as VoiceFeedback)} sx={{ minWidth: 160 }}>
            {feedbackOptions.map((value) => <MenuItem key={value} value={value}>{label(value)}</MenuItem>)}
          </TextField>
          <Button size="small" disabled={busy || !feedback} onClick={async () => {
            if (feedback && await onAcknowledge(event.id, { action: 'acknowledge', feedback })) setFeedback('')
          }}>Send feedback</Button>
        </Stack>
        {event.feedback && <Typography variant="caption">Feedback: {label(event.feedback)}</Typography>}
      </Stack>
    </Paper>
  )
}

export function VoiceQueue({ events, busy, onAcknowledge }: Omit<EventProps, 'event'> & { events: VoiceEvent[] }) {
  return (
    <Stack spacing={1}>
      <Typography component="h3" variant="subtitle2">Queue ({events.length})</Typography>
      {events.length === 0 && <Typography variant="body2" color="text.secondary">No pending alerts.</Typography>}
      <Box sx={{ maxHeight: 460, overflowY: 'auto' }}>
        <Stack spacing={1}>
          {events.map((event) => <VoiceAlert key={event.id} event={event} busy={busy} onAcknowledge={onAcknowledge} />)}
        </Stack>
      </Box>
    </Stack>
  )
}

interface SettingsProps {
  settings: VoiceSettingsData
  busy: boolean
  onSave: (update: VoiceSettingsUpdate) => Promise<boolean>
}

type TimingField = 'cooldown_seconds' | 'repeat_interval_seconds' | 'aggregation_window_seconds'
type SettingsDraft = Omit<VoiceSettingsData, TimingField | 'persona'> & Record<TimingField, number | ''> & {
  persona: Omit<VoiceSettingsData['persona'], 'speaking_rate' | 'expressiveness'> & {
    speaking_rate: number | ''
    expressiveness: number | ''
  }
}

export function VoiceSettings({ settings, busy, onSave }: SettingsProps) {
  const [draft, setDraft] = useState<SettingsDraft>(settings)
  const [dirty, setDirty] = useState(false)
  const [saved, setSaved] = useState(false)
  useEffect(() => { if (!dirty) setDraft(settings) }, [settings, dirty])
  const edit = (update: Partial<SettingsDraft>) => {
    setDraft((previous) => ({ ...previous, ...update }))
    setDirty(true)
    setSaved(false)
  }
  const timingFields = [
    ['cooldown_seconds', 'Cooldown (seconds)'],
    ['repeat_interval_seconds', 'Repeat interval (seconds)'],
    ['aggregation_window_seconds', 'Aggregation window (seconds)'],
  ] as const
  return (
    <Accordion>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography>Voice settings</Typography></AccordionSummary>
      <AccordionDetails>
        <Box component="form" onSubmit={async (e) => {
          e.preventDefault()
          const { mode, operator_name, cooldown_seconds, repeat_interval_seconds, aggregation_window_seconds, persona } = draft
          if (cooldown_seconds === '' || repeat_interval_seconds === '' || aggregation_window_seconds === '' ||
            persona.speaking_rate === '' || persona.expressiveness === '') return
          if (await onSave({ mode, operator_name, cooldown_seconds, repeat_interval_seconds, aggregation_window_seconds,
            persona: { ...persona, speaking_rate: persona.speaking_rate, expressiveness: persona.expressiveness },
          })) {
            setDirty(false)
            setSaved(true)
          }
        }}>
          <Stack spacing={2}>
            <Alert severity="warning">{routingCaution}</Alert>
            <Typography variant="body2">
              Server environment (read-only): device {settings.output_device || 'Not configured'} · host API {settings.output_host_api || 'Not configured'}
              {' · '}routing {settings.routing_verified ? 'verified (server reported)' : 'unverified'}
              {' · '}headset only: {String(settings.headset_only)} · church PA: {String(settings.church_pa)}
              {' · '}livestream: {String(settings.livestream)} · recording: {String(settings.recording)}
              {' · '}max event age: {settings.max_event_age_seconds}s · queue limit: {settings.queue_limit}
            </Typography>
            <TextField select label="Voice mode" value={draft.mode || 'attention_only'} disabled={busy}
              onChange={(e) => edit({ mode: e.target.value as VoiceMode })}>
              {modes.map((mode) => <MenuItem key={mode} value={mode}>{label(mode)}</MenuItem>)}
            </TextField>
            <Typography variant="caption" color="text.secondary">Default: attention only. Emergency speaks critical alerts only.</Typography>
            {draft.mode === 'testing' && <Alert severity="warning">TESTING may speak background events. Use only during an onsite headset test.</Alert>}
            <TextField label="Operator name" value={draft.operator_name} disabled={busy}
              onChange={(e) => edit({ operator_name: e.target.value })} />
            <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' } }}>
              {timingFields.map(([field, title]) => (
                <TextField key={field} label={title} type="number" required value={draft[field]} disabled={busy}
                  slotProps={{ htmlInput: { min: 0, step: 'any' } }}
                  onChange={(e) => edit({ [field]: e.target.value === '' ? '' : Number(e.target.value) })} />
              ))}
            </Box>
            <Typography variant="body2">Persona: female · warm conversational · en-US · neutral pitch · subtle breathiness</Typography>
            <TextField label="Voice ID" value={draft.persona.voice_id} disabled={busy}
              onChange={(e) => edit({ persona: { ...draft.persona, voice_id: e.target.value } })} />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField label="Speaking rate" type="number" required value={draft.persona.speaking_rate} disabled={busy}
                slotProps={{ htmlInput: { min: 0.75, max: 1.2, step: 0.01 } }}
                onChange={(e) => edit({ persona: { ...draft.persona, speaking_rate: e.target.value === '' ? '' : Number(e.target.value) } })} />
              <TextField label="Expressiveness" type="number" required value={draft.persona.expressiveness} disabled={busy}
                slotProps={{ htmlInput: { min: 0, max: 1, step: 0.01 } }}
                onChange={(e) => edit({ persona: { ...draft.persona, expressiveness: e.target.value === '' ? '' : Number(e.target.value) } })} />
            </Stack>
            <Stack direction="row" spacing={1}>
              <Button type="submit" variant="outlined" disabled={busy || !dirty}>Save voice settings</Button>
              <Button disabled={busy || !dirty} onClick={() => { setDraft(settings); setDirty(false); setSaved(false) }}>Reset changes</Button>
            </Stack>
            {saved && <Alert severity="success">Voice settings saved.</Alert>}
          </Stack>
        </Box>
      </AccordionDetails>
    </Accordion>
  )
}

export function VoiceTestDialog({ open, busy, onClose, onTest }: {
  open: boolean; busy: boolean; onClose: () => void; onTest: () => Promise<boolean>
}) {
  const [verified, setVerified] = useState(false)
  const [queued, setQueued] = useState(false)
  const [failed, setFailed] = useState(false)
  useEffect(() => { if (open) { setVerified(false); setQueued(false); setFailed(false) } }, [open])
  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} aria-labelledby="voice-test-title">
      <DialogTitle id="voice-test-title">Test dedicated headset</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Alert severity="warning">{routingCaution}</Alert>
          <Typography variant="body2">This requests a backend test, not browser playback. TESTING mode may also speak background events.</Typography>
          <FormControlLabel control={<Checkbox checked={verified} disabled={busy || queued}
            onChange={(e) => setVerified(e.target.checked)} />}
            label="I have verified physical headset isolation onsite for this test." />
          {queued && <Alert severity="info">Test queued. Listen at the dedicated headset; this does not guarantee physical routing or confirm playback.</Alert>}
          {failed && <Alert severity="error">Headset test request failed. Check the voice service and try again.</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={busy} onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={busy || !verified || queued} onClick={async () => {
          setFailed(false)
          if (await onTest()) setQueued(true)
          else setFailed(true)
        }}>Queue headset test</Button>
      </DialogActions>
    </Dialog>
  )
}

export function VoiceAttentionPanel() {
  const voice = useVoiceAttention()
  const [testOpen, setTestOpen] = useState(false)
  const { status, busy } = voice
  const eventProps = { busy, onAcknowledge: voice.acknowledge }
  const disabled = busy || !status
  return (
    <Card component="section" aria-labelledby="voice-attention-title">
      <CardHeader avatar={<HeadsetMicIcon color="secondary" />}
        title={<Typography id="voice-attention-title" component="h2" variant="subtitle2">Voice attention</Typography>}
        subheader="Private operator attention · backend headset only" />
      <CardContent>
        <Stack spacing={2}>
          {voice.loading && <Stack direction="row" spacing={1}><CircularProgress size={18} aria-label="Loading voice attention" /><Typography>Loading voice attention…</Typography></Stack>}
          {voice.error && <Alert severity="error" action={<Button color="inherit" disabled={busy} onClick={() => void voice.refresh()}>Retry</Button>}>{voice.error}</Alert>}
          {status?.error && <Alert severity="error">{status.error}</Alert>}
          {status?.headset.error && <Alert severity="error">Headset: {status.headset.error}</Alert>}
          <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', gap: 1 }}>
            <Chip size="small" label={voice.connected ? 'Live updates' : 'Reconnecting · polling every 5s'} variant="outlined" />
            <Chip size="small" label={status ? status.enabled ? 'Enabled' : 'Disabled' : 'Status unavailable'} />
            <Chip size="small" label={`Mode: ${label(status?.mode ?? 'attention_only')}`} />
            {status && <><Chip size="small" label={status.muted ? 'Muted' : 'Unmuted'} /><Chip size="small" label={`Speech: ${status.speech_state}`} /></>}
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Headset: {status?.headset.device || 'Not configured'} · {status?.headset.ready ? 'Ready' : 'Not ready'}
            {' · '}{status?.headset.verified ? 'Routing verified (server reported)' : 'Routing unverified'}
          </Typography>
          <Alert severity="warning">{routingCaution}</Alert>
          {status?.mode === 'testing' && <Alert severity="warning">TESTING may speak background events. Return to attention only for normal operation.</Alert>}
          <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', gap: 1 }}>
            <Button variant="outlined" disabled={disabled} onClick={() => void voice.command(status?.enabled ? 'disable' : 'enable')}>{status?.enabled ? 'Disable voice' : 'Enable voice'}</Button>
            <Button variant="outlined" disabled={disabled} onClick={() => void voice.command(status?.muted ? 'unmute' : 'mute')}>{status?.muted ? 'Unmute voice' : 'Mute voice'}</Button>
            <Button disabled={disabled || !status?.enabled || status.muted || !status.last_alert} onClick={() => void voice.command('repeat')}>Repeat last alert</Button>
            <Button disabled={disabled} onClick={() => setTestOpen(true)}>Test headset</Button>
          </Stack>
          <Typography variant="caption" color="text.secondary">Acknowledgement does not approve a production action. Review and confirm production actions in Assistant or Director.</Typography>
          {status?.current && <Box><Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>Current alert</Typography><VoiceAlert key={status.current.id} event={status.current} {...eventProps} /></Box>}
          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
            <Stack spacing={1}>
              <Typography component="h3" variant="subtitle2">Last alert</Typography>
              {status?.last_alert ? <VoiceAlert key={status.last_alert.id} event={status.last_alert} {...eventProps} /> : <Typography variant="body2" color="text.secondary">No last alert.</Typography>}
            </Stack>
            <VoiceQueue events={status?.pending ?? []} {...eventProps} />
          </Box>
          <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography>Recent alerts ({voice.events.length})</Typography></AccordionSummary>
            <AccordionDetails>
              <Stack spacing={1} sx={{ maxHeight: 500, overflowY: 'auto' }}>
                {!voice.events.length && <Typography variant="body2">No recent alerts.</Typography>}
                {voice.events.map((event) => <VoiceAlert key={event.id} event={event} {...eventProps} />)}
              </Stack>
            </AccordionDetails>
          </Accordion>
          {voice.settings ? <VoiceSettings settings={voice.settings} busy={busy} onSave={voice.saveSettings} /> : !voice.loading && <Alert severity="info">Voice settings unavailable. Retry to load configuration.</Alert>}
          {status && <Accordion>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography>Voice diagnostics</Typography></AccordionSummary>
            <AccordionDetails>
              <Typography variant="body2">Audit: {status.audit_available ? 'available' : 'unavailable'}</Typography>
              <Typography variant="subtitle2">Cooldowns (seconds)</Typography>
              {Object.entries(status.cooldowns).map(([key, value]) => <Typography key={key} variant="body2">{key}: {value}</Typography>)}
              <Typography variant="subtitle2">Metrics</Typography>
              {Object.entries(status.metrics).map(([key, value]) => <Typography key={key} variant="body2">{label(key)}: {value}</Typography>)}
            </AccordionDetails>
          </Accordion>}
        </Stack>
      </CardContent>
      <VoiceTestDialog open={testOpen} busy={busy} onClose={() => setTestOpen(false)} onTest={() => voice.command('test')} />
    </Card>
  )
}