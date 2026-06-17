import { useEffect, useRef } from 'react'

export interface MousePosition {
  /** Normalised -1 .. 1 on the X axis (left -> right) */
  x: number
  /** Normalised -1 .. 1 on the Y axis (top -> bottom, inverted for 3D) */
  y: number
}

/**
 * Tracks the pointer position normalised to the -1..1 range, stored in a ref so
 * consumers (e.g. R3F `useFrame` loops) can read it every frame without
 * triggering React re-renders.
 *
 * Returns a ref — read `ref.current.x` / `ref.current.y`.
 */
export function useMouseParallax(enabled = true) {
  const mouse = useRef<MousePosition>({ x: 0, y: 0 })

  useEffect(() => {
    if (!enabled || typeof window === 'undefined') return

    const handleMove = (event: PointerEvent) => {
      mouse.current.x = (event.clientX / window.innerWidth) * 2 - 1
      // Invert Y so that "up" is positive — matches 3D / camera conventions
      mouse.current.y = -((event.clientY / window.innerHeight) * 2 - 1)
    }

    const handleLeave = () => {
      mouse.current.x = 0
      mouse.current.y = 0
    }

    window.addEventListener('pointermove', handleMove, { passive: true })
    window.addEventListener('pointerleave', handleLeave)

    return () => {
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('pointerleave', handleLeave)
    }
  }, [enabled])

  return mouse
}
