# 🎬 Clipmaschine

Die Clipmaschine sucht automatisch die besten Momente aus Twitch-Streams, schneidet sie zu
viralen Hochkant-Videos und postet sie nach Zeitplan auf **TikTok**, **YouTube Shorts** und
**Instagram Reels**. Sie läuft rund um die Uhr. Bei erreichten Limits macht sie danach
automatisch weiter, und von den meisten Fehlern erholt sie sich selbst.

```
 Twitch                                Clipmaschine                                     Plattformen
┌──────────────┐   ┌───────────────┐   ┌──────────────────────────┐   ┌──────────────┐   ┌─────────────┐
│ Top-Clips    │──►│ Highlights    │──►│ Schnitt 9:16 (ffmpeg)    │──►│ Zeitplan     │──►│ TikTok      │
│ Live-Chat    │──►│ bewerten,     │   │ Hook · Untertitel ·      │   │ Tageslimit,  │──►│ YT Shorts   │
│ (Hype-Spitze)│   │ Duplikate weg │   │ Credit · Lautheit        │   │ Abstand,     │──►│ Reels       │
└──────────────┘   └───────────────┘   │ Titel/Hashtags (Claude)  │   │ Uhrzeiten    │   └─────────────┘
                                       └──────────────────────────┘   └──────────────┘
```

---

## ⚠️ Zuerst: Rechte und Plattformregeln

* **Nur Streamer eintragen, die zugestimmt haben**: dein eigener Kanal, ein offizielles
  Clipping-Programm oder eine ausdrückliche Erlaubnis. Fremde Streams ohne Erlaubnis zu reposten
  verletzt das Urheberrecht. Konten werden dafür gesperrt, und Monetarisierung wird als
  „nicht originaler Inhalt“ abgelehnt.
* Jedes Video enthält automatisch einen Credit (`twitch.tv/<streamer>`) im Bild und in der Beschreibung.
* Die Plattformen schalten öffentliches Posten per API erst nach einer Prüfung frei.
  Details stehen weiter unten bei der jeweiligen Plattform.

---

## ✨ Funktionen

| Bereich | Was passiert |
|---|---|
| **Top-Clips** | Holt die meistgesehenen Clips der letzten Stunden. Ein Score aus „Views pro Zeit“ bevorzugt frische Clips, die gerade viral gehen. Clips desselben Moments werden erkannt, und nur der beste wird genommen. |
| **Live-Modus** | Liest den Chat live mit. Explodiert er (z. B. 3× aktiver als normal, viele verschiedene Chatter, *KEKW/LUL/W/Clip it*), erstellt die Maschine sofort selbst einen Twitch-Clip. |
| **Schnitt** | Drei Layouts: `blur` (Video mittig, unscharfer Hintergrund), `crop` (bildfüllend), `split` (Facecam oben, Gameplay unten). Dazu Hook-Text oben, Credit und Lautheits-Angleichung auf −14 LUFS. |
| **Untertitel** *(optional)* | Läuft lokal und kostenlos mit faster-whisper. Wörter erscheinen in kurzen Häppchen, das gerade gesprochene Wort wird gelb hervorgehoben (Karaoke-Stil). |
| **Titel & Hashtags** | Mit Claude (optional), sonst per Vorlage. Hook, Titel, Beschreibung mit Credit und 5–8 Hashtags. |
| **Posten** | TikTok (**direkt mit deinem Account** über den Browser oder per offizieller API), YouTube Shorts und Instagram Reels, mit Tageslimit, Mindestabstand und Uhrzeiten pro Plattform. |
| **Dauerbetrieb** | Selbstheilung, Schutz vor Doppelstart, Heartbeat sowie Auto-Neustart per Docker, systemd oder Windows-Skript. |

---

## ⚡ Schnellstart unter Windows: ein Befehl

**PowerShell** öffnen (Startmenü → „PowerShell“), diesen Befehl einfügen und Enter drücken:

```powershell
irm https://raw.githubusercontent.com/mpoffizial/Claude/claude/blissful-turing-83j3hp/clipmaschine/deploy/install_windows.ps1 | iex
```

