# Profil-Kit für die GTA-6-Kanäle

Die Accounts legst du selbst an (TikTok, Instagram und Google verlangen dafür eine Telefonnummer
bzw. E-Mail-Bestätigung von dir; automatisch erstellte Accounts werden gesperrt). Mit diesem Kit
dauert das pro Plattform etwa fünf Minuten.

## Name (überall derselbe, vorher prüfen, ob er frei ist)

| Vorschlag | Stil |
|---|---|
| `vicecityclips` | neutral, zeitlos |
| `leonida.clips` | Insider (Bundesstaat im Spiel) |
| `gta6.highlights.de` | klar, deutsch |
| `viceclipsdaily` | „Daily“-Kanal |
| `leonidamoments` | etwas persönlicher |

Vermeide „official“, „Rockstar“ oder „Rockstar Games“ im Namen: Das wirkt wie ein offizieller
Kanal und wird wegen Identitätstäuschung entfernt.

## Profilbild und Banner

Fertig zum Hochladen in `presets/branding/`:

| Datei | Wofür |
|---|---|
| `gta6_profilbild_800.jpg` | Profilbild auf TikTok, Instagram und YouTube („VC“ in Pink/Türkis, Vice-City-Sonnenuntergang) |
| `gta6_banner_2560x1440.jpg` | YouTube-Kanalbanner (Schrift liegt im sicheren Bereich, wird auf Handy und TV nicht abgeschnitten) |

* **Kein** Rockstar-Logo und kein Original-Cover als Profilbild (Markenrecht). Die Vorlagen enthalten keins.

## Bio / Beschreibung

**TikTok (80 Zeichen)**
```
Die besten GTA 6 Momente von Twitch 🎮 Täglich neue Clips · Credits im Video
```

**Instagram**
```
🎮 GTA 6 Highlights von Twitch
🔥 Täglich die besten Momente
📌 Credits im Video · Clip entfernen? DM
```

**YouTube (Kanalbeschreibung)**
```
Die besten GTA 6 Momente aus Twitch-Streams als Shorts. Jeden Tag neue Highlights.
Jeder Clip nennt den Streamer im Video und in der Beschreibung.
Du bist Streamer und möchtest einen Clip entfernt haben? Schreib an: <deine E-Mail>
Inoffizieller Fan-Kanal, nicht verbunden mit Rockstar Games oder Take-Two.
```

## Einstellungen

| Plattform | Einstellung |
|---|---|
| TikTok | Konto öffentlich · Kategorie „Gaming“ · Sprache Deutsch |
| Instagram | Konto öffentlich · Kontotyp „Creator“ (Kategorie „Gaming-Video-Creator“) |
| YouTube | Kanal-Keywords: `gta 6, gta vi, gta 6 clips, twitch highlights` · Land: Deutschland |

## Die ersten Tage

* Neue Accounts zuerst zwei, drei Tage normal benutzen (ein paar Videos ansehen, liken), bevor die
  Maschine postet. Frisch erstellte Accounts, die sofort im Akkord posten, werden schnell eingeschränkt.
* Danach verbinden: auf dem Mac „GTA6-Clipmaschine Konten verbinden“ doppelklicken (YouTube, TikTok, Instagram),
  sonst `python -m clipmaschine auth youtube` bzw. `auth tiktok` / `auth instagram`.
