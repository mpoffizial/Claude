"""
ICT Silver Bullet FVG - Python Backtest Engine
==============================================
Faithful port of the Pine Script v5 strategy "ICT Silver Bullet FVG - Strategy":

  Bias (Midnight Open) -> Liquidity Sweep (PDH/PDL, Asia H/L) -> Displacement-FVG
  -> Limit entry (Edge / CE / Full) -> SL behind swing -> TP at liquidity / fixed RRR

Replicates TradingView broker-emulator semantics:
  - signals evaluated on bar close, limit order active from the next bar
  - limit entries fill at open if the bar opens through the limit, else at limit
  - no slippage on limit fills, slippage (1 tick) on stop exits
  - intrabar OHLC path assumption (open closer to high => O-H-L-C else O-L-H-C)
  - pending management: "missed" (TP touched without fill) and "expired"
    (outside SB window and > graceBars after placement) cancel the order
  - cash-per-contract commission on entry and exit

Data: 1-minute OHLC, UTC timestamps, converted to America/New_York.
"""

import numpy as np
import pandas as pd
from numba import njit

# ---------------------------------------------------------------- constants
TICK = 0.25          # NQ tick size
POINT_VALUE = 20.0   # USD per index point (NQ; MNQ = 2.0)
COMMISSION = 2.5     # USD per contract per side
SLIP_TICKS = 1       # ticks of slippage on stop fills

ENTRY_EDGE, ENTRY_CE, ENTRY_FULL = 0, 1, 2
TP_LIQ, TP_FIX = 0, 1


# ---------------------------------------------------------------- data prep
def load_data(csv_path: str, timeframe: str = "1min") -> pd.DataFrame:
    """Load tab-separated Dukascopy-style CSV (UTC), convert to NY time."""
    df = pd.read_csv(csv_path, sep="\t")
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_convert("America/New_York")
    if timeframe != "1min":
        df = (df.resample(timeframe, label="left", closed="left")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna())
    return df[["open", "high", "low", "close"]].astype(np.float64)


def precompute(df: pd.DataFrame) -> dict:
    """Per-bar series that do not depend on strategy parameters."""
    idx = df.index
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    minute_of_day = (idx.hour * 60 + idx.minute).values.astype(np.int32)

    # calendar day id (NY) - resets midnight-open / setups-per-day
    cal_day = idx.normalize()
    new_day = np.zeros(n, dtype=np.bool_)
    new_day[0] = True
    new_day[1:] = cal_day[1:] != cal_day[:-1]

    # midnight open: open of first bar of each NY calendar day, carried forward
    mid_open = np.full(n, np.nan)
    cur = np.nan
    for i in range(n):
        if new_day[i]:
            cur = o[i]
        mid_open[i] = cur

    # trading day id: session boundary 18:00 NY (CME reopen) for PDH/PDL
    tday = np.zeros(n, dtype=np.int64)
    d = 0
    for i in range(1, n):
        if minute_of_day[i] >= 1080 and minute_of_day[i - 1] < 1080:
            d += 1
        elif cal_day[i] != cal_day[i - 1] and minute_of_day[i] >= 1080:
            d += 1
        tday[i] = d
    # previous trading day high/low (constant during current trading day)
    pdh = np.full(n, np.nan)
    pdl = np.full(n, np.nan)
    day_hi, day_lo = {}, {}
    for i in range(n):
        td = tday[i]
        if td - 1 in day_hi:
            pdh[i] = day_hi[td - 1]
            pdl[i] = day_lo[td - 1]
        day_hi[td] = max(day_hi.get(td, -np.inf), h[i])
        day_lo[td] = min(day_lo.get(td, np.inf), l[i])

    # Asia session 18:00-00:00 NY, high/low carried until next session start
    in_asia = minute_of_day >= 1080
    asia_hi = np.full(n, np.nan)
    asia_lo = np.full(n, np.nan)
    ah, al = np.nan, np.nan
    for i in range(n):
        if in_asia[i] and (i == 0 or not in_asia[i - 1]):
            ah, al = h[i], l[i]
        elif in_asia[i]:
            ah = max(ah, h[i])
            al = min(al, l[i])
        asia_hi[i] = ah
        asia_lo[i] = al

    # Wilder RMA ATR(14)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.full(n, np.nan)
    if n > 14:
        atr[13] = tr[:14].mean()
        for i in range(14, n):
            atr[i] = (atr[i - 1] * 13 + tr[i]) / 14.0

    return dict(o=o, h=h, l=l, c=c, n=n, minute=minute_of_day,
                new_day=new_day, mid_open=mid_open, pdh=pdh, pdl=pdl,
                asia_hi=asia_hi, asia_lo=asia_lo, atr=atr, index=idx)


