# NostalgiaForInfinity — Validation Report

**Frage:** Hat NFI einen echten Edge, oder ist es gut aussehendes Curve Fitting?
**Analyse-Datum:** 2026-07-24 · **NFI-Version live:** X7 · **Strategie unter Test:** fremd, unverändert (kein Hyperopt, kein Tuning).

> **Lesehilfe zu den Zahlen.** Dieser Report trennt strikt drei Sorten Zahlen:
> - 🟩 **CLEAN / gemessen** — direkt aus Fakten abgeleitet (hier: Git-History). Belastbar.
> - 🟥 **CONTAMINATED / In-Sample** — Backtests der *aktuellen* Version über Zeiträume, auf die sie nachgetunt wurde. Obergrenze, keine Erwartung.
> - ⬜ **PENDING DATA** — Methode steht, Skript ist geschrieben und getestet, aber die Zahl braucht Marktdaten, die in dieser Umgebung nicht beschaffbar sind (siehe §0). **Nichts davon ist hier erfunden.**

---

## 0. Warum in diesem Report Backtest-Zahlen fehlen (und keine erfunden werden)

Die Validierung lief in einer gemanagten Sandbox (Claude Code on the web). Deren Egress-Policy **sperrt alle Krypto-Datenquellen** — direkt gemessen, nicht vermutet:

| Host | Ergebnis |
|---|---|
| `api.binance.com`, `data.binance.vision` | `403 policy denial` |
| Kraken, Coinbase, Bybit, OKX, KuCoin, Gate, MEXC, Binance.US | `403` / CONNECT tunnel failed |
| HuggingFace (`hf.co` + alle CDN-Hosts), `registry.opendata.aws` | blockiert |
| CoinGecko / CoinCap / CryptoCompare / CryptoDataDownload | blockiert |
| **erlaubt:** GitHub (clone/raw), Google Cloud Storage, AWS S3, PyPI | erreichbar |

Zusätzlich: **kein Docker-Daemon** in der Sandbox (`docker.sock` fehlt) — der vom Task gewünschte `docker-compose`-Weg ist hier nicht lauffähig (auf einer normalen Maschine schon).

**Konsequenz:** Ohne 5m/15m/1h-Klines für die 160-Pair-NFI-Liste 2023–2026 sind Phase 2, 4, 5, 6 und die *Ausführung* von Phase 3 hier nicht durchführbar. Was **ohne** Marktdaten geht — und zufällig der schärfste Read auf die Curve-Fitting-Frage ist — ist die **strukturelle Analyse aus der Git-History** (Phase 3, unten 🟩). Binances eigenen Content über eine Seitentür-URL zu ziehen wäre eine Umgehung der Sicherheitskontrolle und wurde bewusst nicht getan.

Wie du die Zahlen fließen lässt: siehe `README.md` → „Unblock". Die Pipeline (`scripts/`) ist geschrieben, gepinnt und in den datenunabhängigen Teilen unit-getestet; sie produziert die ⬜-Zellen, sobald Daten vorliegen.

---

## 1. Phase 1 — Setup

| Punkt | Status |
|---|---|
| Freqtrade | Docker-Image gepinnt (`freqtradeorg/freqtrade:2026.6`, `docker-compose.yml`); pip-Fallback `freqtrade==2026.6` |
| NFI geklont | ✅ `iterativv/NostalgiaForInfinity`, volle History (25.927 Commits) |
| Pflicht-Config | ✅ `timeframe=5m`, `use_exit_signal=true`, `exit_profit_only=false`, `ignore_roi_if_entry_signal=true` — gesetzt und **nicht** überschrieben (`config/config.json`) |
| Exchange / Quote | Binance Spot / USDT |
| Pairlist | NFI-eigene **statische** Backtest-Liste, **160 USDT-Paare** (`config/pairlist-static-binance-spot-usdt.json`) |
| Datenspezifikation | 5m + 15m + 1h, ab 2023-01-01; geschätztes Volumen **~2,5–3,5 GB** |

**Datenumfang / Paarzahl / fehlende Kerzen:** ⬜ PENDING DATA — `scripts/02_download_data.sh` lädt und `scripts/02b_report_gaps.py` meldet Größe (GB), Paarzahl und fehlende 5m-Kerzen pro Paar. Kann in der Sandbox nicht laufen (Binance gesperrt).

---

## 2. Phase 2 — Naiver Baseline-Backtest 🟥 CONTAMINATED

Standard-Backtest der **aktuellen** NFI (HEAD/X7) über 2023→heute, statische Pairlist.
Skript: `scripts/03_baseline_backtest.sh` → `scripts/summarize_trades.py --contaminated`.

