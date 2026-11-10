# 🎮 Warzone-Streaming: Starter-Kit für Twitch + YouTube

Alles zum Kopieren und Anpassen. Ersetze überall `DEINNAME` durch deinen Kanalnamen.

> **Ehrlich vorweg:** Keine Vorlage macht einen Stream viral. Warzone ist eine der vollsten Kategorien
> auf Twitch, neue Kanäle stehen dort ganz unten in der Liste und werden kaum gefunden. Zuschauer kommen
> am Anfang fast nie *über* Twitch, sondern über Kurzvideos (TikTok, Shorts, Reels), die auf deinen Stream
> zeigen. Der Plan unten ist darauf ausgelegt.

---

## 1. Vor dem ersten Stream (einmalig)

- [ ] **YouTube-Livestreaming freischalten:** youtube.com → Erstellen → Livestream starten. Dafür musst du den
      Kanal per Telefonnummer bestätigen, danach dauert die Freischaltung **bis zu 24 Stunden**. Mach das zuerst.
- [ ] **Twitch:** Konto erstellen, E-Mail bestätigen, Zwei-Faktor-Anmeldung einschalten (Einstellungen → Sicherheit).
- [ ] Überall **derselbe Name** (Twitch, YouTube, TikTok, Instagram, Discord). Kurz, aussprechbar, ohne Zahlenkette.
- [ ] Profilbild + Banner (Canva hat kostenlose Vorlagen für „Twitch Banner“ und „YouTube Banner“).
- [ ] **OBS Studio** installieren (obsproject.com, kostenlos).
- [ ] **StreamElements**-Konto (kostenlos): Alerts, Chatbot und Overlays an einem Ort.
- [ ] **Discord-Server** anlegen (Kanäle: #ankündigungen, #clips, #allgemein, #squad-suche).
- [ ] Upload-Geschwindigkeit messen (speedtest.net). Für beide Plattformen gleichzeitig in 1080p brauchst du
      etwa **20 Mbit/s Upload**. Mit weniger nimm die 720p-Einstellungen unten.

## 2. Technik

### Auf welchem Gerät spielst du?

| Gerät | So streamst du |
|---|---|
| **Windows-PC** | OBS direkt auf dem PC. Beste Lösung, alles unten gilt für dich. |
| **PS5 / Xbox** | Die Konsole kann selbst streamen, aber nur auf **eine** Plattform und ohne Overlays. Für Twitch + YouTube gleichzeitig brauchst du eine Capture Card (z. B. Elgato HD60 X) und OBS auf PC oder MacBook. |
| **MacBook** | Warzone läuft nicht auf macOS. Das MacBook taugt aber als Streaming-Rechner mit Capture Card. |

### OBS-Einstellungen (Einstellungen → Ausgabe → Ausgabemodus „Erweitert“)

| Einstellung | Guter Upload (ab 20 Mbit/s) | Schwacher Upload (ab 10 Mbit/s) |
|---|---|---|
| Auflösung (Video → Ausgabe) | 1920×1080 | 1280×720 |
| FPS | 60 | 60 |
| Encoder | NVIDIA NVENC H.264 (bzw. AMD/Intel-Hardware-Encoder) | gleich |
| Bitrate | 6000 kbit/s | 4500 kbit/s |
| Keyframe-Intervall | 2 s | 2 s |
| Voreinstellung | „Qualität“ / P5 | „Qualität“ / P5 |
| Audio | 160 kbit/s, 48 kHz | 160 kbit/s |

Ruckelt das Spiel beim Streamen: in Warzone die FPS begrenzen (z. B. auf 120) und OBS **als Administrator** starten.

### Gleichzeitig auf Twitch und YouTube

- **Empfohlen:** OBS-Plugin **„Multiple RTMP Outputs“** (obs-multi-rtmp, kostenlos). Twitch läuft wie gewohnt über
  „Stream starten“, YouTube trägst du im Plugin als zweites Ziel ein (Server und Schlüssel aus YouTube Studio → Livestream).
- **Alternative:** Restream.io. Einfacher einzurichten, die Gratis-Version hat aber Einschränkungen.
- **Twitch-Regeln fürs Simulcasting** (bitte einmal in den aktuellen Twitch-Richtlinien gegenlesen):
  die Qualität auf Twitch darf nicht schlechter sein als woanders, im Twitch-Stream keine Hinweise oder Links
  auf den parallelen YouTube-Stream, und fremde Chats nicht ins Twitch-Bild einblenden.

### Szenen in OBS

1. **Gleich geht's los** (Standbild/Animation + Musik + Countdown, 3–5 Minuten)
2. **Gameplay** (Spielaufnahme, Webcam klein in einer Ecke, die das HUD nicht verdeckt, Alerts)
3. **Pause** („Bin gleich zurück“)
4. **Ende** (Danke + nächster Streamtermin + Socials)

### Ton (wichtiger als Bild)

- Mikrofon-Filter in OBS: **Rauschunterdrückung → Kompressor → Limiter** (in dieser Reihenfolge).
- Deine Stimme muss klar über dem Spiel liegen: Spielton etwa 15–20 dB leiser als das Mikro.
- Keine Spotify-/Radio-Musik im Stream (Urheberrecht, Stummschaltungen, Strikes). Nimm freie Musik, z. B. StreamBeats.

## 3. Titel-Vorlagen

Ein guter Titel sagt in einem Satz, **warum man genau jetzt reinschauen soll**: ein Ziel, eine Challenge, eine Frage.
Nichts versprechen, was nicht stimmt, das kostet Vertrauen.

### Twitch (max. 140 Zeichen)

```
🔴 Heute fällt der erste Win – oder ich lösche das Spiel | Warzone deutsch
```
```
Solo gegen Squads bis zum Sieg 🏆 Tag 3 | !loadout !discord
```
```
Win-Streak Versuch #7 – Rekord liegt bei 3 | Warzone [GER]
```
```
Ich teste EURE Loadouts – schlechtestes fliegt raus | !loadout
```
```
Ranked Grind bis Platin 💎 Stand: Gold 2 | Warzone deutsch
```
```
Zuschauer-Lobbys ab 20 Uhr 🎮 Wer killt mich zuerst? | !mitspielen
```
```
0 auf 50 Follower – wir bauen das hier zusammen auf | Warzone
```

**Kategorie:** Call of Duty: Warzone
**Tags:** `Deutsch`, `Warzone`, `FPS`, `BattleRoyale`, `Ranked`, `Controller` oder `MausUndTastatur`, `Anfängerfreundlich`

### YouTube (max. 100 Zeichen, so schreiben, wie Leute suchen)

```
🔴 WARZONE LIVE DEUTSCH – Ranked Grind bis Platin | Tag 3
```
```
🔴 WARZONE LIVE: Solo vs. Squads bis zum Win | Deutsch
```
```
🔴 Beste Loadouts im Test – Warzone Live Deutsch
```
```
🔴 Warzone mit Zuschauern – Custom Lobbys LIVE | Deutsch
```

### Live-Benachrichtigung auf Twitch (max. 140 Zeichen)

```
Bin live! Heute: Solo vs. Squads bis zum Win 🏆 Komm rein und sag Hallo
```

## 4. Beschreibungen

### Twitch-Bio (max. 300 Zeichen)

```
Warzone auf Deutsch 🎮 Ranked, Challenges und Zuschauer-Lobbys.
Live: Mo · Mi · Fr · So ab 19 Uhr.
Hier wird gelacht, auch wenn ich im Gulag verliere. Sag Hallo im Chat!
```

### Twitch-Panels (unter dem Stream)

**Über mich**
```
Ich bin DEINNAME, [Alter] aus [Stadt]. Ich spiele seit [Jahr] Call of Duty und streame Warzone:
Ranked, Challenges und Runden mit euch. Neu hier? Schreib einfach „Hi“, ich antworte jedem.
```

**Streamplan**
```
Montag     19–22 Uhr  Ranked Grind
Mittwoch   19–22 Uhr  Challenge-Abend
Freitag    20–23 Uhr  Zuschauer-Lobbys
Sonntag    17–20 Uhr  Entspannte Runden + Loadout-Tests
```

**Regeln**
```
1. Respektvoll bleiben, keine Beleidigungen, kein Rassismus oder Sexismus.
2. Keine Eigenwerbung und keine Links ohne Erlaubnis.
3. Kein Backseat-Gaming, außer ich frage danach.
4. Keine Spoiler und kein Stream-Sniping.
5. Den Mods wird nicht widersprochen.
```

**Setup**
```
Plattform: [PC / PS5 / Xbox] · Eingabe: [Controller / Maus + Tastatur]
Headset: … · Mikro: … · Monitor: …
Aktuelles Loadout: !loadout im Chat
```

### YouTube-Stream-Beschreibung

```
🔴 Warzone LIVE auf Deutsch – heute: [ZIEL DES STREAMS]

Schreib in den Chat, ich lese alles! Wenn dir der Stream gefällt, lass ein Like und ein Abo da,
das hilft dem Kanal am Anfang enorm.

📅 Live: Mo · Mi · Fr · So ab 19 Uhr
💬 Discord: [LINK]
🎬 TikTok: [LINK] · 📸 Instagram: [LINK]

🎮 Setup: [PC/Konsole] · [Controller/Maus + Tastatur]
🔫 Aktuelles Loadout: [WAFFE + AUFSÄTZE]

#Warzone #WarzoneDeutsch #CallOfDuty
```

### YouTube-Kanalbeschreibung

```
Warzone auf Deutsch: Livestreams, die besten Runden als Shorts und ehrliche Loadout-Tests.
Live: Mo · Mi · Fr · So ab 19 Uhr. Abonniere und aktiviere die Glocke, um keinen Stream zu verpassen.
Kontakt: [E-MAIL]
```

## 5. Chat-Befehle und Bot (StreamElements)

| Befehl | Antwort |
|---|---|
| `!discord` | Komm in die Community: [LINK] |
| `!loadout` | Aktuell: [WAFFE] mit [AUFSÄTZEN] · Zweitwaffe: [WAFFE] |
| `!setup` | [PC/Konsole] · [Eingabegerät] · Details im Panel unter dem Stream |
| `!plan` | Live: Mo · Mi · Fr · So ab 19 Uhr |
| `!mitspielen` | Freitags sind Zuschauer-Lobbys! Code kommt im Discord: [LINK] |
| `!socials` | TikTok: [LINK] · Instagram: [LINK] · YouTube: [LINK] |

**Timer (alle 20 Minuten abwechselnd):**
```
Gefällt dir der Stream? Ein Follow kostet nichts und hilft mir riesig 💜
```
```
Die besten Momente landen als Clips auf TikTok und YouTube Shorts: [LINK]
```

## 6. Ablauf deines ersten Streams

1. **30 Minuten vorher:** Story/Post auf TikTok, Instagram und Discord („Heute 19 Uhr live“).
2. **Vorher testen:** 5 Minuten aufnehmen und ansehen (Ton, Bild, Webcam, Spiel-Lautstärke).
3. **Start:** Szene „Gleich geht's los“ für 3–5 Minuten, dann begrüßen und das Ziel des Abends sagen.
4. **Durchgehend reden**, auch bei 0 Zuschauern: Was planst du, warum landest du dort, was ist schiefgelaufen.
   Wer reinklickt, entscheidet in etwa 10 Sekunden, ob er bleibt. Ein stiller Stream verliert ihn.
5. **Jeden Chatter mit Namen begrüßen** und eine Frage stellen.
6. **2–3 Stunden** reichen. Lieber kurz und gut gelaunt als sechs Stunden müde.
7. **Ende:** bedanken, nächsten Termin nennen, auf einen anderen kleinen Streamer **raiden**.
8. **Danach:** 2–3 beste Momente als Clips markieren und als Kurzvideos posten.

## 7. So bekommst du Zuschauer

**1. Kurzvideos sind dein Hauptkanal.** Poste jeden Tag 1–3 Clips aus deinen Streams auf TikTok, YouTube Shorts
und Instagram Reels, mit „live auf Twitch: DEINNAME“ im Video. Das ist der einzige Weg, auf dem neue Kanäle
verlässlich gefunden werden.

**2. Hab ein Format, nicht nur „ich spiele Warzone“.** Beispiele: Win-Streak mit Zähler, „Solo vs. Squads bis zum
Sieg“, Loadout-Tests nach Zuschauer-Vorschlag, „0 auf Platin“-Serie mit Tageszähler, Zuschauer-Lobbys.
Eine Serie gibt Leuten einen Grund wiederzukommen.

**3. Feste Zeiten.** 3–4 feste Tage pro Woche zur selben Uhrzeit schlagen sieben unregelmäßige Streams.

**4. Rede und reagiere.** Bei kleinen Kanälen bleiben Leute wegen des Streamers, nicht wegen des Gameplays.

**5. Vernetz dich.** Spiel mit anderen kleinen Streamern, raide am Ende, sei in 2–3 Communities wirklich aktiv
(nicht nur zum Werben). Mach Zuschauer-Lobbys, Mitspieler bringen Freunde mit.

**6. Nutze YouTube richtig.** Dort wird gesucht: Titel mit „Warzone Live Deutsch“, ein Thumbnail pro Stream, und
aus guten Streams später ein geschnittenes Video (z. B. „Mein bester Win diese Woche“).

**7. Realistische Ziele.** Erstes Etappenziel ist Twitch-Affiliate: 50 Follower, 500 gestreamte Minuten,
7 verschiedene Streamtage und im Schnitt 3 Zuschauer innerhalb von 30 Tagen. Mit Kurzvideos und festen Zeiten
ist das in 1–3 Monaten machbar. „Viral“ passiert, wenn überhaupt, über einen einzelnen Clip.

**Finger weg von:** gekauften Zuschauern oder Followern (Twitch erkennt das und sperrt), Follow-für-Follow,
Eigenwerbung in fremden Chats und reißerischen Titeln, die nicht stimmen.

## 8. Deine Clipmaschine für den eigenen Kanal

Die Clipmaschine aus diesem Repository kann die Kurzvideos aus **deinen eigenen** Streams automatisch
schneiden und posten. Bei eigenen Inhalten gibt es kein Rechteproblem, und die Plattformen werten sie als Original.

In der `config.yaml`:

```yaml
streamers:
  - login: DEINNAME          # dein eigener Twitch-Kanal
    hashtags: [warzone, warzonedeutsch]

discovery:
  min_views: 1               # am Anfang haben deine Clips wenige Aufrufe
  lookback_hours: 48

metadata:
  default_hashtags: [warzone, callofduty, warzoneclips, gaming, fyp]
```

Markiere im Stream gute Momente als Twitch-Clip (oder lass Zuschauer clippen), den Rest erledigt die Maschine.
Mit `captions.enabled: true` bekommen die Videos zusätzlich Untertitel.