# ---------------------------------------------------------------- core loop
@njit(cache=True)
def _run(o, h, l, c, minute, new_day, mid_open, pdh, pdl, asia_hi, asia_lo, atr,
         use_am, use_ldn, use_pm, use_bias, use_pd, use_asia,
         disp_mult, min_gap_tk, sweep_lb, entry_mode, sl_lb, sl_buf_tk,
         tp_mode, fix_rrr, min_rrr, max_day, grace_bars,
         tick, point_value, commission, slip_ticks,
         gap_atr, buf_atr, be_trigger_rr, max_hold_bars):
    # gap_atr / buf_atr > 0: FVG-Mindestgroesse bzw. SL-Puffer als ATR-Vielfache
    # statt fixer Ticks (skaleninvariant ueber Preisniveaus/Regime).
    # be_trigger_rr > 0: Stop auf Einstand, sobald be_trigger_rr * Risiko
    # erreicht wurde (Bar-Close-Logik). max_hold_bars > 0: Zeit-Stopp zum Close.
    n = len(c)
    min_gap = min_gap_tk * tick
    sl_buf = sl_buf_tk * tick
    slip = slip_ticks * tick

    # trade log
    max_tr = 5000
    t_entry_i = np.zeros(max_tr, dtype=np.int64)
    t_exit_i = np.zeros(max_tr, dtype=np.int64)
    t_dir = np.zeros(max_tr, dtype=np.int64)
    t_epx = np.zeros(max_tr)
    t_xpx = np.zeros(max_tr)
    t_pnl = np.zeros(max_tr)
    ntr = 0
    n_setups = 0
    n_missed = 0
    n_expired = 0

    pend_dir = 0
    pend_bar = -1
    p_entry = 0.0
    p_sl = 0.0
    p_tp = 0.0
    pos_dir = 0
    pos_entry_px = 0.0
    pos_entry_i = -1
    pos_risk = 0.0
    be_done = True
    setups_today = 0
    last_bull_sweep = -10**9
    last_bear_sweep = -10**9

    for i in range(n):
        if new_day[i]:
            setups_today = 0

        # ---------- broker emulator: process open position exits ----------
        if pos_dir != 0:
            exit_px = np.nan
            if pos_dir == 1:
                sl_hit = l[i] <= p_sl
                tp_hit = h[i] >= p_tp
                if o[i] <= p_sl:                       # gap through stop
                    exit_px = o[i] - slip
                elif o[i] >= p_tp:                     # gap through target
                    exit_px = p_tp if p_tp > o[i] else o[i]
                elif sl_hit and tp_hit:
                    high_first = (h[i] - o[i]) < (o[i] - l[i])
                    # open closer to high => O-H-L-C => TP first
                    exit_px = p_tp if high_first else p_sl - slip
                elif sl_hit:
                    exit_px = p_sl - slip
                elif tp_hit:
                    exit_px = p_tp
            else:
                sl_hit = h[i] >= p_sl
                tp_hit = l[i] <= p_tp
                if o[i] >= p_sl:
                    exit_px = o[i] + slip
                elif o[i] <= p_tp:
                    exit_px = p_tp if p_tp < o[i] else o[i]
                elif sl_hit and tp_hit:
                    high_first = (h[i] - o[i]) < (o[i] - l[i])
                    # O-H-L-C => stop (above) first for shorts
                    exit_px = p_sl + slip if high_first else p_tp
                elif sl_hit:
                    exit_px = p_sl + slip
                elif tp_hit:
                    exit_px = p_tp
            if not np.isnan(exit_px):
                pnl = (exit_px - pos_entry_px) * pos_dir * point_value - 2.0 * commission
                if ntr < max_tr:
                    t_entry_i[ntr] = pos_entry_i
                    t_exit_i[ntr] = i
                    t_dir[ntr] = pos_dir
                    t_epx[ntr] = pos_entry_px
                    t_xpx[ntr] = exit_px
                    t_pnl[ntr] = pnl
                    ntr += 1
                pos_dir = 0
            else:
                # Position hat die Bar ueberlebt -> Management (Bar-Close)
                if be_trigger_rr > 0.0 and not be_done:
                    if pos_dir == 1 and h[i] >= pos_entry_px + be_trigger_rr * pos_risk:
                        if p_sl < pos_entry_px:
                            p_sl = pos_entry_px
                        be_done = True
                    elif pos_dir == -1 and l[i] <= pos_entry_px - be_trigger_rr * pos_risk:
                        if p_sl > pos_entry_px:
                            p_sl = pos_entry_px
                        be_done = True
                if max_hold_bars > 0 and i - pos_entry_i >= max_hold_bars:
                    exit_px = c[i]
                    pnl = (exit_px - pos_entry_px) * pos_dir * point_value - 2.0 * commission
                    if ntr < max_tr:
                        t_entry_i[ntr] = pos_entry_i
                        t_exit_i[ntr] = i
                        t_dir[ntr] = pos_dir
                        t_epx[ntr] = pos_entry_px
                        t_xpx[ntr] = exit_px
                        t_pnl[ntr] = pnl
                        ntr += 1
                    pos_dir = 0

        # ---------- broker emulator: pending limit fills ----------
        filled_this_bar = False
        if pend_dir != 0 and i > pend_bar:
            if pend_dir == 1:
                if o[i] <= p_entry or l[i] <= p_entry:
                    fill = o[i] if o[i] <= p_entry else p_entry
                    pos_dir = 1
                    pos_entry_px = fill
                    pos_entry_i = i
                    pos_risk = fill - p_sl
                    be_done = be_trigger_rr <= 0.0
                    pend_dir = 0
                    filled_this_bar = True
                    # same-bar exit checks after the fill
                    if l[i] <= p_sl and h[i] >= p_tp:
                        exit_px = p_sl - slip  # ambiguous -> conservative
                    elif l[i] <= p_sl:
                        exit_px = p_sl - slip
                    elif h[i] >= p_tp:
                        if fill == o[i]:
                            exit_px = p_tp
                        else:
                            high_first = (h[i] - o[i]) < (o[i] - l[i])
                            exit_px = np.nan if high_first else p_tp
                    else:
                        exit_px = np.nan
                    if not np.isnan(exit_px):
                        pnl = (exit_px - pos_entry_px) * point_value - 2.0 * commission
                        if ntr < max_tr:
                            t_entry_i[ntr] = pos_entry_i
                            t_exit_i[ntr] = i
                            t_dir[ntr] = 1
                            t_epx[ntr] = pos_entry_px
                            t_xpx[ntr] = exit_px
                            t_pnl[ntr] = pnl
                            ntr += 1
                        pos_dir = 0
            else:
                if o[i] >= p_entry or h[i] >= p_entry:
                    fill = o[i] if o[i] >= p_entry else p_entry
                    pos_dir = -1
                    pos_entry_px = fill
                    pos_entry_i = i
                    pos_risk = p_sl - fill
                    be_done = be_trigger_rr <= 0.0
                    pend_dir = 0
                    filled_this_bar = True
                    if h[i] >= p_sl and l[i] <= p_tp:
                        exit_px = p_sl + slip
                    elif h[i] >= p_sl:
                        exit_px = p_sl + slip
                    elif l[i] <= p_tp:
                        if fill == o[i]:
                            exit_px = p_tp
                        else:
                            high_first = (h[i] - o[i]) < (o[i] - l[i])
                            exit_px = p_tp if high_first else np.nan
                    else:
                        exit_px = np.nan
                    if not np.isnan(exit_px):
                        pnl = (pos_entry_px - exit_px) * point_value - 2.0 * commission
                        if ntr < max_tr:
                            t_entry_i[ntr] = pos_entry_i
                            t_exit_i[ntr] = i
                            t_dir[ntr] = -1
                            t_epx[ntr] = pos_entry_px
                            t_xpx[ntr] = exit_px
                            t_pnl[ntr] = pnl
                            ntr += 1
                        pos_dir = 0

        # ---------- bar-close script logic ----------
        # sweep tracking (needs pdl/pdh/asia levels)
        sweep_bull = False
        sweep_bear = False
        if use_pd and not np.isnan(pdl[i]) and l[i] < pdl[i] and c[i] > pdl[i]:
            sweep_bull = True
        if use_asia and not np.isnan(asia_lo[i]) and l[i] < asia_lo[i] and c[i] > asia_lo[i]:
            sweep_bull = True
        if use_pd and not np.isnan(pdh[i]) and h[i] > pdh[i] and c[i] < pdh[i]:
            sweep_bear = True
        if use_asia and not np.isnan(asia_hi[i]) and h[i] > asia_hi[i] and c[i] < asia_hi[i]:
            sweep_bear = True
        if sweep_bull:
            last_bull_sweep = i
        if sweep_bear:
            last_bear_sweep = i

        # session windows (NY minutes): AM 10-11, LDN 3-4, PM 14-15
        m = minute[i]
        in_sb = ((use_am and 600 <= m < 660) or
                 (use_ldn and 180 <= m < 240) or
                 (use_pm and 840 <= m < 900))

        # pending management (same order as Pine: fill label / missed / expired)
        if pend_dir != 0 and not filled_this_bar and i > pend_bar:
            if pend_dir == 1:
                missed = h[i] >= p_tp and l[i] > p_entry
            else:
                missed = l[i] <= p_tp and h[i] < p_entry
            expired = (not in_sb) and (i - pend_bar > grace_bars)
            if missed or expired:
                if missed:
                    n_missed += 1
                else:
                    n_expired += 1
                pend_dir = 0

        # new setups
        if (pos_dir == 0 and pend_dir == 0 and in_sb and setups_today < max_day
                and i >= 2 and not np.isnan(atr[i]) and not np.isnan(mid_open[i])):
            gap_req = gap_atr * atr[i] if gap_atr > 0.0 else min_gap
            buf_eff = buf_atr * atr[i] if buf_atr > 0.0 else sl_buf
            disp = abs(c[i - 1] - o[i - 1]) > disp_mult * atr[i]
            bias_long = c[i] > mid_open[i]
            bias_short = c[i] < mid_open[i]

            # --- long ---
            bull_fvg = l[i] > h[i - 2]
            gap_b = l[i] - h[i - 2]
            valid_bull = bull_fvg and disp and c[i - 1] > o[i - 1] and gap_b >= gap_req
            bull_recent = (i - last_bull_sweep) <= sweep_lb
            if valid_bull and bull_recent and ((not use_bias) or bias_long):
                fvg_top = l[i]
                fvg_bot = h[i - 2]
                if entry_mode == 0:
                    entry = fvg_top
                elif entry_mode == 1:
                    entry = (fvg_top + fvg_bot) / 2.0
                else:
                    entry = fvg_bot
                lo_sw = l[i]
                j0 = i - sl_lb + 1
                if j0 < 0:
                    j0 = 0
                for j in range(j0, i + 1):
                    if l[j] < lo_sw:
                        lo_sw = l[j]
                sl = lo_sw - buf_eff
                risk = entry - sl
                if risk > 0:
                    if tp_mode == 1:
                        tp = entry + fix_rrr * risk
                    else:
                        best = np.nan
                        if use_pd and not np.isnan(pdh[i]) and pdh[i] > entry:
                            best = pdh[i]
                        if use_asia and not np.isnan(asia_hi[i]) and asia_hi[i] > entry:
                            best = asia_hi[i] if np.isnan(best) else min(best, asia_hi[i])
                        tp = entry + fix_rrr * risk if np.isnan(best) else best
                    rr = (tp - entry) / risk
                    if rr >= min_rrr:
                        pend_dir = 1
                        pend_bar = i
                        p_entry = entry
                        p_sl = sl
                        p_tp = tp
                        setups_today += 1
                        n_setups += 1

            # --- short ---
            if pend_dir == 0:
                bear_fvg = h[i] < l[i - 2]
                gap_s = l[i - 2] - h[i]
                valid_bear = bear_fvg and disp and c[i - 1] < o[i - 1] and gap_s >= gap_req
                bear_recent = (i - last_bear_sweep) <= sweep_lb
                if valid_bear and bear_recent and ((not use_bias) or bias_short):
                    fvg_top = l[i - 2]
                    fvg_bot = h[i]
                    if entry_mode == 0:
                        entry = fvg_bot
                    elif entry_mode == 1:
                        entry = (fvg_top + fvg_bot) / 2.0
                    else:
                        entry = fvg_top
                    hi_sw = h[i]
                    j0 = i - sl_lb + 1
                    if j0 < 0:
                        j0 = 0
                    for j in range(j0, i + 1):
                        if h[j] > hi_sw:
                            hi_sw = h[j]
                    sl = hi_sw + buf_eff
                    risk = sl - entry
                    if risk > 0:
                        if tp_mode == 1:
                            tp = entry - fix_rrr * risk
                        else:
                            best = np.nan
                            if use_pd and not np.isnan(pdl[i]) and pdl[i] < entry:
                                best = pdl[i]
                            if use_asia and not np.isnan(asia_lo[i]) and asia_lo[i] < entry:
                                best = asia_lo[i] if np.isnan(best) else max(best, asia_lo[i])
                            tp = entry - fix_rrr * risk if np.isnan(best) else best
                        rr = (entry - tp) / risk
                        if rr >= min_rrr:
                            pend_dir = -1
                            pend_bar = i
                            p_entry = entry
                            p_sl = sl
                            p_tp = tp
                            setups_today += 1
                            n_setups += 1

    return (t_entry_i[:ntr], t_exit_i[:ntr], t_dir[:ntr], t_epx[:ntr],
            t_xpx[:ntr], t_pnl[:ntr], n_setups, n_missed, n_expired)


