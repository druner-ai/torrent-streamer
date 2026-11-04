import { useEffect, useRef } from 'react'

export function usePolling(callback, intervalMs) {
  const saved = useRef(callback)

  useEffect(() => {
    saved.current = callback
  }, [callback])

  useEffect(() => {
    let cancelled = false

    async function run() {
      try {
        const result = await saved.current()
        if (cancelled) return result
      } catch {
        // background polling must never throw into the app
      }
      return undefined
    }

    run()
    const id = setInterval(run, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [intervalMs])
}
