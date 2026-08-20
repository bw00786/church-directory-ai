import React from 'react'
import Card from '@mui/material/Card'
import CardHeader from '@mui/material/CardHeader'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Chip from '@mui/material/Chip'
import TimelineIcon from '@mui/icons-material/Timeline'

export function EventTimeline() {
  const events = [
    { time: '10:31:22', type: 'LIKELY_SPEAKER', confidence: '91%' },
    { time: '10:31:28', type: 'GOOD_COMPOSITION', confidence: '94%' },
    { time: '10:32:14', type: 'CAMERA_QUALITY_CHANGE', confidence: '87%' },
    { time: '10:35:01', type: 'CONGREGATION_ACTIVE', confidence: '90%' },
  ]

  return (
    <Card>
      <CardHeader
        avatar={<TimelineIcon color="primary" />}
        title="Event Timeline"
        titleTypographyProps={{ variant: 'subtitle2' }}
      />
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Time</TableCell>
            <TableCell>Event</TableCell>
            <TableCell align="right">Confidence</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {events.map((event, index) => (
            <TableRow key={index}>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{event.time}</TableCell>
              <TableCell>{event.type}</TableCell>
              <TableCell align="right">
                <Chip label={event.confidence} size="small" variant="outlined" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}