# ---------------------------------------------------------------- interface
DEFAULTS = dict(use_am=True, use_ldn=True, use_pm=True, use_bias=True,
                use_pd=True, use_asia=True,
                disp_mult=1.2, min_gap_tk=4, sweep_lb=30, entry_mode=ENTRY_CE,
                sl_lb=10, sl_buf_tk=8, tp_mode=TP_LIQ, fix_rrr=2.0,
                min_rrr=2.0, max_day=4, grace_bars=24,
                gap_atr=0.0, buf_atr=0.0, be_trigger_rr=0.0, max_hold_bars=0)


def run_backtest(pre: dict, params: dict | None = None,
                 start: int = 0, end: int | None = None,
                 tick: float = TICK, point_value: float = POINT_VALUE,
                 commission: float = COMMISSION, slip_ticks: int = SLIP_TICKS) -> dict:
    p = dict(DEFAULTS)
    if params:
        p.update(params)
    end = pre["n"] if end is None else end
    sl_ = slice(start, end)
    res = _run(pre["o"][sl_], pre["h"][sl_], pre["l"][sl_], pre["c"][sl_],
               pre["minute"][sl_], pre["new_day"][sl_], pre["mid_open"][sl_],
               pre["pdh"][sl_], pre["pdl"][sl_], pre["asia_hi"][sl_],
               pre["asia_lo"][sl_], pre["atr"][sl_],
               p["use_am"], p["use_ldn"], p["use_pm"], p["use_bias"],
               p["use_pd"], p["use_asia"],
               float(p["disp_mult"]), float(p["min_gap_tk"]), int(p["sweep_lb"]),
               int(p["entry_mode"]), int(p["sl_lb"]), float(p["sl_buf_tk"]),
               int(p["tp_mode"]), float(p["fix_rrr"]), float(p["min_rrr"]),
               int(p["max_day"]), int(p["grace_bars"]),
               tick, point_value, commission, slip_ticks,
               float(p["gap_atr"]), float(p["buf_atr"]),
               float(p["be_trigger_rr"]), int(p["max_hold_bars"]))
    entry_i, exit_i, tdir, epx, xpx, pnl, n_setups, n_missed, n_expired = res
    return dict(entry_i=entry_i + start, exit_i=exit_i + start, dir=tdir,
                entry_px=epx, exit_px=xpx, pnl=pnl,
                n_setups=n_setups, n_missed=n_missed, n_expired=n_expired,
                params=p)


