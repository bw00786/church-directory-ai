/**
 * First-run setup wizard.
 *
 * Opens automatically when GET /api/setup/status reports missing required
 * settings (once per browser session) and can be reopened from the toolbar
 * via <SetupWizardButton />. Steps: Welcome -> Network discovery -> Cameras ->
 * Audio -> Slides -> Security -> Review & Save. Only fields the operator
 * touched are written (PUT /api/setup/env, admin key); a restart applies them.
 */

import React, { useCallback, useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import CircularProgress from '@mui/material/CircularProgress'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import FormControlLabel from '@mui/material/FormControlLabel'
import Stack from '@mui/material/Stack'
import Step from '@mui/material/Step'
import StepLabel from '@mui/material/StepLabel'
import Stepper from '@mui/material/Stepper'
import Switch from '@mui/material/Switch'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import SettingsIcon from '@mui/icons-material/Settings'

import type { AudioDevice, DiscoverKind, DiscoverResult, EnvValues, SetupStatus, TestComponent, TestRequest, TestResult } from '@/api/setup'
import { errorDetail, useSetup } from '@/hooks/useSetup'

export const STEPS = ['Welcome', 'Network', 'Cameras', 'Audio', 'Slides', 'Security', 'Review & Save'] as const
const DISMISSED_KEY = 'cpd.setupWizardDismissed'
const SECRET_KEYS = new Set(['CAMERA_1_PASSWORD'])

// Toolbar button and dialog live in different parts of the tree; a tiny
// listener set (same pattern as onUnauthorized) connects them.
const openListeners = new Set<() => void>()
export function openSetupWizard(): void {
  openListeners.forEach((listener) => listener())
}

export function SetupWizardButton() {
  return (
    <Button size="small" variant="outlined" startIcon={<SettingsIcon />} onClick={openSetupWizard}>
      Setup
    </Button>
  )
}

type Draft = Record<string, string | boolean>
type Probe = { busy: boolean; text: string | null; ok: boolean | null }
const idle: Probe = { busy: false, text: null, ok: null }

interface StepProps {
  status: SetupStatus | null
  draft: Draft
  set: (key: string, value: string | boolean) => void
  discover: (kind: DiscoverKind) => Promise<DiscoverResult>
  test: (component: TestComponent, body?: TestRequest) => Promise<TestResult>
  audioDevices: () => Promise<{ devices: AudioDevice[]; note: string | null }>
}

function itemValue(status: SetupStatus | null, key: string): string {
  return status?.items.find((i) => i.key === key)?.value ?? ''
}

function ProbeResult({ probe }: { probe: Probe }) {
  if (probe.busy) return <CircularProgress size={16} aria-label="working" />
  if (!probe.text) return null
  return <Chip size="small" color={probe.ok ? 'success' : 'warning'} label={probe.text} sx={{ maxWidth: 420 }} />
}

function useProbe(): [Probe, (run: () => Promise<{ ok: boolean; text: string }>) => Promise<void>] {
  const [probe, setProbe] = useState<Probe>(idle)
  const run = useCallback(async (fn: () => Promise<{ ok: boolean; text: string }>) => {
    setProbe({ busy: true, text: null, ok: null })
    try {
      const result = await fn()
      setProbe({ busy: false, text: result.text, ok: result.ok })
    } catch (err) {
      setProbe({ busy: false, text: errorDetail(err, 'request failed'), ok: false })
    }
  }, [])
  return [probe, run]
}

function Field({ label, k, draft, set, placeholder, type = 'text', helper }: { label: string; k: string; draft: Draft; set: StepProps['set']; placeholder?: string; type?: string; helper?: string }) {
  const value = draft[k]
  return (
    <TextField
      size="small"
      fullWidth
      label={label}
      type={type}
      value={typeof value === 'string' ? value : ''}
      placeholder={placeholder || undefined}
      helperText={helper}
      onChange={(e) => set(k, e.target.value)}
      slotProps={{ htmlInput: { 'aria-label': label } }}
    />
  )
}

function Toggle({ label, k, draft, set, current }: { label: string; k: string; draft: Draft; set: StepProps['set']; current: boolean }) {
  const value = typeof draft[k] === 'boolean' ? (draft[k] as boolean) : current
  return <FormControlLabel control={<Switch checked={value} onChange={(e) => set(k, e.target.checked)} />} label={label} />
}

function WelcomeStep({ status, draft, set }: StepProps) {
  const missing = status?.items.filter((i) => i.missing) ?? []
  return (
    <Stack spacing={2}>
      <Typography variant="body2">
        This wizard collects the hardware addresses and credentials the director needs. Nothing is changed until you save on the
        last step; the backend then needs a restart.
      </Typography>
      {missing.length > 0 ? (
        <Alert severity="warning">
          Missing required settings:{' '}
          {missing.map((i) => (
            <Chip key={i.key} size="small" label={i.label} sx={{ mr: 0.5 }} title={i.hint} />
          ))}
        </Alert>
      ) : (
        <Alert severity="success">All required settings are configured.</Alert>
      )}
      <Typography variant="overline" color="text.secondary">
        Hardware mode (mock = simulated, no hardware)
      </Typography>
      <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: 'wrap' }}>
        <Toggle label="Mock ATEM" k="ENABLE_MOCK_ATEM" draft={draft} set={set} current={status?.mocks.atem ?? true} />
        <Toggle label="Mock mixer" k="ENABLE_MOCK_MIXER" draft={draft} set={set} current={status?.mocks.mixer ?? true} />
        <Toggle label="Mock EasyWorship" k="ENABLE_MOCK_EASYWORSHIP" draft={draft} set={set} current={status?.mocks.easyworship ?? true} />
      </Stack>
    </Stack>
  )
}

