/**
 * Post-service review: pick a date, see the day's totals and a compact timeline
 * of decisions, action outcomes, operator cuts, mode changes and degradations.
 */

import React from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import CardHeader from '@mui/material/CardHeader'
import Chip from '@mui/material/Chip'
import CircularProgress from '@mui/material/CircularProgress'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import AssessmentIcon from '@mui/icons-material/Assessment'

import type { ReviewTotals, TimelineEntry } from '@/api/review'
import { useReview } from '@/hooks/useReview'

function TotalCard({ title, value, detail }: { title: string; value: React.ReactNode; detail?: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Typography variant="overline" color="text.secondary">
        {title}
      </Typography>
      <Typography variant="h6" sx={{ lineHeight: 1.2 }}>
        {value}
      </Typography>
      {detail && (
        <Typography variant="caption" color="text.secondary">
          {detail}
        </Typography>
      )}
    </Paper>
  )
}

export function TotalsGrid({ totals }: { totals: ReviewTotals }) {
  const p = totals.proposals
  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' } }}>
      <TotalCard title="Decision cycles" value={totals.cycles} detail={`${totals.stale_cycles} stale · ${totals.model_errors} model errors`} />
      <TotalCard title="Executed" value={p.executed} detail={`${p.approved} after approval · ${p.pending} still pending`} />
      <TotalCard title="Rejected / expired" value={`${p.rejected} / ${p.expired}`} detail={`${p.rejected_by_operator} by operator · ${p.held} held · ${p.dropped} dropped`} />
      <TotalCard title="Program changes" value={totals.program_changes.total} detail={`${totals.program_changes.app} app · ${totals.program_changes.operator} operator`} />
      <TotalCard title="Latency" value={totals.latency_ms.avg !== null ? `${totals.latency_ms.avg} ms` : '—'} detail={`p95 ${totals.latency_ms.p95 ?? '—'} · max ${totals.latency_ms.max ?? '—'}`} />
      <TotalCard title="Degraded events" value={totals.degraded_events.total} detail={Object.entries(totals.degraded_events.by_component).map(([k, v]) => `${k} ${v}`).join(' · ') || 'none'} />
      <TotalCard title="Pauses / mode changes" value={`${totals.pauses} / ${totals.mode_changes.length}`} detail={Object.keys(totals.modes_seen).join(', ') || 'no cycles'} />
      <TotalCard title="Phase changes" value={totals.phase_changes_accepted} detail={totals.rehearsals ? `${totals.rehearsals} rehearsal(s) excluded` : 'no rehearsals'} />
    </Box>
  )
}

function timeOf(ts: string | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

const STATUS_COLOR: Record<string, 'success' | 'warning' | 'error' | 'default' | 'info'> = {
  executed: 'success',
  executed_after_approval: 'success',
  pending_approval: 'info',
  held: 'warning',
  rejected: 'error',
  rejected_by_operator: 'error',
  dropped: 'default',
  stale: 'warning',
  error: 'error',
  ai_director_paused: 'warning',
  perception_degraded: 'error',
}

export function TimelineRow({ entry }: { entry: TimelineEntry }) {
  return (
    <TableRow>
      <TableCell sx={{ whiteSpace: 'nowrap' }}>{timeOf(entry.ts)}</TableCell>
      <TableCell>{entry.kind}</TableCell>
      <TableCell>
        {entry.summary}
        {entry.detail && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {entry.detail}
          </Typography>
        )}
      </TableCell>
      <TableCell>{entry.status && <Chip size="small" label={entry.status} color={STATUS_COLOR[entry.status] ?? 'default'} />}</TableCell>
    </TableRow>
  )
}

export function ServiceReview() {
  const { dates, date, setDate, review, loading, error } = useReview()
  const options = dates.includes(date) ? dates : [date, ...dates]
  const timeline = review?.timeline
  const hidden = timeline ? timeline.total - timeline.head.length - timeline.tail.length : 0

  return (
    <Card>
      <CardHeader
        avatar={<AssessmentIcon color="secondary" />}
        title="Service review"
        subheader="What the AI Director did, and what the operator did instead"
        titleTypographyProps={{ variant: 'subtitle2' }}
        action={
          <TextField
            select
            size="small"
            label="Date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            slotProps={{ select: { native: true }, htmlInput: { 'aria-label': 'Review date' } }}
            sx={{ mt: 1, mr: 1, minWidth: 160 }}
          >
            {options.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </TextField>
        }
      />
      <CardContent>
        {loading && !review && <CircularProgress size={24} aria-label="loading review" />}
        {error && <Alert severity="error">{error}</Alert>}
        {review && !review.has_data && <Alert severity="info">No AI Director activity recorded on {review.date}.</Alert>}
        {review && review.has_data && (
          <Stack spacing={2}>
            <TotalsGrid totals={review.totals} />
            {review.totals.top_held_reasons.length > 0 && (
              <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
                <Typography variant="overline" color="text.secondary">
                  Top hold reasons
                </Typography>
                {review.totals.top_held_reasons.map((r) => (
                  <Chip key={r.reason} size="small" color="warning" variant="outlined" label={`${r.reason} ×${r.count}`} />
                ))}
              </Stack>
            )}
            {review.operator_advances && (
              <Typography variant="body2" color="text.secondary">
                Operator slide advances learned: {review.operator_advances.slides} slide(s) on record.
              </Typography>
            )}
            {timeline && timeline.total > 0 && (
              <Table size="small" aria-label="Review timeline">
                <TableHead>
                  <TableRow>
                    <TableCell>Time</TableCell>
                    <TableCell>Kind</TableCell>
                    <TableCell>Summary</TableCell>
                    <TableCell>Status</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {timeline.head.map((entry, i) => (
                    <TimelineRow key={`h${i}`} entry={entry} />
                  ))}
                  {hidden > 0 && (
                    <TableRow>
                      <TableCell colSpan={4} align="center">
                        <Typography variant="caption" color="text.secondary">
                          … {hidden} more entries …
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                  {timeline.tail.map((entry, i) => (
                    <TimelineRow key={`t${i}`} entry={entry} />
                  ))}
                </TableBody>
              </Table>
            )}
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}

export default ServiceReview