| Metrik | Wert |
|---|---|
| CAGR, Max Drawdown, Sortino, Calmar, SQN, Profit Factor, #Trades, ⌀ Haltedauer | ⬜ PENDING DATA |

**Warum das Ergebnis kontaminiert ist — quantifiziert (🟩, s. §3.1):** X7 existiert erst seit **2025-10-21**. Ein Backtest über 2023–2025 testet also eine Strategie auf Daten, die ihre Autoren beim Schreiben *kannten* und gegen die sie **~15 Commits/Tag** nachjustiert haben. Diese Zahl ist die **Obergrenze** dessen, was NFI je zeigen wird — keine Erwartung. Sie gehört in den Report nur als Kontrast zu Phase 3.

---

## 3. Phase 3 — Time-Frozen Walk-Forward (der eigentliche Test)

### 3.1 Struktureller Beweis aus der Git-History 🟩 CLEAN (gemessen, keine Marktdaten nötig)

Reproduzierbar: `scripts/01_analyze_git_history.py` → `outputs/git_history.json`, `outputs/complexity_growth.png`.

**NFI wird permanent gegen den jüngsten Markt nachgezogen — das ist messbar:**

- **25.927 Commits** im Repo (seit 2021-10). Commits pro Jahr, *steigend*:
  2021 : 2.102 · 2022 : 3.764 · 2023 : 4.033 · 2024 : 4.430 · 2025 : 5.363 · 2026 : 6.235 (bis 24. Juli).
- **7 Major-Versionen in ~4 Jahren** → im Schnitt **ein kompletter Rewrite alle ~7 Monate**.

**Versions-Lineage (jede Zahl ist ein Git-Fakt):**

| Version | Geboren | Zuletzt berührt | Commits | Zeilen | getunte Float-Konstanten |
|---|---|---|---:|---:|---:|
| X  | 2021-10-06 | 2026-05-13 | 3.591 | 38.977 | 22.614 |
| X2 | 2022-12-13 | 2026-05-13 | 1.027 | 14.444 | 6.475 |
| X3 | 2023-05-30 | 2026-05-13 | 2.390 | 46.718 | 20.716 |
| X4 | 2023-08-16 | 2026-05-13 | 2.094 | 46.716 | 20.716 |
| X5 | 2024-09-14 | 2026-05-13 | 1.319 | 58.249 | 30.301 |
| X6 | 2025-03-01 | 2026-05-31 | 2.612 | 69.664 | 35.678 |
| **X7** | **2025-10-21** | **2026-07-24** | **4.253** | **75.111** | 14.347 |

**Was das heißt:**
1. **Die heute empfohlene Version (X7) ist ~9 Monate alt** und wurde in dieser Zeit von **4.253 Commits** angefasst (⌀ ~15/Tag an *einer* Datei). Es gibt praktisch **keine** Periode, die X7 *nicht* kannte — außer den ~9 Monaten seit Geburt.
2. **Komplexität wächst monoton** (saubere Linie: Codezeilen): von **14.444** Zeilen (X2, aktiv Anfang 2023) auf **75.111** Zeilen (X7) → **~5,2×**. Ab X3 durchgängig steigend (46,7k → 75,1k).
3. **Zehntausende hand-gesetzte Konstanten** pro Version (14k–36k Float-Literale). *Hinweis zur Ehrlichkeit:* Diese Zahl ist **nicht** monoton — X7 (14.347) hat weniger als X6 (35.678), vermutlich weil X7 Konstanten in Hilfsstrukturen refaktoriert hat. Als reines Komplexitätsmaß taugen daher die **Codezeilen**; die Konstanten-Zahl belegt nur die Größenordnung (》10.000 Tuning-Parameter).

> **Zwischenfazit (belastbar, ohne einen einzigen Backtest):** Das ist der Fingerabdruck einer Strategie, die *by design* in-sample glänzt. Ein normaler Backtest über 2023–2026 misst überwiegend, wie gut das Team die Vergangenheit nachgezogen hat — nicht, ob morgen Geld verdient wird. Genau deshalb ist der Time-Frozen-Test unten der einzig faire.

**Grafik:** `outputs/complexity_growth.png` (Zeilen + Konstanten je Version; Geburtstermine als Timeline).

### 3.2 Time-Frozen Backtest — Methode & Ausführung

Skript: `scripts/04_walk_forward.py`. Für jeden Quartals-Checkpoint D von 2023-01 bis heute:

