import { useState } from 'react'
import type { FormEvent } from 'react'
import { motion } from 'framer-motion'
import { MagneticButton } from './MagneticButton'
import { fragrances } from '../data/fragrances'
import './Reservation.css'

interface ReservationProps {
  reducedMotion?: boolean
}

export function Reservation({ reducedMotion = false }: ReservationProps) {
  const [submitted, setSubmitted] = useState(false)

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    // No backend — present a graceful confirmation state.
    setSubmitted(true)
  }

  const reveal = {
    hidden: reducedMotion ? { opacity: 0 } : { opacity: 0, y: 30 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.8, ease: [0.16, 1, 0.3, 1] as const },
    },
  }

  return (
    <section className="section reservation" id="reserver">
      <div className="section__inner reservation__inner">
        <motion.div
          className="reservation__intro"
          variants={reveal}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-100px' }}
        >
          <span className="label">Accès privé</span>
          <h2 className="section__title">
            <em>Réserver</em>
          </h2>
          <p className="reservation__note">
            Only 120 bottles per edition. No waiting lists. No restocks.
          </p>
        </motion.div>

        <motion.div
          className="reservation__form-wrap"
          variants={reveal}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-80px' }}
        >
          {submitted ? (
            <motion.div
              className="reservation__success"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            >
              <span className="reservation__success-mark">✦</span>
              <h3 className="serif-italic">Demande reçue.</h3>
              <p>
                Your request has been registered. If a bottle is allocated to
                you, our atelier in Genève will be in touch personally.
              </p>
            </motion.div>
          ) : (
            <form className="reservation__form" onSubmit={handleSubmit} noValidate>
              <div className="field">
                <label htmlFor="name">Nom</label>
                <input
                  id="name"
                  name="name"
                  type="text"
                  autoComplete="name"
                  placeholder="Your name"
                  required
                />
              </div>

              <div className="field">
                <label htmlFor="email">Adresse e-mail</label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@domain.com"
                  required
                />
              </div>

              <div className="field">
                <label htmlFor="fragrance">Fragrance</label>
                <div className="select-wrap">
                  <select id="fragrance" name="fragrance" defaultValue="">
                    <option value="" disabled>
                      Select an edition
                    </option>
                    {fragrances.map((f) => (
                      <option key={f.number} value={f.name}>
                        {f.number} — {f.name}
                      </option>
                    ))}
                  </select>
                  <span className="select-arrow" aria-hidden="true">
                    ↓
                  </span>
                </div>
              </div>

              <div className="field">
                <label htmlFor="message">Message</label>
                <textarea
                  id="message"
                  name="message"
                  rows={3}
                  placeholder="Tell us what draws you to MISTERY."
                />
              </div>

              <div className="reservation__submit">
                <MagneticButton type="submit">
                  Demander l'accès
                </MagneticButton>
              </div>
            </form>
          )}
        </motion.div>
      </div>
    </section>
  )
}
