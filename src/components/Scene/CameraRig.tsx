import { useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import type { MousePosition } from '../../hooks/useMouseParallax'

interface CameraRigProps {
  mouse?: React.MutableRefObject<MousePosition>
  reducedMotion?: boolean
  /**
   * Live page-scroll progress ref (0..1). The canvas is fixed behind a normally
   * scrolling document, so we read the real scroll position rather than drei's
   * ScrollControls (which would hijack scrolling).
   */
  scrollProgress?: React.MutableRefObject<number>
}

/**
 * Drives the camera:
 *  - Initial zoom-in from far away on load.
 *  - Slow orbit around the bottle as the user scrolls.
 *  - Subtle mouse parallax offset.
 */
export function CameraRig({
  mouse,
  reducedMotion = false,
  scrollProgress,
}: CameraRigProps) {
  const { camera } = useThree()
  const intro = useRef(0)
  const target = useRef(new THREE.Vector3(0, 0, 0))

  useFrame((_, delta) => {
    // Ease intro progress 0 -> 1
    if (intro.current < 1) {
      intro.current = Math.min(
        1,
        intro.current + delta * (reducedMotion ? 5 : 0.6),
      )
    }
    const introEase = easeOutCubic(intro.current)

    const offset = scrollProgress ? scrollProgress.current : 0

    // Orbit angle: a little over a half-revolution across the full page
    const angle = offset * Math.PI * 1.2
    const baseRadius = 5.6 - offset * 0.7

    // Intro starts far out and settles in
    const radius = THREE.MathUtils.lerp(12, baseRadius, introEase)
    const height = 0.3 + offset * 1.5

    const mx = mouse && !reducedMotion ? mouse.current.x : 0
    const my = mouse && !reducedMotion ? mouse.current.y : 0

    const targetX = Math.sin(angle) * radius + mx * 0.5
    const targetZ = Math.cos(angle) * radius
    const targetY = height + my * 0.35

    const ease = reducedMotion ? 1 : 0.06
    camera.position.x = THREE.MathUtils.lerp(camera.position.x, targetX, ease)
    camera.position.y = THREE.MathUtils.lerp(camera.position.y, targetY, ease)
    camera.position.z = THREE.MathUtils.lerp(camera.position.z, targetZ, ease)

    target.current.set(0, 0.1 + offset * 0.35, 0)
    camera.lookAt(target.current)
  })

  return null
}

function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3)
}
