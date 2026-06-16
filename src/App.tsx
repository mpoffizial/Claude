import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Scene } from './components/Scene/Scene'
import { Navigation } from './components/Navigation'
import { Hero } from './components/Hero'
import { Collection } from './components/Collection'
import { Process } from './components/Process'
import { Reservation } from './components/Reservation'
import { Footer } from './components/Footer'
import { useReducedMotion } from './hooks/useReducedMotion'
import { useIsMobile } from './hooks/useIsMobile'
import './App.css'

export default function App() {
  const { prefersReducedMotion } = useReducedMotion()
  const isMobile = useIsMobile()
  const [loaded, setLoaded] = useState(false)

  // Lift the loading overlay shortly after mount so the staggered intro can run.
  useEffect(() => {
    const t = setTimeout(() => setLoaded(true), prefersReducedMotion ? 200 : 800)
    return () => clearTimeout(t)
  }, [prefersReducedMotion])

  return (
    <div className="app">
      {/* Loading overlay */}
      <AnimatePresence>
        {!loaded && (
          <motion.div
            className="overlay"
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          >
            <motion.span
              className="overlay__mark display"
              initial={{ opacity: 0, letterSpacing: '0.6em' }}
              animate={{ opacity: 1, letterSpacing: '0.3em' }}
              transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
            >
              MISTERY
            </motion.span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Fixed 3D background canvas */}
      <motion.div
        className="canvas-layer"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{
          duration: 1,
          delay: prefersReducedMotion ? 0 : 0.6,
          ease: 'easeOut',
        }}
      >
        <Scene isMobile={isMobile} reducedMotion={prefersReducedMotion} />
        <div className="canvas-layer__grain" aria-hidden="true" />
      </motion.div>

      <Navigation reducedMotion={prefersReducedMotion} />

      <main>
        <Hero reducedMotion={prefersReducedMotion} />
        <Collection reducedMotion={prefersReducedMotion} />
        <Process reducedMotion={prefersReducedMotion} />
        <Reservation reducedMotion={prefersReducedMotion} />
      </main>

      <Footer />
    </div>
  )
}
