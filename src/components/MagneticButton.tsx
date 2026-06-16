import { useEffect, useRef } from 'react'
import { useReducedMotion } from '../hooks/useReducedMotion'
import './MagneticButton.css'

interface MagneticButtonProps {
  children: React.ReactNode
  type?: 'button' | 'submit'
  className?: string
  /** Maximum pixel displacement toward the cursor */
  strength?: number
  onClick?: () => void
}

/**
 * A button that magnetically drifts toward the cursor while hovered, with a
 * lerped return on leave. Respects reduced-motion.
 */
export function MagneticButton({
  children,
  type = 'button',
  className = '',
  strength = 22,
  onClick,
}: MagneticButtonProps) {
  const ref = useRef<HTMLButtonElement>(null)
  const inner = useRef<HTMLSpanElement>(null)
  const { prefersReducedMotion } = useReducedMotion()

  useEffect(() => {
    const el = ref.current
    const innerEl = inner.current
    if (!el || !innerEl || prefersReducedMotion) return

    let raf = 0
    const current = { x: 0, y: 0 }
    const target = { x: 0, y: 0 }

    const animate = () => {
      current.x += (target.x - current.x) * 0.18
      current.y += (target.y - current.y) * 0.18
      el.style.transform = `translate(${current.x.toFixed(2)}px, ${current.y.toFixed(2)}px)`
      // Inner label trails a touch further for depth
      innerEl.style.transform = `translate(${(current.x * 0.35).toFixed(2)}px, ${(current.y * 0.35).toFixed(2)}px)`

      if (
        Math.abs(target.x - current.x) > 0.05 ||
        Math.abs(target.y - current.y) > 0.05
      ) {
        raf = requestAnimationFrame(animate)
      } else {
        raf = 0
      }
    }

    const start = () => {
      if (!raf) raf = requestAnimationFrame(animate)
    }

    const onMove = (e: PointerEvent) => {
      const rect = el.getBoundingClientRect()
      const relX = e.clientX - (rect.left + rect.width / 2)
      const relY = e.clientY - (rect.top + rect.height / 2)
      // Normalise by half-size, clamp to [-1,1], scale by strength
      target.x = Math.max(-1, Math.min(1, relX / (rect.width / 2))) * strength
      target.y = Math.max(-1, Math.min(1, relY / (rect.height / 2))) * strength
      start()
    }

    const onLeave = () => {
      target.x = 0
      target.y = 0
      start()
    }

    el.addEventListener('pointermove', onMove)
    el.addEventListener('pointerleave', onLeave)

    return () => {
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerleave', onLeave)
      if (raf) cancelAnimationFrame(raf)
      el.style.transform = ''
      innerEl.style.transform = ''
    }
  }, [prefersReducedMotion, strength])

  return (
    <button
      ref={ref}
      type={type}
      onClick={onClick}
      className={`magnetic ${className}`}
    >
      <span ref={inner} className="magnetic__label">
        {children}
      </span>
    </button>
  )
}
