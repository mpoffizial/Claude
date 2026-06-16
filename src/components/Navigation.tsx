import { motion } from 'framer-motion'
import './Navigation.css'

interface NavigationProps {
  reducedMotion?: boolean
}

const links = [
  { label: 'Collection', href: '#collection' },
  { label: 'Approche', href: '#approche' },
  { label: 'Réserver', href: '#reserver' },
]

export function Navigation({ reducedMotion = false }: NavigationProps) {
  return (
    <motion.header
      className="nav"
      initial={reducedMotion ? false : { y: -80, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.7, delay: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <a className="nav__logo" href="#top" aria-label="MISTERY — home">
        <span>MISTERY</span>
      </a>

      <nav className="nav__links" aria-label="Primary">
        {links.map((link) => (
          <a key={link.href} className="nav__link" href={link.href}>
            {link.label}
          </a>
        ))}
      </nav>

      <a className="nav__meta" href="#reserver">
        <span className="nav__dot" aria-hidden="true" />
        Édition 2024
      </a>
    </motion.header>
  )
}
