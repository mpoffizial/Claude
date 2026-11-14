# Website-Bauplan – so baut Higgsfield Brand-Websites (Analyse von oymyakon.higgsfield.app)

Stand: 14.11.2026. Quelle: Quellcode der Higgsfield-Website `oymyakon` (website_id
`1ff81bfe-7143-41a5-a714-c58c7c2ec656`, Slug `oymyakon`, URL https://oymyakon.higgsfield.app)
und der Higgsfield-Workflow `website-builder-flow` (Referenz `references/website-flow.md`).

> Hinweis: Von außen leitete die Live-URL am 14.11.2026 auf den Higgsfield-Login um
> (Seite war nur mit Login sichtbar). Zum öffentlichen Teilen prüfen, ob sie im Inkognito-Fenster
> erreichbar ist, sonst in Higgsfield veröffentlichen.

---

## 1. Wie Higgsfield eine Website baut (die Maschine dahinter)

**Werkzeuge (Higgsfield MCP):** `get_workflow_instructions {workflow: "website-builder-flow"}` zuerst laden,
dann `create_website` → `website_repo_access` (checkout) → Code im Higgsfield-Sandbox (`sandbox_exec`) schreiben
und committen → `website_repo_access` (push) → `deploy_website` → optional `publish_website` (Community-Feed).
Bestehende Seiten: `list_websites`, Status: `website_status`.

**Typ wählen (`type`):**
- `website` = eigenständige Marke, eigenes Design, keine KI-Generierung auf der Seite. **Das ist unser Fall (Shop/Brand).**
- `app` = mit "Sign in with Higgsfield" und Bild/Video-Generierung (Quanta-UI).
- `game` = Browser-Spiel.

**Stack:** React 19 + TanStack Start (SSR), Vite 7, bun, Tailwind v4, deployt als ein Cloudflare Worker.
Projekt liegt in `app/`. Server-Logik über `createServerFn` (kein Express). Datenbank D1 (`"db": true` in
`app/app.manifest.json`), optional R2-Speicher, KV, Durable Objects, Container.

**Template:** Animierte Seite = `template: "scroll-scrub"` → die Scroll-Film-Engine ist fertig in
`app/src/components/scroll-scrub/scroll-scrub.tsx`; man füllt nur `app/src/scroll-scrub-scenes.ts`.
Nicht-animiert = ohne Template. **Vor dem Anlegen immer fragen: Animiert (empfohlen) oder nicht?**
Außerdem fragen: vorhandene Marke (Farben/Fonts/Logos) oder freie Hand, und ob in den Feed veröffentlicht werden soll.

**Pipeline (Reihenfolge, nichts überspringen):**
1. **Phase 0 – Design-Brief** `app/design-brief.md`: Design-Read (Zielgruppe + Gefühl), Concept Spine (eine
   erzählerische Leitidee), Delivery Tier (`cinema` Standard / `spectacle` WebGL/3D / `editorial`),
   **feste Farbpalette mit Hex-Codes** (verboten: Graphit+Orange, Schwarz+Neon, Beige+Messing, KI-Lila),
   **feste Schriften**, `Animation mode: animated-website` + Journey (Kapitel des Scroll-Films),
   Section-Plan (jede Sektion eigene Layout-Familie), Asset-Plan, CTA-Inventar (jeder Button eigenes Design).
2. **Phase 1 – Referenz-Boards:** pro Sektion ein Design-Bild mit `generate_image` (Palette fix), schlechte neu würfeln.
3. **Phase 2 – Asset-System:** alle Bilder/Icons/Logo/Favicon/Material-Platten/Film gleichzeitig generieren lassen,
   in `app/public/assets/` ablegen. Kein Stock, keine Platzhalter.
4. **Phase 3 – Bauen nach den Boards**, Sektion für Sektion, jede Datei einmal komplett schreiben.
5. **Phase 4 – Motion:** Lenis + GSAP ScrollTrigger, Scroll steuert den Film, nichts wartet auf `opacity: 0`,
   alles mit `prefers-reduced-motion`-Fallback, kein `window` beim SSR-Render.
6. **Phase 5 – Prüfliste** (`references/review-rubric.md` §A): keine Platzhalter, keine Gedankenstriche, Palette ok,
   alle Assets benutzt, SSR-sicher, CTA-Klassen nicht wiederholt.
7. **Phase 6 – Deploy:** `bun run typecheck` im Sandbox, dann `deploy_website`.
   Pflicht vor Fertig/Publish: Cover + Metadaten in `app/src/app-meta.json`
   (`og_title`, `og_description`, `og_image_url`, `marketplace_cover_url`, `favicon_url`), siehe `references/app-cover.md`.

**Mit dem Nutzer reden:** keine Technik-Wörter (Repo, Commit, Push, Deploy) – stattdessen "Seite wird eingerichtet",
"Änderungen werden gespeichert", "Deine Seite ist live: <URL>".

---

## 2. Was die OYMYAKON-Seite konkret ausmacht

**Design-Read:** Streetwear-Käufer, die direkt kaufen. Kalt, technisch, leise aggressiv. "Ein Shop in einem schwarzen
Studio, kein Lookbook."

**Concept Spine:** Schwarzes Studio, eisweißes Produkt, Haarlinien-Interface, ein einziges thermisch-rotes Signal.
Alles auf der Seite ist entweder ein Kleidungsstück, ein Preis oder ein Bedienelement.

**Palette (fest):**

| Rolle | Hex | Name |
|---|---|---|
| Hintergrund | `#0D0F11` | Polar Charcoal |
| Text/Ink | `#E9EDEF` | Ice White |
| Gedämpft | `#8A9398` | Smoke |
| Akzent | `#D8352A` | Thermal Red – nur für Preise, Zustände, Markierungen |

**Schriften:** Satoshi (Display + Text), JetBrains Mono (Preise, Größen, Labels), Blackletter nur für die Marke
(Wortmarke OYMYAKON und OYMK). Keine Serifenschrift. Dateien: `app/public/assets/fonts/*.woff2`.

**Hero = Scroll-Film (A4, single-shot):** ein durchgehender ~15-s-Film der Jacke im schwarzen Studio, in 5 Clips
geschnitten (Desktop + leichte Mobile-Versionen + Poster je Clip), Scroll spult den Film. 5 Kapitel:
1. "Streetwear, worn out" (New drop) – Jacke weit hinten im Dunkeln
2. "The 001 puffer" – näher, Steppung und Kapuze (Tags: €240, XS bis XXL)
3. "Wide, not sloppy" – Volumen von Kapuze und Ärmeln
4. "One mark, one colour" – das OYMK-Brustlogo läuft durchs Bild
5. "The rest of the range" – Makro des Stoffs zum Schluss

Welt-Grammatik: tiefschwarze Hohlkehle, ein kaltes silbernes Führungslicht von oben links, schwaches rotes Licht am
rechten Rand, Frost-Staub, 50 mm, feste Belichtung, langsame konstante Kamerafahrt, keine Schnitte.
Budget: Desktop-Clips ≤ 32 MiB, Mobile ≤ 16 MiB.

**Sektionen (jede eigene Layout-Familie):**
1. Journey – Scroll-Film, angepinnte Bühne mit Text-Spalte
2. Shop – asymmetrisches Produkt-Grid: eine Fotokarte + typografische Karten für Teile in Produktion, mit Preis und Status
3. Produktdetail – Split: Medien (Galerie mit 6 Ansichten + **3D-Viewer** `model-viewer` mit `puffer-001.glb`) +
   klebendes Kauf-Panel mit Größen-Chips, "Add to bag", Spezifikationsliste
4. Fabric – vollbreites, versetztes Material-Band, 3 Nahaufnahmen, nur Bildunterschriften
5. About – typografische Wand, Blackletter-Wortmarke vom Bildrand angeschnitten, vertikale Seitennotiz
6. Drop-Termine – asymmetrischer Split mit E-Mail-Feld (Newsletter/Waitlist in D1)
7. Footer – eine Haarlinien-Zeile

**Buttons (jeder anders):** Navigation mit sich zeichnender Haarlinie; "Pick a size" als rot unterstrichener Mono-Link;
"Add to bag" als volle Outline-Leiste mit rotem Caret; "Place order" als rote Fläche, die sich beim Hover leert;
"Subscribe" als umrandeter Chip; Warenkorb als fixe Leiste mit rotem Zähler-Quadrat. Keine Pillen, alle Ecken scharf.

**Shop-Technik:** Warenkorb im Browser, Bestellung als echte Zeile in D1 (`orders`: Referenz, Name, E-Mail, Adresse,
Artikel-JSON, Summe, EUR). Preise immer serverseitig aus dem Katalog (`src/shop/catalog`), Validierung mit zod.
Bezahlung: Zahlungslink per Mail (keine Kartendaten auf der Seite). Newsletter-Tabelle `waitlist`.

**Assets:** generierter Film + Poster, die eigenen 6 Produktfotos freigestellt und auf die dunkle Studio-Platte gesetzt
(Front, Winkel, Rücken, Kapuzen-Print, Rücken-Print, Brust-Detail), 3 generierte Material-Platten, Cover/OG/Favicon,
komplettes Icon-Set (`app/public/assets/head/`).

**SEO/Meta:** `og_title` "OYMYAKON | Streetwear, sold direct", robots.txt + sitemap.xml als Routen, StructuredData-Komponente.

---

## 3. Rezept für die nächsten Seiten (ähnlich oder besser)

1. Workflow laden: `get_workflow_instructions {workflow: "website-builder-flow"}` und `references/website-flow.md` lesen.
2. Fragen (eine Runde): Animiert? Bestehende Brand nutzen? In den Feed veröffentlichen?
3. **Brand-Vorgaben aus `brand/README.md` übernehmen** (Palette, Schriften, Logos, Logo-Positionen), außer der Nutzer will
   bewusst einen neuen Look (z. B. Sommer-Drop).
4. Eigene Assets wiederverwenden: Renders/3D-Modell aus `jacket_3d/` (GLB für den 3D-Viewer, finale Renders für die Galerie),
   Logos aus `jacket_3d/logos/`, Designs aus `jacket_3d/designs/`.
5. Pipeline Phase 0–6 wie oben.

**Ideen, um es noch besser zu machen:**
- **Rechtliches (Pflicht für einen Shop in DE/EU):** Impressum, Datenschutzerklärung, AGB, Widerrufsbelehrung,
  Versand- und Zahlungsinfos, Cookie-Hinweis falls Tracking. Fehlte in der ersten Version (nur `/`, robots, sitemap).
- Echte Zahlung (z. B. Stripe Checkout mit eigenem Schlüssel über `website_secrets`) statt Zahlungslink per Mail.
- Eigene Produktseiten pro Artikel (`/p/<slug>`) für SEO und zum Teilen, mit JSON-LD `Product`.
- Größentabelle mit Maßen, Fit-Hinweis ("eine Größe kleiner für regular fit").
- Sprachumschalter DE/EN.
- AR-Ansicht auf dem iPhone (USDZ neben der GLB im `model-viewer`, `ar`-Attribut).
- Drop-Countdown + Waitlist mit Bestätigungs-Mail.
- Lookbook/Kampagnen-Sektion mit generierten Model-Fotos (Higgsfield `soul_2`/Marketing Studio).
- `multi-leg`-Journey, wenn die Kollektion mehrere Welten hat (Winter −71° / Sommer-Hitze).
- Turntable-Video der Jacke (aus `jacket_3d/jacket_scene.blend`, Drehteller-Animation Frame 1–120).