1. Commit finden, der an D HEAD war (`git rev-list -1 --before=D`).
2. `git worktree add <sha>` → Strategie **exakt wie an Tag D**.
3. Version wählen, die *damals* aktuell war (neueste vorhandene `NostalgiaForInfinityX*.py` — Mapping unten).
4. Backtest **ausschließlich** auf `[D, D+90 Tage]`. Assertion im Code bricht ab, falls auch nur **ein** Trade vor D öffnet.

**Welche Version pro Fenster eingefroren wird 🟩 (gemessen):**

| Fenster-Start | Aktive Version | Alter der Version an D (Tage) |
|---|---|---:|
| 2023-01-01 | X2 | 19 |
| 2023-04-01 | X2 | 109 |
| 2023-07-01 | X3 | 32 |
| 2023-10-01 | X4 | 46 |
| 2024-01-01 | X4 | 138 |
| 2024-04-01 | X4 | 229 |
| 2024-07-01 | X4 | 320 |
| 2024-10-01 | X5 | 17 |
| 2025-01-01 | X5 | 109 |
| 2025-04-01 | X6 | 31 |
| 2025-07-01 | X6 | 122 |
| 2025-10-01 | X6 | 214 |
| 2026-01-01 | X7 | 72 |
| 2026-04-01 | X7 | 162 |
| 2026-07-01 | X7 | 253 |

*Methodischer Hinweis:* Die „Strategie" ist ein bewegliches Ziel über mehrere Dateien — man kann **nicht** einfach eine Datei bis 2023 zurück-`git log`en. Das obige Mapping löst das. „Alter an D" zeigt zudem, wie frisch der Freeze war: kleine Werte (z. B. 2023-07 X3, 32 Tage) sind der sauberste OOS-Read; große (2024-07 X4, 320 Tage) enthalten mehr bereits eingebautes Tuning — bleiben aber gültig, weil das 90-Tage-Fenster strikt *nach* D liegt.

**Aggregiertes Ergebnis (wie performt jede eingefrorene Version auf Daten, die ihre Autoren nicht kannten?):** ⬜ PENDING DATA — CAGR/PF/Sortino/MaxDD je Fenster, plus aggregierter sauberer Trade-Satz für Phase 5/6. **Grafik:** `outputs/equity_curves.png` — jedes Fenster **einzeln** geplottet (nicht aneinandergehängt), wie gefordert.

### 3.3 Survivorship Bias in der Pairlist

`scripts/lib/pit_pairlist.py` rekonstruiert pro Fenster eine `StaticPairList` aus dem Volumen-Ranking **zum Commit-Zeitpunkt** (Lookback endet an D, nur Paare mit Historie vor D). `estimate_survivorship_bias()` misst pro Fenster, wie viele Paare an D noch gar nicht gelistet waren.

**Ehrliche Grenze (nicht still ignoriert):** Die NFI-Static-Liste wurde ~2024+ geschrieben. Paare, die 2023 handelbar waren und **vor** ~2024 starben, fehlen im Universum komplett — kein PIT-Ranking holt sie zurück. Der wahre Survivorship-Bias ist daher **≥** dem messbaren Teil. Richtung: **nach oben** (der Baseline-Backtest sieht zu gut aus, weil die Toten fehlen). Voll sauber wäre nur mit einem echten Point-in-Time-Listing aller je gelisteten Binance-USDT-Paare — das braucht die gesperrten Daten. Quantifizierung: ⬜ PENDING DATA (`outputs/walk_forward.json` → Feld `survivorship`).

---

## 4. Phase 4 — Kostensensitivität ⬜ PENDING DATA (Methode fix)

`scripts/05_costs.py` re-kostet die **sauberen** Phase-3-Trades über das Gitter
Fees {0,10 % / 0,075 % / 0,05 % pro Seite} × Slippage {0/5/15/30 bps pro Fill}, plus
ein Szenario mit erhöhter Slippage für Paare außerhalb der Top-20 nach Volumen.

**Re-KostING-Modell (Annahmen offengelegt):** gross ≈ net + fills·applied_fee; net' = gross − fills·(fee+slip); `fills=2` als Default. **NFI nutzt Position-Adjustment (DCA/„grinding") → reale Fills ≥ 2 → die hier berechnete Break-Even-Schwelle ist eine *optimistische Obergrenze*.** Gold-Standard (freqtrade je Fee neu laufen lassen) ist im README notiert.

