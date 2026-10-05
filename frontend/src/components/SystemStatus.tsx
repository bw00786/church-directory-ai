import React, { useCallback, useEffect, useState } from 'react'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import CardHeader from '@mui/material/CardHeader'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import MonitorHeartIcon from '@mui/icons-material/MonitorHeart'

import { useAtem } from '@/hooks/useAtem'

type Level = 'ok' | 'warn' | 'error' | 'unknown'

const COLOR: Record<Level, 'success' | 'warning' | 'error' | 'default'> = {
  ok: 'success',
  warn: 'warning',
  error: 'error',
  unknown: 'default',
}

interface ReadyResponse {
  status: string
  checks: Record<string, string>
}

interface ClaudeResponse {
  status?: string
  ok?: boolean
  error?: string
}

export function SystemStatus() {
  const { state: atem, error: atemError } = useAtem()
  const [ready, setReady] = useState<ReadyResponse | null>(null)
  const [backendDown, setBackendDown] = useState(false)
  const [claude, setClaude] = useState<{ level: Level; text: string }>({
    level: 'unknown',
    text: 'not tested',
  })
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetch('/api/health/ready')
        if (!res.ok) throw new Error(String(res.status))
        const body = (await res.json()) as ReadyResponse
        if (!cancelled) {
          setReady(body)
          setBackendDown(false)
        }
      } catch {
        if (!cancelled) setBackendDown(true)
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  // On-demand only: this call bills against the Anthropic account.
  const testClaude = useCallback(async () => {
    setTesting(true)
    try {
      const res = await fetch('/api/health/anthropic')
      const body = (await res.json()) as ClaudeResponse
      const good = res.ok && body.ok !== false && !body.error
      setClaude({ level: good ? 'ok' : 'error', text: good ? 'responding' : body.error ?? 'failed' })
    } catch {
      setClaude({ level: 'error', text: 'unreachable' })
    } finally {
      setTesting(false)
    }
  }, [])

  const backend: { level: Level; text: string } = backendDown
    ? { level: 'error', text: 'offline' }
    : ready
      ? { level: 'ok', text: ready.status }
      : { level: 'unknown', text: 'checking' }

  const atemRow: { level: Level; text: string } = atemError
    ? { level: 'error', text: 'unreachable' }
    : atem?.connected
      ? { level: 'ok', text: 'connected' }
      : { level: 'warn', text: 'not connected' }

  const configured = ready?.checks?.anthropic === 'configured'

  const rows: Array<{ label: string; level: Level; text: string; action?: React.ReactNode }> = [
    { label: 'Backend API', ...backend },
    { label: 'ATEM', ...atemRow },
    {
      label: 'Claude API key',
      level: ready ? (configured ? 'ok' : 'warn') : 'unknown',
      text: ready ? (configured ? 'configured' : 'not configured') : 'checking',
    },
    {
      label: 'Claude inference',
      ...claude,
      action: (
        <Button size="small" onClick={testClaude} disabled={testing || backendDown}>
          {testing ? 'Testing...' : 'Test'}
        </Button>
      ),
    },
  ]

  return (
    <Card>
      <CardHeader
        avatar={<MonitorHeartIcon color="primary" />}
        title="System Status"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <CardContent>
        <Stack spacing={1}>
          {rows.map((row) => (
            <Stack key={row.label} direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
              <Typography variant="body2">{row.label}</Typography>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                {row.action}
                <Chip size="small" variant="outlined" color={COLOR[row.level]} label={row.text} />
              </Stack>
            </Stack>
          ))}
        </Stack>
      </CardContent>
    </Card>
  )
}
