import React, { useCallback, useState } from 'react'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import CardHeader from '@mui/material/CardHeader'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import MonitorHeartIcon from '@mui/icons-material/MonitorHeart'

import { apiFetch } from '@/api/client'
import { COMPONENT_LABELS, type ComponentStatus, useHealth } from '@/hooks/useHealth'

type ChipColor = 'success' | 'warning' | 'error' | 'default' | 'info'

const COLOR: Record<ComponentStatus | 'unknown', ChipColor> = {
  ok: 'success',
  mock: 'info',
  off: 'default',
  degraded: 'warning',
  down: 'error',
  unknown: 'default',
}

interface OllamaResponse {
  ok?: boolean
  model?: string
  base_url?: string
  error?: string
}

/**
 * Pre-service checklist: one row per backend component from GET /health/ready
 * (database, ATEM, cameras, EasyWorship, mixer, audio, Ollama, AI Director,
 * slide verification, voice) plus an on-demand Ollama inference test.
 */
export function SystemStatus() {
  const { ready, backendDown } = useHealth()
  const [ollama, setOllama] = useState<{ level: ChipColor; text: string }>({
    level: 'default',
    text: 'not tested',
  })
  const [testing, setTesting] = useState(false)

  // On-demand only: runs a real (short) inference on the configured Ollama.
  const testOllama = useCallback(async () => {
    setTesting(true)
    try {
      const res = await apiFetch('/health/ollama')
      const body = (await res.json()) as OllamaResponse
      const good = res.ok && body.ok !== false && !body.error
      const where = body.base_url ? ` @ ${body.base_url}` : ''
      setOllama({
        level: good ? 'success' : 'error',
        text: good ? `${body.model ?? 'model'} responding${where}` : `${body.error ?? 'failed'}${where}`,
      })
    } catch {
      setOllama({ level: 'error', text: 'unreachable' })
    } finally {
      setTesting(false)
    }
  }, [])

  const components = ready?.components ?? {}
  const order = Object.keys(COMPONENT_LABELS).filter((k) => k in components)
  const extra = Object.keys(components).filter((k) => !(k in COMPONENT_LABELS))

  return (
    <Card>
      <CardHeader
        avatar={<MonitorHeartIcon color="primary" />}
        title="System Status"
        subheader={backendDown ? 'Backend offline' : ready ? `${ready.status}${ready.version ? ` · v${ready.version}` : ''}` : 'checking…'}
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        <Stack spacing={1}>
          <Row label="Backend API" level={backendDown ? 'error' : ready ? 'success' : 'default'} text={backendDown ? 'offline' : ready ? 'online' : 'checking'} />
          {[...order, ...extra].map((key) => {
            const c = components[key]
            return <Row key={key} label={COMPONENT_LABELS[key] ?? key} level={COLOR[c.status] ?? 'default'} text={c.status} tooltip={c.detail} />
          })}
          <Row
            label="Ollama inference"
            level={ollama.level}
            text={ollama.text}
            action={
              <Button size="small" onClick={testOllama} disabled={testing || backendDown}>
                {testing ? 'Testing...' : 'Test'}
              </Button>
            }
          />
        </Stack>
      </CardContent>
    </Card>
  )
}

function Row({ label, level, text, tooltip, action }: { label: string; level: ChipColor; text: string; tooltip?: string; action?: React.ReactNode }) {
  const chip = <Chip size="small" variant="outlined" color={level} label={text} />
  return (
    <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
      <Typography variant="body2">{label}</Typography>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        {action}
        {tooltip ? <Tooltip title={tooltip}>{chip}</Tooltip> : chip}
      </Stack>
    </Stack>
  )
}