Der Installer
* installiert fehlendes Python und ffmpeg (per winget),
* legt die Clipmaschine in `Desktop\Clipmaschine` ab. Liegt dein Desktop in OneDrive, landet sie stattdessen in
  einem lokalen Ordner (vermeidet Sync-Konflikte), und der Desktop bekommt eine Verknüpfung dorthin,
* erstellt auf dem Desktop **„Clipmaschine starten“** und **„Clipmaschine aktualisieren“**,
* startet die Maschine. Beim ersten Mal führt ein Assistent durch Twitch-Kanäle, Twitch-Zugang und die
  TikTok-Anmeldung per QR-Code.

Updates: Doppelklick auf „Clipmaschine aktualisieren“. Deine Einstellungen, Logins und Daten bleiben erhalten.

### Alternative: ZIP und Doppelklick

1. Einmalig installieren (in der Eingabeaufforderung):
   ```bat
   winget install Python.Python.3.12
   winget install Gyan.FFmpeg
   ```
2. Das Repository herunterladen (grüner Button **Code → Download ZIP** auf GitHub, Branch
   `claude/blissful-turing-83j3hp`) und entpacken.
3. **`clipmaschine\deploy\start_windows.bat` doppelklicken.** Beim ersten Start:
   * richtet das Skript die Python-Umgebung und alle Pakete selbst ein,
   * fragt der Assistent deine Twitch-Kanäle und die Twitch-Zugangsdaten ab (Claude-Key optional),
   * öffnet sich ein Browserfenster für die **TikTok-Anmeldung** (am einfachsten per QR-Code),
   * startet danach die Maschine und hält sie am Laufen (Neustart nach Abstürzen).

Ab dann reicht ein Doppelklick auf dieselbe Datei. Den Assistenten kannst du jederzeit erneut starten:
`python -m clipmaschine setup`.

## 🚀 Installation (manuell, alle Systeme)

**Voraussetzungen:** Python 3.10 oder neuer und **ffmpeg** (mit libass, bei den üblichen Builds dabei).

* Windows: `winget install Gyan.FFmpeg`
* macOS: `brew install ffmpeg`
* Linux: `sudo apt install ffmpeg`

```bash
cd Claude                                   # das Repository
python -m venv .venv
source .venv/bin/activate                   # Windows: .venv\Scripts\activate
pip install -r clipmaschine/requirements.txt
cp clipmaschine/config.example.yaml config.yaml
cp clipmaschine/.env.example .env
```

Danach `config.yaml` (Streamer, Plattformen) und `.env` (Zugangsdaten) ausfüllen und prüfen:

```bash
python -m clipmaschine check
```

### Erster Test ohne Accounts

Mit einer beliebigen Videodatei lässt sich der Schnitt sofort ausprobieren:

```bash
python -m clipmaschine process --file mein_clip.mp4 --streamer deinname --title "Krasser Moment"
```

Das fertige Video liegt danach in `clipmaschine_data/rendered/`.

---

## 🔑 Plattformen einrichten

### Twitch (Pflicht)

1. <https://dev.twitch.tv/console/apps> öffnen, **Register Your Application** wählen.
2. Als OAuth-Redirect-URL `http://localhost:17563/twitch/callback` eintragen. Client-Typ: *Confidential*.
3. Client-ID und ein neues Secret in die `.env` eintragen (`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`).
4. **Nur für den Live-Modus** einmal mit dem Account anmelden, der die Clips erstellen soll:
   ```bash
   python -m clipmaschine auth twitch
   ```

### TikTok – direkt mit deinem Account verbinden (Browser-Modus, empfohlen)

Du brauchst **kein Entwickler-Konto**, und die Videos sind sofort öffentlich, so wie bei einem normalen Upload.

1. In der `config.yaml` setzen:
   ```yaml
   tiktok:
     enabled: true
     method: browser
   ```
