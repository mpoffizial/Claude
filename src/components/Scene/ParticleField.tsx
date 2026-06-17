import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'

interface ParticleFieldProps {
  count?: number
  reducedMotion?: boolean
}

/**
 * Drifting "scent" particles — points scattered in a sphere around the camera
 * that rise slowly and sway on a sine wave. Very subtle, champagne-toned.
 */
export function ParticleField({
  count = 280,
  reducedMotion = false,
}: ParticleFieldProps) {
  const points = useRef<THREE.Points>(null)

  // Initial positions + per-particle phase/speed for organic drift
  const { positions, seeds } = useMemo(() => {
    const positions = new Float32Array(count * 3)
    const seeds = new Float32Array(count * 2)
    const radius = 7

    for (let i = 0; i < count; i++) {
      // Spread within a sphere, biased outward slightly
      const r = radius * Math.cbrt(Math.random())
      const theta = Math.random() * Math.PI * 2
      const phi = Math.acos(2 * Math.random() - 1)

      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta)
      positions[i * 3 + 1] = (Math.random() - 0.5) * radius * 1.6
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta)

      seeds[i * 2] = Math.random() * Math.PI * 2 // phase
      seeds[i * 2 + 1] = 0.4 + Math.random() * 0.8 // speed
    }

    return { positions, seeds }
  }, [count])

  // Circular soft sprite so points read as motes of light, not squares
  const sprite = useMemo(() => {
    const size = 64
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')!
    const grad = ctx.createRadialGradient(
      size / 2,
      size / 2,
      0,
      size / 2,
      size / 2,
      size / 2,
    )
    grad.addColorStop(0, 'rgba(218,184,119,1)')
    grad.addColorStop(0.35, 'rgba(218,184,119,0.55)')
    grad.addColorStop(1, 'rgba(218,184,119,0)')
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, size, size)
    const tex = new THREE.CanvasTexture(canvas)
    tex.needsUpdate = true
    return tex
  }, [])

  useFrame((state) => {
    if (!points.current || reducedMotion) return

    const time = state.clock.elapsedTime
    const arr = points.current.geometry.attributes.position
      .array as Float32Array

    for (let i = 0; i < count; i++) {
      const phase = seeds[i * 2]
      const speed = seeds[i * 2 + 1]

      // Rise upward
      arr[i * 3 + 1] += speed * 0.0025

      // Wrap around when above the field
      if (arr[i * 3 + 1] > 6) arr[i * 3 + 1] = -6

      // Sine sway on X / Z
      arr[i * 3] += Math.sin(time * 0.2 * speed + phase) * 0.0009
      arr[i * 3 + 2] += Math.cos(time * 0.18 * speed + phase) * 0.0009
    }

    points.current.geometry.attributes.position.needsUpdate = true
    points.current.rotation.y = time * 0.01
  })

  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={count}
          array={positions}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        map={sprite}
        size={0.09}
        sizeAttenuation
        color="#dab877"
        transparent
        opacity={0.28}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  )
}