def metrics(bt: dict, n_days: float | None = None) -> dict:
    pnl = bt["pnl"]
    n = len(pnl)
    if n == 0:
        return dict(trades=0, net=0.0, winrate=np.nan, pf=np.nan, avg=np.nan,
                    max_dd=0.0, gross_win=0.0, gross_loss=0.0,
                    setups=bt["n_setups"], missed=bt["n_missed"],
                    expired=bt["n_expired"], longs=0, shorts=0)
    wins = pnl > 0
    gross_win = pnl[wins].sum()
    gross_loss = -pnl[~wins].sum()
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    dd = (np.concatenate(([0.0], equity)) - peak)
    return dict(trades=n, net=float(pnl.sum()),
                winrate=float(wins.mean() * 100),
                pf=float(gross_win / gross_loss) if gross_loss > 0 else np.inf,
                avg=float(pnl.mean()), max_dd=float(-dd.min()),
                gross_win=float(gross_win), gross_loss=float(gross_loss),
                setups=bt["n_setups"], missed=bt["n_missed"],
                expired=bt["n_expired"],
                longs=int((bt["dir"] == 1).sum()),
                shorts=int((bt["dir"] == -1).sum()))


def print_report(title: str, m: dict):
    print(f"--- {title} ---")
    print(f"  Trades: {m['trades']}  (long {m['longs']} / short {m['shorts']})"
          f"  | Setups: {m['setups']}  missed: {m['missed']}  expired: {m['expired']}")
    if m["trades"]:
        print(f"  Net PnL: {m['net']:+.2f} USD | Winrate: {m['winrate']:.1f}% | "
              f"PF: {m['pf']:.2f} | Avg/Trade: {m['avg']:+.2f} USD | MaxDD: {m['max_dd']:.2f} USD")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "USATECHIDXUSD_M1.csv"
    for tf in ("1min", "3min"):
        df = load_data(path, tf)
        pre = precompute(df)
        bt = run_backtest(pre)
        print(f"\n===== {tf}  bars={pre['n']}  {df.index[0]} .. {df.index[-1]} =====")
        print_report("Defaults", metrics(bt))