2. Verbinden:
   ```bash
   python -m clipmaschine auth tiktok
   ```
   Ein Browserfenster öffnet sich. Melde dich dort ganz normal an, am einfachsten per **QR-Code**
   (TikTok-App → Profil → ☰ → QR-Code scannen). Das Fenster schließt sich danach von selbst.
   Dein Passwort bekommt die Clipmaschine nie zu sehen. Die Anmeldung liegt in einem eigenen
   Browserprofil unter `clipmaschine_data/browser/tiktok/`.
3. Fertig. Bei jedem Post öffnet sich kurz ein Browserfenster: Die Maschine lädt das Video in TikTok
   Studio hoch, trägt Titel und Hashtags ein und klickt auf „Posten“.

So bleibt es zuverlässig:

* **Kein Doppel-Post:** Scheitert etwas *vor* dem Klick auf „Posten“, versucht die Maschine es später
  erneut. Nach dem Klick wird nie automatisch noch einmal gepostet. Bleibt die Bestätigung aus, gilt das
  Video als eingereicht, und ein Screenshot zur Kontrolle liegt in `clipmaschine_data/logs/tiktok_screenshots/`.
* **Anmeldung abgelaufen:** TikTok pausiert, die anderen Plattformen laufen weiter. Sobald du
  `python -m clipmaschine auth tiktok` erneut ausführst, geht es **sofort** weiter, auch während die Maschine läuft.
* **Sicherheitsprüfung (Captcha):** Die Maschine holt das Fenster nach vorne und wartet bis zu 5 Minuten, bis du sie gelöst hast.
* Nutzt dein installiertes **Google Chrome**. Ist keins installiert, einmal `python -m playwright install chromium` ausführen.
* Ändert TikTok die Upload-Seite, lassen sich die Erkennungsmerkmale über `tiktok.browser_selectors` anpassen, ohne Code zu ändern.

> ⚠️ Laut Nutzungsbedingungen erlaubt TikTok automatisiertes Hochladen über die Website eigentlich nicht.
> Das Risiko betrifft nur deinen Account (im schlimmsten Fall Einschränkungen). Bleib deshalb bei moderaten
> Limits (Standard: 4 Posts pro Tag, 2 Stunden Abstand). Der Browser-Modus ist für den Betrieb auf deinem
> PC gedacht. Auf Servern ohne Bildschirm `browser_headless: true` setzen, besser aber die API nutzen.

### TikTok – offizielle API (Alternative)

1. Auf <https://developers.tiktok.com> eine App anlegen und die Produkte **Login Kit** und **Content Posting API** hinzufügen.
   Für `mode: direct` zusätzlich *Direct Post* aktivieren.
2. Plattform **Desktop** wählen, Redirect-URI `http://localhost:17564/tiktok/callback`.
3. Scopes: `user.info.basic` plus `video.publish` (direct) bzw. `video.upload` (inbox).
4. `TIKTOK_CLIENT_KEY` und `TIKTOK_CLIENT_SECRET` in die `.env` eintragen, `tiktok.method: api` setzen und anmelden:
   ```bash
   python -m clipmaschine auth tiktok
   ```
5. **Wichtig:** Solange TikTok deine App nicht geprüft hat (Audit), dürfen Videos nur **privat**
   (`privacy_level: SELF_ONLY`) gepostet werden, und der TikTok-Account muss dafür auf privat stehen.
   Nach dem Audit `privacy_level: PUBLIC_TO_EVERYONE` setzen.
   **Alternative:** `mode: inbox`. Dann landet jedes Video als Entwurf in deiner TikTok-App und du tippst nur noch auf „Posten“.

> Falls der Login mit „invalid code_verifier“ o. Ä. scheitert (bei Web- statt Desktop-Apps): `tiktok.use_pkce: false`.

### YouTube Shorts

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt anlegen und die **YouTube Data API v3** aktivieren.
2. Den **OAuth-Zustimmungsbildschirm** einrichten (extern, dich selbst als Testnutzer eintragen).
3. Unter *Anmeldedaten* eine **OAuth-Client-ID vom Typ „Desktop-App“** erstellen, die JSON herunterladen
   und als `client_secret.json` neben die `config.yaml` legen.