function NetworkStep({ status, draft, set, discover }: StepProps) {
  const [atem, runAtem] = useProbe()
  const [ew, runEw] = useProbe()
  const [ollama, runOllama] = useProbe()
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
        <Button
          variant="outlined"
          size="small"
          onClick={() =>
            runAtem(async () => {
              const r = await discover('atem')
              if (typeof r.found === 'string') set('ATEM_IP', r.found)
              return { ok: Boolean(r.found), text: r.detail + (typeof r.found === 'string' ? ` (${r.found})` : '') }
            })
          }
        >
          Discover ATEM
        </Button>
        <ProbeResult probe={atem} />
      </Stack>
      <Field label="ATEM_IP" k="ATEM_IP" draft={draft} set={set} placeholder={itemValue(status, 'atem')} />
      <Field label="ATEM_RESOLVE_SUBNETS" k="ATEM_RESOLVE_SUBNETS" draft={draft} set={set} placeholder="192.168.4" helper="Comma-separated /24 prefixes swept to find the ATEM by MAC when DHCP moves it" />

      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
        <Button
          variant="outlined"
          size="small"
          onClick={() =>
            runEw(async () => {
              const r = await discover('easyworship')
              if (r.found && typeof r.found === 'object') set('EASYWORSHIP_REMOTE_HOST', r.found.host)
              return { ok: Boolean(r.found), text: r.detail }
            })
          }
        >
          Discover EasyWorship
        </Button>
        <ProbeResult probe={ew} />
      </Stack>
      <Field label="EASYWORSHIP_REMOTE_HOST" k="EASYWORSHIP_REMOTE_HOST" draft={draft} set={set} placeholder={itemValue(status, 'easyworship')} />

      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
        <Button
          variant="outlined"
          size="small"
          onClick={() =>
            runOllama(async () => {
              const r = await discover('ollama')
              if (typeof r.found === 'string') set('OLLAMA_BASE_URL', r.base_url ?? `http://${r.found}:11434`)
              return { ok: Boolean(r.found), text: r.detail }
            })
          }
        >
          Discover Ollama
        </Button>
        <ProbeResult probe={ollama} />
      </Stack>
      <Field label="OLLAMA_BASE_URL" k="OLLAMA_BASE_URL" draft={draft} set={set} placeholder={itemValue(status, 'ollama')} helper="Leave empty to keep auto-discovery on the EasyWorship PC" />
    </Stack>
  )
}

function CamerasStep({ status, draft, set, test }: StepProps) {
  const [probe, run] = useProbe()
  const host = (draft.CAMERA_1_HOST as string) || itemValue(status, 'camera_1')
  return (
    <Stack spacing={2}>
      <Field label="CAMERA_1_HOST" k="CAMERA_1_HOST" draft={draft} set={set} placeholder={itemValue(status, 'camera_1')} />
      <Field label="CAMERA_1_USERNAME" k="CAMERA_1_USERNAME" draft={draft} set={set} placeholder="admin" />
      <Field label="CAMERA_1_PASSWORD" k="CAMERA_1_PASSWORD" draft={draft} set={set} type="password" helper="Stored in .env only; never shown again" />
      <Field label="CAMERA_1_VISCA_PORT" k="CAMERA_1_VISCA_PORT" draft={draft} set={set} placeholder="1240" />
      <Toggle label="VISCA over UDP" k="CAMERA_1_VISCA_UDP" draft={draft} set={set} current={false} />
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <Button
          variant="outlined"
          size="small"
          disabled={!host}
          onClick={() =>
            run(async () => {
              const r = await test('camera', {
                host,
                port: draft.CAMERA_1_VISCA_PORT ? Number(draft.CAMERA_1_VISCA_PORT) : undefined,
                visca_udp: typeof draft.CAMERA_1_VISCA_UDP === 'boolean' ? draft.CAMERA_1_VISCA_UDP : undefined,
              })
              return { ok: r.ok, text: r.detail }
            })
          }
        >
          Test camera
        </Button>
        <ProbeResult probe={probe} />
      </Stack>
    </Stack>
  )
}

