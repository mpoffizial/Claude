import { useEffect, useState } from 'react'

/**
 * Detects "mobile" viewports via width breakpoint (and coarse pointer as a
 * secondary signal). Updates on resize / orientation change. Used to swap the
 * heavy 3D canvas for a lighter scene on phones.
 */
export function useIsMobile(breakpoint = 768) {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < breakpoint
  })

  useEffect(() => {
    if (typeof window === 'undefined') return

    const check = () => {
      const narrow = window.innerWidth < breakpoint
      const coarse = window.matchMedia?.('(pointer: coarse)').matches ?? false
      setIsMobile(narrow || (coarse && window.innerWidth < 1024))
    }

    check()
    window.addEventListener('resize', check)
    window.addEventListener('orientationchange', check)
    return () => {
      window.removeEventListener('resize', check)
      window.removeEventListener('orientationchange', check)
    }
  }, [breakpoint])

  return isMobile
}