**Break-Even-Kostenschwelle pro Roundtrip:** ⬜ PENDING DATA. Entscheidungsregel steht: liegt sie **< ~40 bps**, ist die Strategie für Retail-Ausführung tot — das Skript sagt es wörtlich (`verdict`-Feld). *Prior aus §3.1:* NFI macht viele kleine Scalp/Grind-Trades → geringe Brutto-Marge pro Trade → hohe Kostensensitivität ist zu erwarten.

---

## 5. Phase 5 — Robustheit ⬜ PENDING DATA (Code getestet)

`scripts/06_robustness.py` auf den aggregierten sauberen Trades; Kernfunktionen unit-getestet (`test_robustness.py`, 4/4 ✅), Seed gepinnt (`20260724`). Erzeugt `outputs/montecarlo.png`.

- **Monte Carlo (n=10.000):** Trade-Reihenfolge shufflen → 5. Perzentil Endkapital, 95. Perzentil Max Drawdown.
- **Bootstrap (n=10.000):** Trades mit Zurücklegen → 95%-KI für Profit Factor; **enthält es 1.0?** (Feld `ci_contains_1.0`).
- **Regime-Split:** BTC Bull/Bear/Seitwärts (50/200-SMA + 30d-Momentum) → kommt der ganze Gewinn aus *einem* Regime?
- **Trade-Konzentration:** Kurve nach Entfernen der besten 5 % Trades — bleibt Profit übrig?

⬜ Zahlen: PENDING DATA.

---

## 6. Phase 6 — Benchmark ⬜ PENDING DATA (Methode fix)

`scripts/07_benchmark.py` je Fenster: NFI vs **Buy & Hold BTC** und vs **gleichgewichtetes Körbchen** der gehandelten Paare — **risikoadjustiert (Sortino)**, nicht nominal.
Entscheidungsregel steht: schlägt NFI nach Kosten das B&H-BTC-Sortino in ≤ der Hälfte der Fenster, **zahlt sich die Komplexität nicht aus** (`verdict`-Feld). ⬜ Zahlen: PENDING DATA.

---

## 7. Würde ich eigenes Geld darauf setzen — und warum nicht?

**Auf Basis dessen, was heute *belastbar* ist: Nein.**

Ich habe keinen sauberen Out-of-Sample-Return (Phase 3-Ausführung ist datengesperrt), also ist das ein Urteil über die **Beweislast**, nicht über eine gemessene Rendite — und die Beweislast steht schlecht. Die Git-History (das Einzige, was hier 100 % sauber messbar war) zeigt genau das Profil, das man bei Curve Fitting erwartet: **75.000 Zeilen, zehntausende hand-gesetzte Schwellen, alle ~7 Monate ein Rewrite, ~15 Commits/Tag, und die aktuelle Version ist erst ~9 Monate alt.** Eine Strategie mit so vielen freien Parametern, kontinuierlich gegen den jüngsten Markt nachgezogen, *wird* jeden historischen Backtest glänzend bestehen — das ist keine Evidenz für Edge, sondern der Mechanismus, der Edge *vortäuscht*. Dazu kommt die strukturelle Kostenanfälligkeit (viele kleine DCA-Trades) und der nach oben gerichtete Survivorship-Bias in jeder naiven Pairlist.

Was mich umstimmen würde — konkret und messbar, kein Weichzeichner:
1. **Phase 3 sauber:** die eingefrorenen Versionen liefern über die 15 Vorwärts-Fenster im Mittel **PF > 1 nach realistischen Kosten** — und das Bootstrap-95%-KI für PF liegt **komplett über 1.0**.
2. **Phase 4:** Break-Even-Roundtrip-Kosten **deutlich über 40 bps**.
3. **Phase 6:** besseres **Sortino als Buy & Hold BTC** in der Mehrheit der Fenster.
4. **Phase 5:** Gewinn **nicht** aus einem einzigen BTC-Regime und **nicht** aus den besten 5 % Trades.

Treffen diese vier zu, revidiere ich das Urteil. Bis dahin gilt: Der spektakuläre Baseline-Backtest, den man überall sieht, ist die **kontaminierte Obergrenze** — und die einzige unkontaminierte Zahl, die ich messen konnte, deutet auf Fit, nicht auf Edge. Kein eigenes Geld.

---

### Reproduzierbarkeit
Gepinnt: `freqtrade==2026.6` (Docker-Tag identisch), `pandas==3.0.5`, `numpy==2.4.6`, `matplotlib==3.11.1`, `scipy==1.17.1`. Strategie per **Git-SHA** eingefroren (nicht pip). Seeds gepinnt. Datenunabhängige Teile unit-getestet (`test_metrics.py` 6/6, `test_robustness.py` 4/4). Ablauf: `README.md`.