const ROLE_CHANNELS: { key: string; label: string }[] = [
  { key: 'MIXER_PASTOR_CHANNEL', label: 'Pastor channel' },
  { key: 'MIXER_LITURGIST_CHANNEL', label: 'Liturgist channel' },
  { key: 'MIXER_VOCALIST_CHANNEL', label: 'Vocalist channel' },
  { key: 'MIXER_CONGREGATION_CHANNEL', label: 'Congregation channel' },
]

function AudioStep({ status, draft, set, test, audioDevices }: StepProps) {
  const [devices, setDevices] = useState<AudioDevice[] | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [probe, run] = useProbe()
  useEffect(() => {
    let cancelled = false
    audioDevices()
      .then((r) => {
        if (cancelled) return
        setDevices(r.devices)
        setNote(r.note)
      })
      .catch(() => {
        if (!cancelled) {
          setDevices([])
          setNote('could not list audio devices')
        }
      })
    return () => {
      cancelled = true
    }
  }, [audioDevices])
  const selected = (draft.MGX_USB_DEVICE_NAME as string) || itemValue(status, 'mixer_usb')
  return (
    <Stack spacing={2}>
      <Toggle label="Per-channel USB capture (MGX_USB_ENABLED)" k="MGX_USB_ENABLED" draft={draft} set={set} current={Boolean(itemValue(status, 'mixer_usb'))} />
      {devices === null ? (
        <CircularProgress size={20} aria-label="loading audio devices" />
      ) : (
        <TextField
          select
          size="small"
          label="USB input device (MGX_USB_DEVICE_NAME)"
          value={selected}
          onChange={(e) => set('MGX_USB_DEVICE_NAME', e.target.value)}
          slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'USB input device' } }}
          helperText={note ?? `${devices.length} input device(s) found`}
        >
          <option value="">{devices.length ? 'Choose a device' : 'No input devices'}</option>
          {devices.map((d) => (
            <option key={d.index} value={d.name}>
              {d.name} ({d.max_input_channels} ch{d.hostapi ? `, ${d.hostapi}` : ''})
            </option>
          ))}
          {selected && !devices.some((d) => d.name === selected) && <option value={selected}>{selected} (configured)</option>}
        </TextField>
      )}
      <Typography variant="overline" color="text.secondary">
        Mixer channel per role
      </Typography>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', sm: 'repeat(4, 1fr)' } }}>
        {ROLE_CHANNELS.map((r) => (
          <Field key={r.key} label={r.label} k={r.key} draft={draft} set={set} type="number" />
        ))}
      </Box>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <Button
          variant="outlined"
          size="small"
          onClick={() =>
            run(async () => {
              const r = await test('mixer', selected ? { device_name: selected } : {})
              return { ok: r.ok, text: r.detail }
            })
          }
        >
          Test mixer
        </Button>
        <ProbeResult probe={probe} />
      </Stack>
    </Stack>
  )
}

function SlidesStep({ status, draft, set, test }: StepProps) {
  const [probe, run] = useProbe()
  return (
    <Stack spacing={2}>
      <Field label="EASYWORSHIP_REMOTE_HOST" k="EASYWORSHIP_REMOTE_HOST" draft={draft} set={set} placeholder={itemValue(status, 'easyworship')} helper="The EasyWorship PC (Remote must be enabled in EasyWorship)" />
      <Field label="SLIDE_VERIFY_DEVICE" k="SLIDE_VERIFY_DEVICE" draft={draft} set={set} placeholder={itemValue(status, 'slides_tap') || 'e.g. 1 or "USB Video"'} helper="Capture device fed by the EasyWorship output tap (camera 2) for OCR slide verification" />
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <Button
          variant="outlined"
          size="small"
          onClick={() =>
            run(async () => {
              const host = (draft.EASYWORSHIP_REMOTE_HOST as string) || undefined
              const r = await test('easyworship', host ? { host } : {})
              return { ok: r.ok, text: r.detail }
            })
          }
        >
          Test EasyWorship
        </Button>
        <ProbeResult probe={probe} />
      </Stack>
    </Stack>
  )
}

