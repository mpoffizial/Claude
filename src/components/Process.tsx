import { motion } from 'framer-motion'
import './Process.css'

interface ProcessProps {
  reducedMotion?: boolean
}

const steps = [
  {
    n: '01',
    title: 'Sélection',
    body: 'Only the rarest raw materials, from single origins. Each ingredient is traced to a specific harvest, a specific field, a specific year.',
  },
  {
    n: '02',
    title: 'Composition',
    body: 'Structured by master perfumers and never rushed. A formula may rest for months between revisions until its architecture is exact.',
  },
  {
    n: '03',
    title: 'Édition',
    body: 'Each bottle is individually numbered and sealed by hand. Once an edition is complete, it is never restocked — and never repeated.',
  },
]

export function Process({ reducedMotion = false }: ProcessProps) {
  const list = {
    hidden: {},
    show: { transition: { staggerChildren: reducedMotion ? 0 : 0.14 } },
  }

  const step = {
    hidden: reducedMotion ? { opacity: 0 } : { opacity: 0, y: 30 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.7, ease: [0.16, 1, 0.3, 1] as const },
    },
  }

  return (
    <section className="section process" id="approche">
      <div className="section__inner">
        <motion.div
          className="section__head"
          initial={reducedMotion ? false : { opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-100px' }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        >
          <span className="label">Le savoir-faire</span>
          <h2 className="section__title">
            L'<em>Approche</em>
          </h2>
        </motion.div>

        <motion.ol
          className="process__steps"
          variants={list}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-80px' }}
        >
          {steps.map((s) => (
            <motion.li key={s.n} className="process__step" variants={step}>
              <span className="process__num display">{s.n}</span>
              <div className="process__text">
                <h3 className="process__title">{s.title}</h3>
                <p className="process__body">{s.body}</p>
              </div>
            </motion.li>
          ))}
        </motion.ol>
      </div>
    </section>
  )
}