4. Anmelden:
   ```bash
   python -m clipmaschine auth youtube
   ```
5. **Wichtig für 24/7:** Im Status *„Testing“* laufen Google-Logins nach 7 Tagen ab. Setze die App
   deshalb auf **„In Produktion“**. Für die eigene Nutzung ist keine Verifizierung nötig, Google zeigt nur einen Warnhinweis.
6. Hinweise: Videos aus ungeprüften API-Projekten sind zunächst **privat**, bis Google das Projekt
   freigibt (YouTube API Audit). Das Tageskontingent reicht für etwa 6 Uploads pro Tag. Ist es aufgebraucht,
   pausiert die Maschine YouTube **bis zum Reset um Mitternacht (US-Westküste)** und macht dann von selbst weiter.

### Instagram Reels

1. Den Instagram-Account auf **Business oder Creator** umstellen.
2. Auf <https://developers.facebook.com> eine App mit dem Produkt **Instagram** anlegen
   (*Instagram API with Instagram Login*). Berechtigungen: `instagram_business_basic` und `instagram_business_content_publish`.
3. Für deinen Account einen Token erzeugen und in die `.env` eintragen: `INSTAGRAM_USER_ID` und `INSTAGRAM_ACCESS_TOKEN`.
4. Der langlebige Token gilt 60 Tage. **Die Clipmaschine erneuert ihn automatisch** (wöchentlich, gespeichert in `clipmaschine_data/tokens/`).
5. Wer die Facebook-Login-Variante nutzt (Seiten- oder System-User-Token, läuft nicht ab), setzt `instagram.graph_host: graph.facebook.com`.

### Claude für Titel & Hashtags (optional)

* Auf <https://console.anthropic.com> einen API-Key erstellen und `ANTHROPIC_API_KEY=` in die `.env` eintragen.
* Genutzt wird **Claude Opus 5.5** mit niedrigem Aufwand (`effort: low`). Das kostet grob 1–2 Cent pro Clip.
* Claude schreibt Hook, Titel, Beschreibung und Hashtags passend zu Clip-Titel, Spiel, Chat-Reaktion und
  (falls Untertitel aktiv) dem Gesprochenen. Ausdrücklich verboten sind erfundene Ereignisse und irreführendes Clickbait.
* Die serverseitigen **Fallbacks** sind aktiviert: Lehnt ein Sicherheitsfilter eine Anfrage ab, beantwortet ein Ersatzmodell sie automatisch.
* Ohne Key, bei Ablehnung oder bei Fehlern greift automatisch die Vorlage. Die Maschine läuft also immer weiter.

### Untertitel (optional)

```bash
pip install faster-whisper
```

Danach in der `config.yaml` `captions.enabled: true` setzen. Läuft auf der CPU. Beim ersten Start wird das Modell geladen (`small`, etwa 500 MB).

---

## 🕹️ Benutzung

| Befehl | Zweck |
|---|---|
| `python -m clipmaschine setup` | Einrichtungs-Assistent: Kanäle, Zugangsdaten, TikTok verbinden |
| `python -m clipmaschine check` | Einrichtung prüfen: ffmpeg, Twitch, Plattformen, KI, Untertitel |
| `python -m clipmaschine discover` | Aktuelle Top-Clips suchen, anzeigen und vormerken |
| `python -m clipmaschine process <clip-url>` | Einen bestimmten Clip schneiden (mit `--upload` sofort posten) |
| `python -m clipmaschine process --file video.mp4 --streamer name` | Lokale Videodatei schneiden |
| `python -m clipmaschine frame <clip-url>` | Standbild mit Raster zum Ausmessen der Facecam |
| `python -m clipmaschine run --dry-run` | Alles durchspielen, **ohne** hochzuladen (zum Testen) |
| `python -m clipmaschine run` | **Dauerbetrieb** |
| `python -m clipmaschine status` | Läuft die Maschine? Warteschlange, letzte Uploads |
| `python -m clipmaschine auth twitch\|tiktok\|youtube` | Einmalige Anmeldung |
| `python -m clipmaschine health` | Exit-Code 0, wenn die Maschine läuft (Monitoring) |

