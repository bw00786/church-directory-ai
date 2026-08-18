/**
 * Production state hook
 * Manages overall production state
 */

import { useState } from 'react'

export interface ProductionState {
  status: 'ready' | 'running' | 'stopped'
  atem_connected: boolean
  streaming: boolean
  recording: boolean
  ai_mode: 'manual' | 'assisted' | 'autonomous'
}

export function useProductionState() {
  const [state, setState] = useState<ProductionState>({
    status: 'ready',
    atem_connected: false,
    streaming: false,
    recording: false,
    ai_mode: 'manual',
  })

  return { state, setState }
}
