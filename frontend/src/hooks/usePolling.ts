import { useEffect, useRef } from 'react'

/** Calls fn immediately and then every intervalMs (no overlapping calls) while enabled. */
export function usePolling(fn: () => Promise<unknown> | void, intervalMs: number, enabled = true, deps: unknown[] = []) {
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        await fnRef.current()
      } catch {
        /* errors are surfaced by fn itself */
      }
      if (!stopped) timer = setTimeout(tick, intervalMs)
    }
    tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, ...deps])
}