**Empfohlener Start:** `check` → `process <clip-url>` (Ergebnis anschauen) → `run --dry-run` → `run`.

### Facecam-Layout (`split`) einstellen

```bash
python -m clipmaschine frame https://clips.twitch.tv/DeinClip
```

Das Standbild hat ein rotes 10-%-Raster. Liegt die Facecam z. B. oben rechts über 3×3 Kästchen:

```yaml
streamers:
  - login: dein_streamer
    layout: split
    facecam: {x: 0.7, y: 0.0, w: 0.3, h: 0.3}
```

---

## 🛡️ Was passiert bei Limits und Fehlern?

Die Maschine ist so gebaut, dass sie **ohne Eingreifen** weiterläuft:

| Situation | Reaktion |
|---|---|
| Tageslimit oder Mindestabstand erreicht | Die genaue Uhrzeit des nächsten Posts wird einmal geloggt, dann geht es **automatisch** weiter. Inzwischen werden neue Clips vorbereitet. |
| Außerhalb der Posting-Zeiten | Wartet bis zum Beginn des nächsten Zeitfensters. |
| YouTube-Kontingent aufgebraucht | YouTube pausiert bis zum Reset (Mitternacht US-Westküste), die anderen Plattformen laufen weiter. |
| Rate-Limit bei TikTok, Instagram oder Twitch | Pause bzw. Warten bis zum Reset, danach automatische Fortsetzung. Zählt nicht als Fehlversuch. |
| Netzwerkfehler, Server-Fehler (5xx) | Sofortige Wiederholung mit wachsender Wartezeit, sonst neuer Versuch im nächsten Slot (bis zu `max_attempts`). |
| Clip lässt sich nicht laden oder rendern | Neuer Versuch nach 5, dann 10 Minuten. Erst danach wird er übersprungen und der nächste Clip genommen. |
| Gerendertes Video fehlt | Wird automatisch neu gerendert. |
| Plattform lehnt ein Video endgültig ab | Kein weiterer Versuch für diesen Clip. Andere Clips und Plattformen laufen normal weiter. |
| Tokens laufen ab | Twitch, TikTok, YouTube und Instagram werden automatisch erneuert. |
| TikTok-Anmeldung im Browser-Modus abgelaufen | TikTok pausiert, der Rest läuft weiter. Nach `auth tiktok` geht es **sofort** weiter. |
| Chat-Verbindung bricht ab | Automatischer Reconnect. Ein Watchdog startet abgestürzte Threads neu. |
| Hängender ffmpeg- oder Download-Prozess | Zeitlimit, Abbruch, neuer Versuch. |
| Wenig Speicherplatz | Downloads pausieren, bis wieder genug Platz frei ist (`min_free_disk_gb`). |
| Programm stürzt ab oder Server startet neu | Docker, systemd oder das Windows-Skript starten es neu. Reste des alten Laufs werden aufgeräumt, nichts wird doppelt gepostet. |
| Versehentlich zweimal gestartet | Die zweite Instanz beendet sich sofort (Dateisperre), also keine Doppel-Posts. |

Alle Logs landen zusätzlich in `clipmaschine_data/logs/clipmaschine.log`, rotierend mit maximal 4 × 5 MB.

---

## 🖥️ Dauerbetrieb (24/7)

### Docker (empfohlen für Server)

```bash
mkdir -p clipmaschine/docker-data
cp config.yaml .env clipmaschine/docker-data/           # ggf. auch client_secret.json
cp -r clipmaschine_data clipmaschine/docker-data/       # enthält die Logins aus "auth"
docker compose -f clipmaschine/docker-compose.yml up -d --build
docker compose -f clipmaschine/docker-compose.yml logs -f
```

