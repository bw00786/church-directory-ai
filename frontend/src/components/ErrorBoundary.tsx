import React from 'react'
import Alert from '@mui/material/Alert'
import AlertTitle from '@mui/material/AlertTitle'
import Button from '@mui/material/Button'
import Box from '@mui/material/Box'

interface Props {
  children: React.ReactNode
}

interface State {
  error: Error | null
}

/**
 * Last line of defence for the operator UI: a render error in one panel must
 * never blank the whole console mid-service. Shows the error and a reload
 * button; manual hardware control stays available through the other panels
 * once reloaded.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Operator UI crashed', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <Box sx={{ p: 3 }}>
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => window.location.reload()}>
              Reload
            </Button>
          }
        >
          <AlertTitle>The operator UI hit an error</AlertTitle>
          {this.state.error.message}
          <Box component="pre" sx={{ mt: 1, fontSize: 12, whiteSpace: 'pre-wrap' }}>
            {this.state.error.stack}
          </Box>
        </Alert>
      </Box>
    )
  }
}
