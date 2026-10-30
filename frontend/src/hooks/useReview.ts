/**
 * Post-service review: available dates plus one day's aggregated report.
 */

import { useCallback, useEffect, useState } from 'react'

import { reviewAPI, type ServiceReviewData } from '@/api/review'

export function todayIso(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

export function useReview(initialDate?: string) {
  const [dates, setDates] = useState<string[]>([])
  const [date, setDate] = useState<string>(initialDate ?? todayIso())
  const [review, setReview] = useState<ServiceReviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    reviewAPI
      .dates()
      .then(({ data }) => {
        if (cancelled) return
        setDates(data.dates)
        // Default to the most recent day with data when today has none.
        if (!initialDate && data.dates.length > 0 && !data.dates.includes(todayIso())) setDate(data.dates[0])
      })
      .catch(() => {
        if (!cancelled) setDates([])
      })
    return () => {
      cancelled = true
    }
  }, [initialDate])

  const load = useCallback(async (day: string) => {
    setLoading(true)
    try {
      const { data } = await reviewAPI.get(day)
      setReview(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Review unavailable')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(date)
  }, [date, load])

  return { dates, date, setDate, review, loading, error, reload: () => load(date) }
}
