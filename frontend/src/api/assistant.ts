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

export interface ChatResponse {
  reply: string
  pending_confirmation: PendingConfirmation | null
}

export const assistantAPI = {
  async chat(messages: ChatMessage[]) {
    return client.post<ChatResponse>('/api/assistant/chat', { messages })
  },

  async confirm(token: string) {
    return client.post(`/api/assistant/confirm/${token}`)
  },

  async cancel(token: string) {
    return client.post(`/api/assistant/cancel/${token}`)
  },
}