function SecurityStep({ status, draft, set }: StepProps) {
  const keys = status?.keys
  return (
    <Stack spacing={2}>
      <Field label="API_HOST" k="API_HOST" draft={draft} set={set} placeholder={status?.api_host ?? '127.0.0.1'} helper="127.0.0.1 serves this PC only; 0.0.0.0 serves the LAN and then requires API keys" />
      <Typography variant="overline" color="text.secondary">
        API keys (set with scripts/setup-windows.ps1; never edited here)
      </Typography>
      <Stack direction="row" spacing={1}>
        <Chip size="small" color={keys?.operator ? 'success' : 'default'} label={keys?.operator ? 'Operator key set' : 'Operator key missing'} />
        <Chip size="small" color={keys?.admin ? 'success' : 'default'} label={keys?.admin ? 'Admin key set' : 'Admin key missing'} />
        {keys && !keys.auth_enabled && <Chip size="small" color="warning" label="Auth disabled" />}
      </Stack>
      {status && !status.api_loopback && !(keys?.operator || keys?.admin) && (
        <Alert severity="error">API_HOST is not loopback and no API keys are set: the backend refuses to serve until keys exist.</Alert>
      )}
    </Stack>
  )
}

function ReviewStep({ draft, status, result, error, saving }: { draft: Draft; status: SetupStatus | null; result: { written: string[] } | null; error: string | null; saving: boolean }) {
  const keys = Object.keys(draft)
  return (
    <Stack spacing={2}>
      {result ? (
        <Alert severity="warning" role="status">
          Saved {result.written.length} setting(s) to {status?.env_file ?? '.env'}. <strong>Restart the backend</strong> to apply them.
        </Alert>
      ) : keys.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Nothing changed yet. Go back and fill in the fields you want to write.
        </Typography>
      ) : (
        <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 1 }}>
          {keys.map((k) => (
            <React.Fragment key={k}>
              <Typography component="dt" variant="body2" sx={{ fontFamily: 'monospace' }}>
                {k}
              </Typography>
              <Typography component="dd" variant="body2" sx={{ m: 0 }}>
                {SECRET_KEYS.has(k) ? '••••••••' : String(draft[k])}
              </Typography>
            </React.Fragment>
          ))}
        </Box>
      )}
      {error && <Alert severity="error">{error}</Alert>}
      {saving && <CircularProgress size={20} aria-label="saving" />}
    </Stack>
  )
}

export function SetupWizard() {
  const { status, needsSetup, discover, test, audioDevices, saveEnv } = useSetup()
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<Draft>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [result, setResult] = useState<{ written: string[] } | null>(null)

  useEffect(() => {
    const listener = () => setOpen(true)
    openListeners.add(listener)
    return () => {
      openListeners.delete(listener)
    }
  }, [])

  useEffect(() => {
    if (needsSetup && !window.sessionStorage?.getItem(DISMISSED_KEY)) setOpen(true)
  }, [needsSetup])

  const close = () => {
    setOpen(false)
    try {
      window.sessionStorage?.setItem(DISMISSED_KEY, '1')
    } catch {
      // Storage unavailable; the wizard simply reopens next load.
    }
  }

  const set = useCallback((key: string, value: string | boolean) => {
    setDraft((prev) => ({ ...prev, [key]: value }))
    setResult(null)
  }, [])

  const save = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const values: EnvValues = {}
      for (const [k, v] of Object.entries(draft)) values[k] = v
      const r = await saveEnv(values)
      setResult(r)
      setDraft({})
    } catch (err) {
      setSaveError(errorDetail(err, 'Could not write .env'))
    } finally {
      setSaving(false)
    }
  }

  const props: StepProps = { status, draft, set, discover, test, audioDevices }
  const last = step === STEPS.length - 1
  const body = [
    <WelcomeStep key="welcome" {...props} />,
    <NetworkStep key="network" {...props} />,
    <CamerasStep key="cameras" {...props} />,
    <AudioStep key="audio" {...props} />,
    <SlidesStep key="slides" {...props} />,
    <SecurityStep key="security" {...props} />,
    <ReviewStep key="review" draft={draft} status={status} result={result} error={saveError} saving={saving} />,
  ][step]

  return (
    <Dialog open={open} onClose={close} maxWidth="md" fullWidth aria-labelledby="setup-wizard-title">
      <DialogTitle id="setup-wizard-title">Setup wizard — {STEPS[step]}</DialogTitle>
      <DialogContent dividers>
        <Stepper activeStep={step} alternativeLabel sx={{ mb: 3 }}>
          {STEPS.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>
        {body}
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>Close</Button>
        <Box sx={{ flexGrow: 1 }} />
        <Button disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          Back
        </Button>
        {last ? (
          <Button variant="contained" disabled={saving || Object.keys(draft).length === 0} onClick={save}>
            Save to .env
          </Button>
        ) : (
          <Button variant="contained" onClick={() => setStep((s) => s + 1)}>
            Next
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

export default SetupWizard
