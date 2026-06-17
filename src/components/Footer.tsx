import './Footer.css'

const socials = [
  { label: 'Instagram', href: '#' },
  { label: 'Journal', href: '#' },
  { label: 'Atelier', href: '#' },
]

export function Footer() {
  return (
    <footer className="footer">
      <div className="footer__inner">
        <div className="footer__brand">
          <span className="footer__logo display">MISTERY</span>
          <span className="footer__place label label--muted">
            Genève, Suisse · Est. 2024
          </span>
        </div>

        <nav className="footer__socials" aria-label="Social">
          {socials.map((s) => (
            <a key={s.label} href={s.href} className="footer__social">
              {s.label}
            </a>
          ))}
        </nav>
      </div>

      <div className="footer__base">
        <span>© {new Date().getFullYear()} MISTERY Parfums SA</span>
        <span className="footer__tag serif-italic">
          Scent as a singular thought.
        </span>
      </div>
    </footer>
  )
}
