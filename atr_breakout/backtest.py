"""
BTC / Gold ATR-Breakout – Python-Backtest von BTC_Gold_Breakout.pine

Bildet das Pine-Skript Kerze für Kerze auf 1h-Daten nach, inklusive der
TradingView-Brokeremulation, soweit sie für dieses Skript relevant ist:

- Signale nur auf abgeschlossenen 16h- (BTC) bzw. 4h-Blöcken (Gold), die aus
  1h-Kerzen mit fester Zeitverankerung ab 01.01.2020 + Phase gebildet werden.
- Market-Orders füllen zum Open der nächsten 1h-Kerze ± Slippage (Ticks).
- Initialstop relativ zum Fill (loss in Ticks), danach Trailing auf
  Blockschluss: max(Stop, Close - stopMult * ATR). Stop kann schon in der
  Einstiegsstunde ausgelöst werden; Gap unter den Stop füllt zum Open.
- Exit "Unter SMA" als Market-Order zum nächsten Open.
- Provision in % je Ausführung, Margin 2 % (Long) mit TradingView-Margin-Call:
  fällt das Equity am Kerzentief unter die Margin, wird das 4-fache der zur
  Deckung nötigen Menge zum Kerzentief liquidiert.
- Kontowährung CHF: Gewinne werden mit dem USD/CHF-Tageskurs des Vortags
  umgerechnet, die Positionsgrösse mit demselben Kurs in USD.

Optional (--funding, nicht im Pine-Skript): historische Binance-Funding-Raten
für BTC, belastet zum Open der Funding-Stunde, solange die Position offen ist.

Nicht nachgebildet (wie im Pine-Skript selbst): Wechselgebühren,
Markpreis-Liquidation der Börse, Portfolio-Rebalancing.
"""

import argparse
import math
import sys
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data as datasrc  # noqa: E402

HOUR_MS = 3_600_000
ANCHOR_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
MODEL_SLIP = 0.0002


@dataclass(frozen=True)
class Profile:
    name: str
    tf_hours: int
    phase_hours: int
    ma_length: int
    band_mult: float
    stop_mult: float
    risk: float
    lev_cap: float
    model_fee: float
    is_gold: bool


PROFILES = {
    "btc": Profile("BTC – Portfolio", 16, 6, 24, 3.0, 1.75, 0.60, 50.0, 0.0005, False),
    "btc-latest": Profile("BTC – jüngster Test", 16, 5, 20, 3.0, 1.0, 0.60, 50.0, 0.0005, False),
    "gold": Profile("Gold – Portfolio", 4, 0, 40, 1.0, 4.0, 0.40, 20.0, 0.00005, True),
}


@dataclass
class Settings:
    """strategy()-Eigenschaften und Inputs des Pine-Skripts."""
    start: pd.Timestamp
    end: pd.Timestamp
    initial_capital: float = 500.0
    commission_pct: float = 0.05
    slippage_ticks: int = 2
    margin_long_pct: float = 2.0
    mintick: float = 0.1
    qty_step: float = 0.001
    point_value: float = 1.0
    max_notional: float = 5_000_000.0
    risk: float | None = None          # None = originales Forschungsrisiko
    max_leverage: float | None = None  # None = originale Grenze levCap * 0.8
    margin_calls: bool = True


@dataclass
class Result:
    profile: Profile
    settings: Settings
    trades: pd.DataFrame
    equity: pd.Series                  # Equity CHF auf 1h-Schluss
    equity_low: pd.Series              # Equity CHF am Kerzentief (intrabar)
    signals: int = 0
    rejected: int = 0
    funding_chf: float | None = None
    stats: dict = field(default_factory=dict)


