# OYMYAKON – 3D-Puffer-Jacke (Blender)

Weiße Puffer-Jacke als 3D-Modell mit Studio-Szene und Logos der Brand **OYMYAKON**.

## Inhalt

| Pfad | Was |
|---|---|
| `jacket_model.glb` | Das 3D-Modell der Jacke (Geometrie + Texturen) |
| `alternativ/jacket_model_meshy.glb` | Alternatives, leichteres Modell |
| `build_jacket_scene.py` | Baut die komplette Blender-Szene: Material, Studio, Kameras, Drehteller, Logos |
| `logos/` | Alle Logo-Entwürfe; `*_logo.png` = transparente, zugeschnittene Versionen für die Jacke |
| `renders/final/` | Finale Produktbilder mit Logos |
| `renders/…` | Mockups und Varianten |
| `jacket_scene.blend` | Fertige Szene (wird **nicht** in Git gespeichert, da > 100 MB; siehe unten) |

Aktuelle Logo-Belegung:

- **OYMK** (Gothic) vorne rechts an der Seitenkante, ca. 7 cm hoch, läuft um die Rundung in die Seite (nicht auf den Ärmel)
- **OYMYAKON** klein hinten im Nacken
- **OYMYAKON** klein seitlich an der Kapuze

## Szene neu erzeugen

Benötigt Blender 4.x.

```bash
cd jacket_3d
blender -b --factory-startup -P build_jacket_scene.py -- \
  glb=jacket_model.glb rotz=-90 exposure=-0.5 backdrop=0.22,0.22,0.215 finish=print \
  logo=logos/I_OYMK_gothic_logo.png,logos/M_OYMYAKON_gothic_logo.png,logos/M_OYMYAKON_gothic_logo.png \
  logo_pos=wrap_frontside_right,nape,hood_side_right logo_size=0.15,0.085,0.09 \
  out=jacket_scene.blend
```

Mit `render=threequarter,front,back,side,torso@45,logo_nape,logo_hood_side_right` werden zusätzlich Bilder nach `renders/` gerendert.
`finish=embroidery` oder `finish=patch` ändert die Ausführung der Logos (Stickerei / Patch statt Druck).

## Logos in Blender anpassen

- **Flache Logos** (Nacken, Kapuze): Das Bild-Objekt `Logo_nape` bzw. `Logo_hood_side_right` verschieben, drehen oder skalieren – das Logo wandert auf der Jacke mit.
- **Seitenlogo** (`Logo_wrap_frontside_right`, Pfeil auf der Körperachse): um die Z-Achse drehen = Logo um den Körper schieben, hoch/runter bewegen = Höhe. Breite im Material über den Node „Wrap Width (m)“.
- **Logo austauschen:** Im Material der Jacke im Rahmen des Logos den Bild-Node „LOGO IMAGE (hier tauschen)“ auf eine andere PNG-Datei (transparenter Hintergrund) setzen.
- **360°-Video:** Der Drehteller ist animiert (Frame 1–120). Kamera `Cam_Front` wählen und als Animation rendern.
