/**
 * AI assistant chat API client
 */

import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const client = axios.create({
  baseURL: API_BASE,
})

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface PendingConfirmation {
  token: string
  description: string
}

export interface PendingAction extends PendingConfirmation {
  action: string
}

interface ActionResponse {
  ok: boolean
  error?: string
}

export interface ChatResponse {
  reply: string
  pending_confirmation: PendingConfirmation | null
}

export const assistantAPI = {
  async pending() {
    return client.get<PendingAction[]>('/api/assistant/pending')
  },

  async chat(messages: ChatMessage[]) {
    return client.post<ChatResponse>('/api/assistant/chat', { messages })
  },

  async confirm(token: string) {
    return client.post<ActionResponse>(`/api/assistant/confirm/${token}`)
  },

  async cancel(token: string) {
    return client.post<ActionResponse>(`/api/assistant/cancel/${token}`)
  },
}
