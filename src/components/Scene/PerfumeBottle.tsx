import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { MeshTransmissionMaterial } from '@react-three/drei'
import * as THREE from 'three'
import type { MousePosition } from '../../hooks/useMouseParallax'

interface PerfumeBottleProps {
  /** Pointer position ref (-1..1) for subtle parallax tilt */
  mouse?: React.MutableRefObject<MousePosition>
  /** Disable expensive transmission shading on low-power devices */
  lowPower?: boolean
  /** Disable idle rotation / tilt */
  reducedMotion?: boolean
}

/**
 * A perfume bottle assembled from primitives:
 *   - tapered glass body (lathe for a soft shoulder)
 *   - inner amber liquid
 *   - glass neck + collar
 *   - faceted cap
 * The glass uses MeshTransmissionMaterial for a refractive, chromatic look.
 */
export function PerfumeBottle({
  mouse,
  lowPower = false,
  reducedMotion = false,
}: PerfumeBottleProps) {
  const group = useRef<THREE.Group>(null)
  const body = useRef<THREE.Group>(null)

  // Lathe profile for the bottle body: gives a tapered shoulder instead of a
  // plain cylinder. Points are [radius, height] in local space.
  const bodyPoints = [
    new THREE.Vector2(0.0, -1.05),
    new THREE.Vector2(0.62, -1.05),
    new THREE.Vector2(0.66, -0.7),
    new THREE.Vector2(0.66, 0.45),
    new THREE.Vector2(0.6, 0.72),
    new THREE.Vector2(0.34, 0.92),
    new THREE.Vector2(0.3, 1.0),
    new THREE.Vector2(0.0, 1.0),
  ]

  useFrame((state, delta) => {
    if (!group.current) return

    // Slow continuous Y rotation
    if (!reducedMotion) {
      group.current.rotation.y += delta * 0.18
    }

    // Mouse parallax tilt — eased toward the target each frame
    const targetX = mouse && !reducedMotion ? mouse.current.y * 0.18 : 0
    const targetZ = mouse && !reducedMotion ? -mouse.current.x * 0.14 : 0

    group.current.rotation.x = THREE.MathUtils.lerp(
      group.current.rotation.x,
      targetX,
      0.05,
    )
    group.current.rotation.z = THREE.MathUtils.lerp(
      group.current.rotation.z,
      targetZ,
      0.05,
    )

    // Gentle vertical bob
    if (body.current && !reducedMotion) {
      body.current.position.y = Math.sin(state.clock.elapsedTime * 0.6) * 0.04
    }
  })

  return (
    <group ref={group} position={[0, -0.1, 0]} dispose={null}>
      <group ref={body}>
        {/* Inner amber liquid — sits just inside the glass walls */}
        <mesh position={[0, -0.18, 0]} castShadow>
          <cylinderGeometry args={[0.58, 0.55, 1.42, 64]} />
          <meshStandardMaterial
            color="#8B4513"
            emissive="#3a1d05"
            emissiveIntensity={0.55}
            metalness={0.1}
            roughness={0.25}
            transparent
            opacity={0.92}
          />
        </mesh>

        {/* Glass body — refractive transmission material */}
        <mesh castShadow>
          <latheGeometry args={[bodyPoints, 96]} />
          {lowPower ? (
            <meshPhysicalMaterial
              color="#c8956a"
              transparent
              opacity={0.55}
              roughness={0.08}
              metalness={0}
              transmission={0.9}
              thickness={0.5}
              ior={1.5}
              clearcoat={1}
              clearcoatRoughness={0.1}
            />
          ) : (
            <MeshTransmissionMaterial
              transmission={1}
              roughness={0.05}
              thickness={0.8}
              chromaticAberration={0.08}
              ior={1.5}
              color="#c8956a"
              backside
              backsideThickness={0.3}
              anisotropicBlur={0.1}
              distortion={0.15}
              distortionScale={0.3}
              temporalDistortion={0.05}
              samples={lowPower ? 4 : 10}
              resolution={lowPower ? 256 : 512}
              attenuationColor="#e0b27a"
              attenuationDistance={1.5}
              envMapIntensity={2}
            />
          )}
        </mesh>

        {/* Neck collar */}
        <mesh position={[0, 1.04, 0]} castShadow>
          <cylinderGeometry args={[0.2, 0.26, 0.16, 48]} />
          <meshStandardMaterial
            color="#dab877"
            metalness={0.9}
            roughness={0.22}
            envMapIntensity={1.2}
          />
        </mesh>

        {/* Neck glass */}
        <mesh position={[0, 1.2, 0]} castShadow>
          <cylinderGeometry args={[0.17, 0.2, 0.22, 48]} />
          <meshPhysicalMaterial
            color="#c8956a"
            transparent
            opacity={0.6}
            roughness={0.1}
            transmission={0.85}
            thickness={0.3}
            ior={1.5}
          />
        </mesh>
      </group>

      {/* Cap — a faceted golden stopper */}
      <group position={[0, 1.46, 0]}>
        <mesh castShadow>
          <cylinderGeometry args={[0.24, 0.22, 0.34, 6]} />
          <meshStandardMaterial
            color="#1a1410"
            metalness={0.85}
            roughness={0.3}
            envMapIntensity={1}
          />
        </mesh>
        {/* Gold inlay ring on the cap */}
        <mesh position={[0, 0.18, 0]} castShadow>
          <cylinderGeometry args={[0.235, 0.235, 0.05, 6]} />
          <meshStandardMaterial
            color="#dab877"
            metalness={0.95}
            roughness={0.18}
            envMapIntensity={1.4}
          />
        </mesh>
      </group>

      {/* Soft contact shadow disc under the bottle */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, -1.06, 0]}
        receiveShadow
      >
        <circleGeometry args={[1.1, 48]} />
        <meshBasicMaterial color="#000000" transparent opacity={0.28} />
      </mesh>
    </group>
  )
}
