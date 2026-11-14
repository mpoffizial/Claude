# OYMYAKON – Markenhandbuch (Stand 14.11.2026)

## Name und Geschichte
**OYMYAKON** – benannt nach dem kältesten bewohnten Ort der Welt (Sibirien, Republik Sakha, 63°27′N 142°47′E;
offiziell −67,7 °C gemessen, inoffiziell −71,2 °C). Haltung: "Wo andere nicht überleben, leben wir."
Inspiration: Corteiz/Alcatraz-Prinzip (realer extremer Ort + Story + einfaches Zeichen), aber eigenständig.
Sommer-Idee: Oymyakon hat auch heiße Sommer (über +30 °C) – Sommer-Drops mit dem Hitze-Rekord als Gegenstück
zu "−71°" (genauen Rekordwert vor Verwendung prüfen).
Markenrecht: Name/Logos vor Produktion bei DPMA/EUIPO prüfen (noch nicht geprüft).

## Logos (Dateien in `jacket_3d/logos/`, transparente Versionen = `*_logo.png`)
- **Hauptzeichen: OYMK** in Gothic/Blackletter – `I_OYMK_gothic_logo.png`
- **Voller Name: OYMYAKON** in derselben Gothic – `M_OYMYAKON_gothic_logo.png`
- Weitere Entwürfe: −71° Stencil/Badge/Wordmark/Icon (A–D), Jakutisches Pferd (E), Kapuzen-Figur (F), Dorf (G),
  Lärche (H), OYM Extended (J), OY-Monogramm (K), OYMK Eis (L)
- Design-Paket `jacket_3d/designs/`: Varsity, Graffiti, Kyrillisch "ОЙМЯКОН", Puffer-OYMK, Brush-Signatur,
  Expedition-Label, Eis-OYMK, Pferd-Emblem

## Farben und Schriften (aus der Website)
Polar Charcoal `#0D0F11`, Ice White `#E9EDEF`, Smoke `#8A9398`, Thermal Red `#D8352A` (nur Akzent).
Schriften: Satoshi, JetBrains Mono, Blackletter nur für die Marke.

## Produkt 001: Weiße Puffer-Jacke (`jacket_3d/`)
Logo-Belegung (vom Nutzer gewählt):
- **OYMK** vorne rechts seitlich, ca. 7 cm hoch, läuft auf dem Körper unter den Ärmel und schaut vorne heraus
  (Platzierung `wrap_frontside_right`, nicht auf den Ärmel drucken)
- **OYMYAKON** klein hinten im Nacken (`nape`)
- **OYMYAKON** klein seitlich an der Kapuze (`hood_side_right`)
- Ausführung: Druck. Produktionshinweis: Taschen-Reißverschluss läuft durch das "O" (Druck braucht dort eine Lücke
  oder Tasche versetzen).

Werkzeuge: 3D-Modell `jacket_3d/jacket_model.glb`, Blender-Szene per `jacket_3d/build_jacket_scene.py`
(Anleitung `jacket_3d/README.md`), finale Renders `jacket_3d/renders/final/`.

## Website
Live: https://oymyakon.higgsfield.app (Higgsfield, website_id `1ff81bfe-7143-41a5-a714-c58c7c2ec656`).
Bauplan und Analyse: `brand/website_blueprint.md`.

## Nächste Produkte
T-Shirts, Hosen usw. nach demselben Ablauf: Fotos/Beschreibung → saubere Ansichten (Higgsfield `gpt_image_2_6`)
→ 3D (Tripo H3.1 Multiview, Reihenfolge vorne/links/hinten/rechts im Ingenieur-Sinn prüfen, sonst "zwei Vorderseiten")
→ Blender-Szene + Logos. Pro Teil eigener Ordner (z. B. `tshirt_3d/`).
