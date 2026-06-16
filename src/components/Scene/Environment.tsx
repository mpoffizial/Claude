import { Environment as DreiEnvironment } from '@react-three/drei'

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
      <ambientLight intensity={0.2} />

      {/* Warm key light, upper-right */}
      <directionalLight
        position={[4, 6, 4]}
        intensity={1.5}
        color="#fff8e7"
        castShadow={!lowPower}
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-bias={-0.0004}
      />

      {/* Champagne rim light from the left */}
      <pointLight position={[-5, 1.5, 2]} intensity={0.8} color="#dab877" />

      {/* Sharp specular spotlight from directly above */}
      <spotLight
        position={[0, 7, 0.5]}
        intensity={2}
        angle={0.32}
        penumbra={0.6}
        color="#ffffff"
        castShadow={!lowPower}
      />

      {/* Cool fill from behind to separate the glass from the dark bg */}
      <pointLight position={[0, -1, -5]} intensity={0.4} color="#8b7d8a" />

      {/* HDRI for realistic glass reflections */}
      <DreiEnvironment preset="studio" environmentIntensity={lowPower ? 0.6 : 0.9} />
    </>
  )
}
