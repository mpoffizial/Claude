import { motion } from 'framer-motion'
import { fragrances } from '../data/fragrances'
import './Collection.css'

interface CollectionProps {
  reducedMotion?: boolean
}

export function Collection({ reducedMotion = false }: CollectionProps) {
  const grid = {
    hidden: {},
    show: {
      transition: { staggerChildren: reducedMotion ? 0 : 0.08 },
    },
  }

  const card = {
    hidden: reducedMotion ? { opacity: 0 } : { opacity: 0, y: 30 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.7, ease: [0.16, 1, 0.3, 1] as const },
    },
  }

  return (
    <section className="section collection" id="collection">
      <div className="section__inner">
        <motion.div
          className="section__head"
          initial={reducedMotion ? false : { opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-100px' }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        >
          <span className="label">№ 01 — 06 · Six compositions</span>
          <h2 className="section__title">
            La <em>Collection</em>
          </h2>
        </motion.div>

        <motion.div
          className="collection__grid"
          variants={grid}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-80px' }}
        >
          {fragrances.map((f) => (
            <motion.article
              key={f.number}
              className="card"
              variants={card}
            >
              <div className="card__top">
                <span className="card__number label">MISTERY · {f.number}</span>
                <span className="card__family">{f.family}</span>
              </div>

              <h3 className="card__name serif-italic">{f.name}</h3>

              <dl className="card__pyramid">
                <div className="card__note">
                  <dt>Kopf · Head</dt>
                  <dd>{f.pyramid.head}</dd>
                </div>
                <div className="card__note">
                  <dt>Herz · Heart</dt>
                  <dd>{f.pyramid.heart}</dd>
                </div>
                <div className="card__note">
                  <dt>Basis · Base</dt>
                  <dd>{f.pyramid.base}</dd>
                </div>
              </dl>

              <p className="card__desc">{f.description}</p>

              <span className="card__glow" aria-hidden="true" />
            </motion.article>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
