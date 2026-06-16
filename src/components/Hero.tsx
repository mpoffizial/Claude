import { motion } from 'framer-motion'
import './Hero.css'

interface HeroProps {
  reducedMotion?: boolean
}

export function Hero({ reducedMotion = false }: HeroProps) {
  // Staggered reveal — letters/lines come in between 0.8s and ~1.4s
  const container = {
    hidden: {},
    show: {
      transition: {
        staggerChildren: reducedMotion ? 0 : 0.12,
        delayChildren: reducedMotion ? 0 : 0.8,
      },
    },
  }

  const item = {
    hidden: reducedMotion ? { opacity: 0 } : { opacity: 0, y: 40 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.9, ease: [0.16, 1, 0.3, 1] as const },
    },
  }

  return (
    <section className="hero" id="top">
      <motion.div
        className="hero__content"
        variants={container}
        initial="hidden"
        animate="show"
      >
        <motion.p className="hero__sub label" variants={item}>
          Swiss Perfumery · Édition Limitée
        </motion.p>

        <motion.h1 className="hero__title display" variants={item}>
          MISTERY
        </motion.h1>

        <motion.p className="hero__tagline serif-italic" variants={item}>
          Scent as a singular thought.
        </motion.p>
      </motion.div>

      <motion.a
        href="#collection"
        className="hero__scroll"
        aria-label="Scroll to collection"
        initial={reducedMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.6, duration: 0.8 }}
      >
        <span className="hero__scroll-label label">Découvrir</span>
        <span className="hero__scroll-line" aria-hidden="true">
          <span className="hero__scroll-dot" />
        </span>
      </motion.a>
    </section>
  )
}