`restart: unless-stopped` startet den Container nach Abstürzen und Server-Neustarts automatisch.
Die Anmeldungen (`auth …`) vorher lokal am PC machen, weil dafür ein Browser nötig ist.
Das Docker-Image enthält keinen Browser. Für TikTok im Container also `tiktok.method: api` nutzen
oder die Maschine auf deinem PC laufen lassen (Windows-Skript unten).

### Linux (systemd)

Pfade und Benutzer in `clipmaschine/deploy/clipmaschine.service` anpassen, dann:

```bash
sudo cp clipmaschine/deploy/clipmaschine.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now clipmaschine
journalctl -u clipmaschine -f
```

### Windows

`clipmaschine\deploy\start_windows.bat` per Doppelklick starten. Das Skript startet die Maschine nach einem
Absturz automatisch neu. Für den Autostart mit Windows: `Win+R` → `shell:startup` → eine Verknüpfung zur `.bat` hineinlegen.

---

## ⚙️ Wichtige Einstellungen (`config.yaml`)

| Option | Standard | Bedeutung |
|---|---|---|
| `discovery.min_views` | 50 | Mindest-Views für Top-Clips |
| `discovery.lookback_hours` | 24 | Wie weit zurück gesucht wird |
| `discovery.gravity` | 1.3 | Höher bevorzugt frische Clips stärker |
| `live.enabled` | false | Chat-Hype-Clips (braucht `auth twitch`) |
| `live.spike_factor` | 3.0 | Um wie viel aktiver der Chat sein muss |
| `editing.layout` | blur | `blur`, `crop` oder `split` |
| `editing.max_duration` | 59 | Maximale Videolänge in Sekunden |
| `metadata.language` | de | Sprache der Texte |
| `posting.max_posts_per_day` | 4 | Pro Plattform, in rollierenden 24 Stunden |
| `posting.min_minutes_between_posts` | 120 | Mindestabstand pro Plattform |
| `posting.active_hours` | [10, 23] | Posting-Fenster in Ortszeit |
| `posting.queue_size` | 4 | Fertige Videos auf Vorrat |

Alle Optionen sind in `config.example.yaml` kommentiert. Tippfehler in Optionsnamen werden beim Start gemeldet.

---

## 🧪 Tests

```bash
python -m pytest clipmaschine/tests -q
```

Die Tests decken Hype-Erkennung, Bewertung und Duplikate, Datenbank, Zeitplan (Limits, Uhrzeiten),
Untertitel, alle drei Layouts mit echtem ffmpeg, die Uploader-Protokolle (mit simulierten APIs),
KI-Titel samt Fallbacks, Selbstheilung, Einzelinstanz-Sperre und die Kommandozeile ab.

---

## ❓ Häufige Probleme

| Problem | Lösung |
|---|---|
| `ffmpeg nicht gefunden` | ffmpeg installieren und Terminal neu öffnen (PATH) |
| Twitch: „Kanal nicht gefunden“ | Login-Namen in `streamers` prüfen (klein, ohne `twitch.tv/`) |
| TikTok: `unaudited_client_…` | App noch nicht geprüft: `privacy_level: SELF_ONLY` mit privatem Account, `mode: inbox`, oder den Browser-Modus nutzen |
| TikTok (Browser): „Kein Browser startbar“ | Google Chrome installieren oder `python -m playwright install chromium` |
| TikTok (Browser): Post hängt | Screenshot in `clipmaschine_data/logs/tiktok_screenshots/` ansehen. Hat TikTok die Seite geändert, `tiktok.browser_selectors` anpassen |
| YouTube-Login muss jede Woche neu | OAuth-App in der Google Cloud Console auf „In Produktion“ stellen |
| Videos auf YouTube sind privat | Ungeprüftes API-Projekt. YouTube API Audit beantragen |
| Schrift sieht anders aus | Eigene Schrift: `editing.font` plus `editing.fonts_dir` mit der .ttf-Datei |
| Keine Clips gefunden | `min_views` senken, `lookback_hours` erhöhen, `discover` zum Testen nutzen |
| „Die Clipmaschine läuft bereits“ | Es läuft schon eine Instanz. `status` zeigt die PID |
