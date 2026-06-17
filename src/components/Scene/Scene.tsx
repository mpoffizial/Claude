import { Suspense, useEffect, useRef } from 'react'
import { Canvas } from '@react-three/fiber'
import { Float } from '@react-three/drei'
import {
  EffectComposer,
  Bloom,
  Vignette,
} from '@react-three/postprocessing'
import { PerfumeBottle } from './PerfumeBottle'
import { ParticleField } from './ParticleField'
import { CameraRig } from './CameraRig'
import { SceneLighting } from './Environment'
import { useMouseParallax } from '../../hooks/useMouseParallax'

interface SceneProps {
  isMobile?: boolean
  reducedMotion?: boolean
}

/**
 * The full 3D hero scene, rendered into a canvas that is fixed behind the page.
 *
 * On desktop: page-scroll-driven camera orbit, drifting particles, transmission
 * glass and a full postprocessing stack (bloom + DOF + vignette).
 *
 * On mobile: a lighter static composition — no postprocessing, fewer particles,
 * physical-material glass, gentle float instead of a scroll orbit.
 */
export function Scene({ isMobile = false, reducedMotion = false }: SceneProps) {
  const mouse = useMouseParallax(!isMobile && !reducedMotion)
  const scrollProgress = useRef(0)

  // Track real page-scroll progress into a ref (no re-renders).
  useEffect(() => {
    let frame = 0
    const update = () => {
      frame = 0
      const top = window.scrollY || document.documentElement.scrollTop
      const max =
        document.documentElement.scrollHeight - window.innerHeight || 1
      scrollProgress.current = Math.min(1, Math.max(0, top / max))
    }
    const onScroll = () => {
      if (frame) return
      frame = requestAnimationFrame(update)
    }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <Canvas
      shadows={!isMobile}
      gl={{
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
      }}
      dpr={isMobile ? [1, 1.5] : [1, 2]}
      camera={{ position: [0, 0.3, 6], fov: 35 }}
    >
      <color attach="background" args={['#15100c']} />
      <fog attach="fog" args={['#15100c', 8, 18]} />

      {/* Lighting + HDRI in its own Suspense so a slow/failed fetch
          never blocks the bottle from rendering */}
      <Suspense fallback={null}>
        <SceneLighting lowPower={isMobile} />
      </Suspense>

      {/* Bottle, particles, and camera rig are immediately available */}
      {isMobile ? (
        // -------- Mobile: static, lightweight --------
        <>
          <Float
            speed={reducedMotion ? 0 : 1.2}
            rotationIntensity={reducedMotion ? 0 : 0.3}
            floatIntensity={reducedMotion ? 0 : 0.4}
          >
            <PerfumeBottle lowPower reducedMotion={reducedMotion} />
          </Float>
          <ParticleField count={90} reducedMotion={reducedMotion} />
          <CameraRig reducedMotion={reducedMotion} />
        </>
      ) : (
        // -------- Desktop: scroll-driven + postprocessing --------
        <>
          <CameraRig
            mouse={mouse}
            reducedMotion={reducedMotion}
            scrollProgress={scrollProgress}
          />
          <PerfumeBottle mouse={mouse} reducedMotion={reducedMotion} />
          <ParticleField count={280} reducedMotion={reducedMotion} />

          <EffectComposer enableNormalPass={false}>
            <Bloom
              luminanceThreshold={0.6}
              intensity={0.6}
              mipmapBlur
              luminanceSmoothing={0.3}
            />
            <Vignette eskil={false} offset={0.3} darkness={0.55} />
          </EffectComposer>
        </>
      )}
    </Canvas>
  )
}
