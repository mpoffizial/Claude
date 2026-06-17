import { useEffect, useState } from 'react'

/**
 * Tracks the user's `prefers-reduced-motion` setting and keeps it in sync
 * if the OS-level preference changes during the session.
 */
export function useReducedMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return

    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    setPrefersReducedMotion(query.matches)

    const onChange = (event: MediaQueryListEvent) => {
      setPrefersReducedMotion(event.matches)
    }

    // Safari < 14 only supports addListener
    if (query.addEventListener) {
      query.addEventListener('change', onChange)
      return () => query.removeEventListener('change', onChange)
    } else {
      query.addListener(onChange)
      return () => query.removeListener(onChange)
    }
  }, [])

  return { prefersReducedMotion }
}
