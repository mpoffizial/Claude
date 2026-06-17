import { useEffect, useState } from 'react'

/**
 * Returns the page scroll progress as a value between 0 (top) and 1 (bottom).
 * Throttled to animation frames for smoothness.
 */
export function useScrollProgress() {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    if (typeof window === 'undefined') return

    let frame = 0

    const update = () => {
      frame = 0
      const scrollTop = window.scrollY || document.documentElement.scrollTop
      const max =
        document.documentElement.scrollHeight - window.innerHeight || 1
      setProgress(Math.min(1, Math.max(0, scrollTop / max)))
    }

    const onScroll = () => {
      if (frame) return
      frame = window.requestAnimationFrame(update)
    }

    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)

    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [])

  return progress
}
