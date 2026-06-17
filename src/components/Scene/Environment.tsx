// DreiEnvironment removed — the preset fetches from an external CDN that may
// be unreachable, crashing the Canvas. We rely on scene lights instead.

interface SceneLightingProps {
  /** Skip the HDRI environment map on low-power devices */
  lowPower?: boolean
}

/**
 * Lighting + HDRI setup for the bottle:
 *  - warm key light from the upper-right
 *  - champagne rim light from the left
 *  - sharp specular spot from above
 *  - studio environment for glass reflections
 */
export function SceneLighting({ lowPower = false }: SceneLightingProps) {
  return (
    <>
      <ambientLight intensity={0.5} />

      {/* Warm key light, upper-right */}
      <directionalLight
        position={[4, 6, 4]}
        intensity={2.0}
        color="#fff8e7"
        castShadow={!lowPower}
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-bias={-0.0004}
      />

      {/* Champagne rim light from the left */}
      <pointLight position={[-5, 1.5, 2]} intensity={1.5} color="#dab877" />

      {/* Sharp specular spotlight from directly above */}
      <spotLight
        position={[0, 7, 0.5]}
        intensity={3}
        angle={0.32}
        penumbra={0.6}
        color="#ffffff"
        castShadow={!lowPower}
      />

      {/* Warm fill from behind — gives the glass a visible edge to refract */}
      <pointLight position={[0, 0, -4]} intensity={1.2} color="#c8956a" />

      {/* Cool fill from the side */}
      <pointLight position={[0, -1, -5]} intensity={0.6} color="#8b7d8a" />

      {/* Glowing backplane — a large, softly emissive mesh the glass refracts.
          Without this the bottle disappears into the dark background. */}
      <mesh position={[0, 0, -3]} renderOrder={-1}>
        <planeGeometry args={[12, 10]} />
        <meshStandardMaterial
          color="#3a1a08"
          emissive="#c8600a"
          emissiveIntensity={0.18}
          roughness={1}
          metalness={0}
        />
      </mesh>

      {/* Soft warm halo behind the bottle neck */}
      <mesh position={[0, 0.8, -2.8]}>
        <circleGeometry args={[1.4, 48]} />
        <meshStandardMaterial
          color="#000"
          emissive="#dab877"
          emissiveIntensity={0.22}
          roughness={1}
        />
      </mesh>

      {/* Large sky sphere — gives the glass a rich environment to refract */}
      <mesh scale={[-30, -30, -30]}>
        <sphereGeometry args={[1, 32, 16]} />
        <meshStandardMaterial
          color="#1a0d06"
          emissive="#3d1a08"
          emissiveIntensity={0.4}
          side={2}
          roughness={1}
        />
      </mesh>
    </>
  )
}