def run(bars, fx, prof, s, funding=None):
    t = bars.index.as_unit("ms").asi8
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    rate = fx  # CHF je USD, je Kerze
    n = len(t)

    period = prof.tf_hours * HOUR_MS
    anchor = ANCHOR_MS + prof.phase_hours * HOUR_MS
    risk = prof.risk if s.risk is None else s.risk
    pv = s.point_value
    mratio = s.margin_long_pct / 100
    comm = s.commission_pct / 100
    slip = s.slippage_ticks * s.mintick
    start_ms = int(s.start.value // 10**6)
    end_ms = int(s.end.value // 10**6)

    # Skriptzustand (var-Variablen)
    active_bucket = None
    b_high = b_low = b_close = math.nan
    clean = False
    prev_close = atr = ema = basis = math.nan
    samples = 0
    closes = deque(maxlen=prof.ma_length)
    stop_price = math.nan
    pending_distance = math.nan

    # Brokerzustand
    qty = 0.0
    avg = math.nan
    entry_comm = 0.0                   # USD, anteilig für Restposition
    entry_time = None
    net_chf = 0.0
    pend_entry = None                  # (qty, loss_ticks)
    pend_close = False
    exit_stop = math.nan
    trades = []
    prev_pos = 0.0
    prev_closed = 0
    signals = rejected = 0
    funding_chf = 0.0
    eq_close = np.empty(n)
    eq_low = np.empty(n)

    def close_part(q, price, i, reason):
        nonlocal qty, entry_comm, net_chf
        part_comm = entry_comm * (q / qty)
        exit_comm = q * price * pv * comm
        pnl_usd = (price - avg) * q * pv - part_comm - exit_comm
        pnl_chf = pnl_usd * rate[i]
        net_chf += pnl_chf
        trades.append({"entry_time": entry_time, "exit_time": bars.index[i], "qty": q,
                       "entry": avg, "exit": price, "pnl_usd": pnl_usd, "pnl_chf": pnl_chf,
                       "reason": reason, "equity_chf": s.initial_capital + net_chf})
        entry_comm -= part_comm
        qty = round(qty - q, 12)

    for i in range(n):
        r = rate[i]
        if funding is not None and qty > 0 and funding[i] != 0.0:
            cost = qty * o[i] * pv * funding[i] * r
            net_chf -= cost
            funding_chf += cost
        # ---------------- Brokeremulator: Ausführungen innerhalb der Kerze i
        if pend_close and qty > 0:
            close_part(qty, o[i] - slip, i, "Unter SMA")
        pend_close = False
        if pend_entry is not None and qty == 0:
            q, loss_ticks = pend_entry
            fill = o[i] + slip
            funds_usd = (s.initial_capital + net_chf) / r
            if q * fill * pv * mratio <= funds_usd:
                qty, avg, entry_time = q, fill, bars.index[i]
                entry_comm = q * fill * pv * comm
                exit_stop = fill - loss_ticks * s.mintick
            else:
                rejected += 1
        pend_entry = None

        if qty > 0:
            if o[i] <= exit_stop:
                close_part(qty, o[i] - slip, i, "SL")
            elif lo[i] <= exit_stop:
                close_part(qty, exit_stop - slip, i, "SL")

        # Margin Call am Kerzentief (nur wenn der Stop nicht ausgelöst hat)
        while s.margin_calls and qty > 0:
            p = lo[i]
            equity_usd = (s.initial_capital + net_chf) / r + (p - avg) * qty * pv - entry_comm
            avail = equity_usd - qty * p * pv * mratio
            if avail >= 0:
                break
            cover = math.ceil(-avail / (mratio * p * pv) / s.qty_step) * s.qty_step
            close_part(min(qty, 4 * cover), p, i, "Margin Call")

        if qty > 0:
            eq_low[i] = s.initial_capital + net_chf + ((lo[i] - avg) * qty * pv - entry_comm) * r
        else:
            eq_low[i] = s.initial_capital + net_chf

        # ---------------- Skript auf Kerzenschluss
        bucket = (t[i] - anchor) // period
        if active_bucket is None or bucket != active_bucket:
            active_bucket = bucket
            b_high, b_low, b_close = h[i], lo[i], c[i]
            clean = i != 0 or t[i] == anchor + bucket * period
        else:
            b_high = max(b_high, h[i])
            b_low = min(b_low, lo[i])
            b_close = c[i]
        time_close = t[i] + HOUR_MS
        completed = time_close == anchor + (bucket + 1) * period and clean
        buy = sell = False
        if completed:
            if math.isnan(prev_close):
                tr = b_high - b_low
            else:
                tr = max(b_high - b_low, abs(b_high - prev_close), abs(b_low - prev_close))
            atr = tr if math.isnan(atr) else atr + (tr - atr) / 14.0
            ema = b_close if math.isnan(ema) else ema + (b_close - ema) * (2.0 / 101.0)
            prev_close = b_close
            samples += 1
            closes.append(b_close)
            basis = sum(closes) / len(closes) if len(closes) == prof.ma_length else math.nan
            ready = samples >= (100 if prof.is_gold else max(14, prof.ma_length))
            buy = ready and b_close > basis + prof.band_mult * atr and (not prof.is_gold or b_close > ema)
            sell = ready and b_close < basis

        if qty > 0 and prev_pos == 0:
            stop_price = avg - pending_distance
        if qty == 0:
            stop_price = math.nan
        in_window = start_ms <= time_close < end_ms
        stopped_this_bar = len(trades) > prev_closed
        if qty > 0:
            if completed and not math.isnan(atr):
                stop_price = max(stop_price, c[i] - prof.stop_mult * atr)
            exit_stop = stop_price
            if completed and sell:
                pend_close = True
            if time_close >= end_ms:
                close_part(qty, c[i] - slip, i, "Testende")
                pend_close = False

        equity_chf = s.initial_capital + net_chf + (((c[i] - avg) * qty * pv - entry_comm) * r if qty > 0 else 0.0)
        if completed and buy and in_window and qty == 0 and not stopped_this_bar and equity_chf > 0:
            signals += 1
            eq_q = equity_chf / r
            distance = prof.stop_mult * atr
            est_entry = c[i] * (1 + MODEL_SLIP)
            per_unit = (distance + 2 * c[i] * MODEL_SLIP + prof.model_fee * (est_entry + c[i])) * pv
            lev = prof.lev_cap * 0.8 if s.max_leverage is None else s.max_leverage
            q = min(eq_q * risk / per_unit, eq_q * lev / (est_entry * pv))
            q = min(q, s.max_notional / (est_entry * pv))
            value = q * est_entry * pv
            if prof.is_gold:
                maint = 0.05
            else:
                maint = (0.01 if value <= 50_000 else 0.02 if value <= 250_000 else
                         0.03 if value <= 1_000_000 else 0.05 if value <= 5_000_000 else 0.10)
            q = min(q, eq_q / (2 * maint * est_entry * pv))
            q = math.floor(q / s.qty_step) * s.qty_step
            sufficient = q >= s.qty_step and (prof.is_gold or q * est_entry * pv >= 100)
            if sufficient and distance < c[i]:
                loss_ticks = math.ceil(distance / s.mintick)
                pending_distance = loss_ticks * s.mintick
                pend_entry = (q, loss_ticks)

        eq_close[i] = equity_chf
        prev_pos = qty
        prev_closed = len(trades)

    idx = bars.index
    res = Result(prof, s, pd.DataFrame(trades), pd.Series(eq_close, idx), pd.Series(eq_low, idx),
                 signals, rejected, None if funding is None else funding_chf)
    res.stats = statistics(res)
    return res


def quarter_edges(start, end):
    """Testbeginn, alle Kalenderquartalsanfänge dazwischen, Testende."""
    qs = pd.date_range(start.normalize(), end, freq="QS", tz="UTC")
    return [start] + [q for q in qs if start < q < end] + [end]


def run_portfolio(btc, gold, fx_btc, fx_gold, base, gold_settings, weights=(0.7, 0.3), funding=None):
    """Originalportfolio: je Quartal Kapital nach weights aufteilen, beide
    Strategien handeln getrennt, am Quartalsende wird alles geschlossen."""
    total = base.initial_capital
    eq, eql, rows = [], [], []
    trades = {"btc": [], "gold": []}
    edges = quarter_edges(base.start, base.end)
    for a, b in zip(edges[:-1], edges[1:]):
        seg = {}
        for key, bars, fx, st, w in (("btc", btc, fx_btc, base, weights[0]),
                                     ("gold", gold, fx_gold, gold_settings, weights[1])):
            m = bars.index < b
            fund = funding[m] if key == "btc" and funding is not None else None
            r = run(bars[m], fx[m], PROFILES[key], replace(st, start=a, end=b, initial_capital=total * w), fund)
            win = (r.equity.index >= a) & (r.equity.index < b)
            seg[key] = (r.equity[win], r.equity_low[win], total * w)
            if len(r.trades):
                trades[key].append(r.trades)
        idx = seg["btc"][0].index
        # Gold hat Handelspausen: letzten Stand fortschreiben, vor dem ersten Gold-Bar = Zuteilung
        g = seg["gold"][0].reindex(idx).ffill().fillna(seg["gold"][2])
        gl = seg["gold"][1].reindex(idx).ffill().fillna(seg["gold"][2])
        eq.append(seg["btc"][0] + g)
        eql.append(seg["btc"][1] + gl)
        btc_end = float(seg["btc"][0].iloc[-1])
        gold_end = float(seg["gold"][0].iloc[-1]) if len(seg["gold"][0]) else seg["gold"][2]
        rows.append({"Quartal ab": f"{a:%d.%m.%Y}", "Start CHF": total, "BTC CHF": btc_end,
                     "Gold CHF": gold_end, "Ende CHF": btc_end + gold_end})
        total = btc_end + gold_end
    trades = {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in trades.items()}
    return pd.concat(eq), pd.concat(eql), pd.DataFrame(rows), trades


def max_drawdown(eq, eq_low=None):
    peak = eq.cummax()
    worst = eq if eq_low is None else np.minimum(eq, eq_low)
    dd = (worst - peak) / peak
    return float(dd.min())


def statistics(res):
    s, tr = res.settings, res.trades
    win = (res.equity.index >= s.start) & (res.equity.index < s.end)
    eq, eql = res.equity[win], res.equity_low[win]
    final = float(eq.iloc[-1]) if len(eq) else s.initial_capital
    years = (s.end - s.start).days / 365.25
    out = {
        "Startkapital CHF": s.initial_capital,
        "Endkapital CHF": final,
        "Nettogewinn CHF": final - s.initial_capital,
        "Rendite %": (final / s.initial_capital - 1) * 100,
        "CAGR %": ((final / s.initial_capital) ** (1 / years) - 1) * 100 if final > 0 else -100.0,
        "Max. Drawdown % (Schluss)": max_drawdown(eq) * 100,
        "Max. Drawdown % (intrabar)": max_drawdown(eq, eql) * 100,
        "Tiefster Kontostand CHF": float(eql.min()),
        "Höchster Kontostand CHF": float(eq.max()),
    }
    if len(tr):
        rt = tr.groupby("entry_time").agg(pnl=("pnl_chf", "sum"), reasons=("reason", lambda x: "+".join(sorted(set(x)))))
        wins, losses = rt.pnl[rt.pnl > 0], rt.pnl[rt.pnl <= 0]
        out.update({
            "Trades": len(rt),
            "Gewinnquote %": len(wins) / len(rt) * 100,
            "Profit Factor": wins.sum() / -losses.sum() if losses.sum() < 0 else math.inf,
            "Ø Gewinn CHF": wins.mean() if len(wins) else 0.0,
            "Ø Verlust CHF": losses.mean() if len(losses) else 0.0,
            "Grösster Gewinn CHF": rt.pnl.max(),
            "Grösster Verlust CHF": rt.pnl.min(),
            "Margin Calls": int((tr.reason == "Margin Call").sum()),
            "Exits Stop / SMA": f"{int((tr.reason == 'SL').sum())} / {int((tr.reason == 'Unter SMA').sum())}",
        })
    else:
        out["Trades"] = 0
    out["Signale ohne Fill (zu wenig Kapital)"] = res.rejected
    if res.funding_chf is not None:
        out["Funding bezahlt CHF"] = res.funding_chf
    return out


def fx_per_bar(fx, index):
    """USD/CHF-Schlusskurs des Vortags für jede Kerze (wie TradingView)."""
    shifted = fx.copy()
    shifted.index = shifted.index.normalize() + pd.Timedelta(days=1)
    vals = shifted.reindex(shifted.index.union(index)).ffill().bfill().reindex(index)
    return vals.to_numpy(float)


def yearly_table(results, start, end):
    edges = [start] + [e for y in range(start.year + 1, end.year + 1)
                       if (e := pd.Timestamp(f"{y}-{start.month:02d}-{start.day:02d}", tz="UTC")) < end]
    rows = []
    for a, b in zip(edges, edges[1:] + [end]):
        row = {"Periode": f"{a:%d.%m.%Y} – {b:%d.%m.%Y}"}
        for name, eq in results.items():
            seg = eq[(eq.index >= a) & (eq.index < b)]
            if len(seg):
                row[name] = f"{seg.iloc[-1]:,.0f}".replace(",", "'")
        rows.append(row)
    return pd.DataFrame(rows)


def load_inputs(args):
    cache = Path(args.data_dir)
    warm = date.fromisoformat(args.warmup_start)
    end_d = pd.Timestamp(args.end).date()

    def get(kind, csv_arg, fname, downloader):
        if csv_arg:
            return datasrc.load_ohlc_csv(csv_arg)
        path = cache / fname
        if path.exists() and not args.refresh:
            return datasrc.load_ohlc_csv(path)
        if not args.download:
            return None
        print(f"Lade {kind} …", flush=True)
        df = downloader(start=warm, end=end_d)
        datasrc.save_csv(df, path)
        return df

    btc = get("BTCUSDT-Perpetual 1h (Binance)", args.btc_csv, "BTCUSDT_PERP_1h.csv", datasrc.download_binance)
    gold = get("XAUUSD 1h (Dukascopy)", args.gold_csv, "XAUUSD_1h.csv", datasrc.download_dukascopy)

    if args.usdchf:
        fx = pd.Series([args.usdchf], index=[pd.Timestamp(warm, tz="UTC")])
    elif args.fx_csv:
        fx = datasrc.load_fx_csv(args.fx_csv)
    elif (cache / "USDCHF_daily.csv").exists() and not args.refresh:
        fx = datasrc.load_fx_csv(cache / "USDCHF_daily.csv")
    elif args.download:
        print("Lade USD/CHF (Dukascopy) …", flush=True)
        fx = datasrc.download_usdchf(start=warm, end=end_d)
        datasrc.save_csv(fx.to_frame(), cache / "USDCHF_daily.csv")
    else:
        fx = None
    return btc, gold, fx


def load_funding(args):
    path = Path(args.data_dir) / "BTCUSDT_funding.csv"
    if path.exists() and not args.refresh:
        df = pd.read_csv(path)
        return pd.Series(df["funding_rate"].values, index=pd.to_datetime(df["time"], utc=True))
    print("Lade Binance-Funding-Raten …", flush=True)
    s = datasrc.download_binance_funding(start=date.fromisoformat(args.warmup_start),
                                         end=pd.Timestamp(args.end).date())
    datasrc.save_csv(s.to_frame(), path)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=list(PROFILES) + ["all"], default="all")
    ap.add_argument("--capital", type=float, default=500.0, help="Startkapital CHF je Strategie")
    ap.add_argument("--start", default="2021-10-18", help="Testbeginn UTC")
    ap.add_argument("--end", default="2026-10-18", help="Testende exklusiv UTC")
    ap.add_argument("--warmup-start", default="2020-01-01", help="Beginn der Kurshistorie")
    ap.add_argument("--risk", type=float, help="Risiko je Trade in %% statt Originalrisiko")
    ap.add_argument("--no-margin-calls", action="store_true")
    ap.add_argument("--realistic", action="store_true",
                    help="Einstellungen von BTC_Gold_Breakout_realistisch.pine: 5 %% Risiko, max. 5x Hebel, "
                         "10 %% Margin, Kosten inkl. 0.02 %% Slippage, Gold in 0.01 Unzen, Funding")
    ap.add_argument("--funding", action="store_true", help="Binance-Funding-Raten für BTC belasten")
    ap.add_argument("--portfolio", action="store_true",
                    help="Originalportfolio 70/30 BTC/Gold mit Quartals-Umverteilung rechnen")
    ap.add_argument("--btc-csv", help="1h-OHLC-CSV statt Download (z. B. TradingView-Export)")
    ap.add_argument("--gold-csv", help="1h-OHLC-CSV für XAUUSD")
    ap.add_argument("--fx-csv", help="Tageskurse USD/CHF (Spalten date, usdchf)")
    ap.add_argument("--usdchf", type=float, help="Konstanter USD/CHF-Kurs statt Tageskursen")
    ap.add_argument("--download", action="store_true", help="Fehlende Daten herunterladen")
    ap.add_argument("--refresh", action="store_true", help="Cache ignorieren und neu laden")
    ap.add_argument("--data-dir", default=str(datasrc.CACHE_DIR))
    ap.add_argument("--out", default=str(datasrc.CACHE_DIR / "results"))
    args = ap.parse_args()

    btc, gold, fx = load_inputs(args)
    if fx is None:
        sys.exit("Kein USD/CHF-Kurs: --download, --fx-csv oder --usdchf angeben.")
    start, end = pd.Timestamp(args.start, tz="UTC"), pd.Timestamp(args.end, tz="UTC")
    base = Settings(start=start, end=end, initial_capital=args.capital,
                    risk=None if args.risk is None else args.risk / 100,
                    margin_calls=not args.no_margin_calls)
    gold_settings = replace(base, commission_pct=0.005, mintick=0.01, qty_step=1.0)
    if args.realistic:
        # Kosten = Provision + 0.02 % Slippage, in TradingView als Provision eingetragen
        base = replace(base, risk=0.05 if args.risk is None else base.risk, max_leverage=5.0,
                       margin_long_pct=10.0, commission_pct=0.07, slippage_ticks=0)
        gold_settings = replace(base, commission_pct=0.025, mintick=0.01, qty_step=0.01)
        args.funding = True

    names = list(PROFILES) if args.profile == "all" else [args.profile]
    funding = load_funding(args) if args.funding else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    curves = {}
    for key in names:
        prof = PROFILES[key]
        bars = gold if prof.is_gold else btc
        if bars is None:
            print(f"\n[{prof.name}] übersprungen – keine Kursdaten.")
            continue
        bars = bars[bars.index < end]
        if bars.index[-1] < end - pd.Timedelta(days=2):
            print(f"\nWARNUNG: Daten enden am {bars.index[-1]:%d.%m.%Y %H:%M} UTC, vor dem Testende.")
        fund = None
        if args.funding and not prof.is_gold:
            fund = funding.reindex(bars.index).fillna(0.0).to_numpy(float)
        res = run(bars, fx_per_bar(fx, bars.index), prof, gold_settings if prof.is_gold else base, fund)
        curves[prof.name] = res.equity
        print(f"\n=== {prof.name} | {start:%d.%m.%Y} – {end:%d.%m.%Y} | Daten ab {bars.index[0]:%d.%m.%Y} ===")
        for k, v in res.stats.items():
            print(f"  {k:<38} {v:,.2f}" if isinstance(v, float) else f"  {k:<38} {v}")
        res.trades.to_csv(out / f"trades_{key}.csv", index=False)
        res.equity.to_frame("equity_chf").to_csv(out / f"equity_{key}.csv")

    if args.portfolio and btc is not None and gold is not None:
        b_all, g_all = btc[btc.index < end], gold[gold.index < end]
        fund = funding.reindex(b_all.index).fillna(0.0).to_numpy(float) if args.funding else None
        w = (0.7, 0.3)
        peq, peql, qtab, ptr = run_portfolio(b_all, g_all, fx_per_bar(fx, b_all.index), fx_per_bar(fx, g_all.index),
                                             base, gold_settings, w, fund)
        curves["Portfolio 70/30"] = peq
        peq.to_frame("equity_chf").to_csv(out / "equity_portfolio.csv")
        qtab.to_csv(out / "portfolio_quartale.csv", index=False)
        final = float(peq.iloc[-1])
        years = (end - start).days / 365.25
        print(f"\n=== Portfolio 70 % BTC / 30 % Gold, Quartals-Umverteilung | {start:%d.%m.%Y} – {end:%d.%m.%Y} ===")
        print(f"  Startkapital CHF                       {args.capital:,.2f}")
        print(f"  Endkapital CHF                         {final:,.2f}")
        print(f"  Rendite %                              {(final / args.capital - 1) * 100:,.2f}")
        print(f"  CAGR %                                 {((final / args.capital) ** (1 / years) - 1) * 100:,.2f}")
        print(f"  Max. Drawdown % (Schluss)              {max_drawdown(peq) * 100:,.2f}")
        print(f"  Max. Drawdown % (intrabar)             {max_drawdown(peq, peql) * 100:,.2f}")
        print(f"  Tiefster Kontostand CHF                {float(np.minimum(peq, peql).min()):,.2f}")
        for k, name in (("btc", "BTC"), ("gold", "Gold")):
            t = ptr[k]
            n = t["entry_time"].nunique() if len(t) else 0
            pnl = t["pnl_chf"].sum() if len(t) else 0.0
            print(f"  {name}: Trades {n}, Summe Trade-Gewinne CHF {pnl:,.2f}")

    if btc is not None:
        b = btc[(btc.index >= start) & (btc.index < end)]
        r = fx_per_bar(fx, b.index)
        bh = args.capital * (b.close * r) / (b.close.iloc[0] * r[0])
        curves["Buy & Hold BTC"] = bh
        bh.to_frame("equity_chf").to_csv(out / "equity_buyhold.csv")
        print(f"\n=== Vergleich Buy & Hold BTC ===\n  Endkapital CHF {bh.iloc[-1]:,.2f}"
              f" | Max. Drawdown {max_drawdown(bh) * 100:.1f} %")
    if curves:
        print("\nKontostand CHF am Ende jedes Testjahres:")
        print(yearly_table(curves, start, end).to_string(index=False))


if __name__ == "__main__":
    main()
