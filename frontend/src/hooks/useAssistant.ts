/**
 * AI assistant chat hook
 * Keeps the conversation in memory and surfaces any pending high-risk
 * confirmation (streaming/recording/mic) returned by the assistant.
 */

import { useState } from 'react'
import { assistantAPI, ChatMessage, PendingConfirmation } from '@/api/assistant'

export function useAssistant() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState<PendingConfirmation | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || sending) return

    const next = [...messages, { role: 'user' as const, content: trimmed }]
    setMessages(next)
    setSending(true)
    setError(null)
    try {
      const response = await assistantAPI.chat(next)
      setMessages([...next, { role: 'assistant', content: response.data.reply }])
      setPending(response.data.pending_confirmation)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reach the assistant')
    } finally {
      setSending(false)
    }
  }

  const confirmPending = async () => {
    if (!pending) return
    try {
      await assistantAPI.confirm(pending.token)
      setMessages((prev) => [...prev, { role: 'assistant', content: `Confirmed: ${pending.description}` }])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to confirm action')
    } finally {
      setPending(null)
    }
  }

  const cancelPending = async () => {
    if (!pending) return
    try {
      await assistantAPI.cancel(pending.token)
      setMessages((prev) => [...prev, { role: 'assistant', content: `Cancelled: ${pending.description}` }])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to cancel action')
    } finally {
      setPending(null)
    }
  }

  return { messages, pending, sending, error, send, confirmPending, cancelPending }
}
