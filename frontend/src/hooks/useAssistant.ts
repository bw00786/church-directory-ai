/**
 * AI assistant chat hook
 * Keeps the conversation in memory and surfaces any pending high-risk
 * confirmation (streaming/recording/mic), including approvals outside chat.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { assistantAPI, ChatMessage, PendingConfirmation } from '@/api/assistant'

export function useAssistant() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pendingActions, setPendingActions] = useState<PendingConfirmation[]>([])
  const [sending, setSending] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pendingError, setPendingError] = useState<string | null>(null)
  const mounted = useRef(false)
  const pendingRequest = useRef(0)
  const resolvingRef = useRef(false)
  const resolvedTokens = useRef(new Set<string>())
  const pending = pendingActions[0] ?? null

  const refreshPending = useCallback(async () => {
    const request = ++pendingRequest.current
    try {
      const response = await assistantAPI.pending()
      if (!mounted.current || request !== pendingRequest.current) return
      setPendingActions(response.data.filter((item) => !resolvedTokens.current.has(item.token)))
      setPendingError(null)
    } catch (e) {
      if (!mounted.current || request !== pendingRequest.current) return
      setPendingError(e instanceof Error ? e.message : 'Failed to load pending approvals')
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void refreshPending()
    const interval = setInterval(() => {
      if (!resolvingRef.current) void refreshPending()
    }, 5000)
    return () => {
      mounted.current = false
      ++pendingRequest.current
      clearInterval(interval)
    }
  }, [refreshPending])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || sending) return

    const next = [...messages, { role: 'user' as const, content: trimmed }]
    setMessages(next)
    setSending(true)
    setError(null)
    try {
      const response = await assistantAPI.chat(next)
      if (!mounted.current) return
      setMessages((prev) => [...prev, { role: 'assistant', content: response.data.reply }])
      const proposal = response.data.pending_confirmation
      if (proposal && !resolvedTokens.current.has(proposal.token)) {
        ++pendingRequest.current
        setPendingActions((prev) => prev.some((item) => item.token === proposal.token) ? prev : [...prev, proposal])
      }
      if (!resolvingRef.current) await refreshPending()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reach the assistant')
    } finally {
      setSending(false)
    }
  }

  const resolvePending = async (confirm: boolean) => {
    if (!pending || resolvingRef.current || resolvedTokens.current.has(pending.token)) return
    resolvingRef.current = true
    setResolving(true)
    setError(null)
    // Invalidate snapshots taken before this token is consumed.
    ++pendingRequest.current
    try {
      const response = await (confirm ? assistantAPI.confirm(pending.token) : assistantAPI.cancel(pending.token))
      // Confirm consumes the token even when the executor reports failure.
      if (confirm || response.data.ok) {
        resolvedTokens.current.add(pending.token)
        if (mounted.current) setPendingActions((prev) => prev.filter((item) => item.token !== pending.token))
      }
      if (response.data.ok !== true) {
        throw new Error(response.data.error || (confirm ? 'Failed to confirm action' : 'Failed to cancel action'))
      }
      if (mounted.current) {
        setMessages((prev) => [...prev, {
          role: 'assistant', content: `${confirm ? 'Confirmed' : 'Cancelled'}: ${pending.description}`,
        }])
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : 'Failed to resolve action')
    } finally {
      if (mounted.current) await refreshPending()
      resolvingRef.current = false
      if (mounted.current) setResolving(false)
    }
  }

  const confirmPending = () => resolvePending(true)
  const cancelPending = () => resolvePending(false)

  return { messages, pending, sending, resolving, error: error ?? pendingError, send, confirmPending, cancelPending }
}
