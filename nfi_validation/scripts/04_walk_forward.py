#!/usr/bin/env python3
"""
Phase 3 - Time-Frozen Walk-Forward (the actual out-of-sample test).

For each quarterly checkpoint D from --start to today:
  1. Find the NFI commit that was HEAD on/before D  (rev-list --before=D).
  2. `git worktree add` that SHA  -> the strategy EXACTLY as it existed on D.
  3. Pick the strategy class that was current then (highest NostalgiaForInfinityX*.py
     present at that commit).
  4. Reconstruct a point-in-time StaticPairList for the window (lib.pit_pairlist),
     so we don't apply today's survivor universe to old data.
  5. Backtest strictly on [D, D+window_days]. NOT ONE candle before D.
  6. Load the trades, verify none opened before D, compute clean metrics.

Aggregates all windows into one CLEAN (out-of-sample) trade set for Phases 5/6, and
plots each window's equity curve SEPARATELY (not concatenated), per the deliverable.

This script SHELLS OUT to freqtrade (local binary or docker). It needs (a) OHLCV data
downloaded for the universe+window and (b) freqtrade==2026.6. It does not fabricate
anything: with no data it stops at the first window and tells you what is missing.

Example
-------
  python3 04_walk_forward.py \
      --nfi-repo ../vendor/NostalgiaForInfinity \
      --data-dir ../user_data/data/binance \
      --base-config ../config/config.json \
      --universe ../config/pairlist-static-binance-spot-usdt.json \
      --out ../outputs --fee 0.00075 --start 2023-01-01 \
      --freqtrade-cmd "freqtrade"
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "lib"))
import metrics as M  # noqa: E402
from pit_pairlist import estimate_survivorship_bias, pit_static_pairlist  # noqa: E402

VERSION_FILES = [f"NostalgiaForInfinityX{n}.py" for n in ["", "2", "3", "4", "5", "6", "7"]]


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                          check=True).stdout.strip()


def default_branch(repo: Path) -> str:
    try:
        ref = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
        return ref.split("/")[-1]
    except subprocess.CalledProcessError:
        return "main"


def quarterly_checkpoints(start: date, end: date) -> list[date]:
    out = []
    for yy in range(start.year, end.year + 1):
        for m in (1, 4, 7, 10):
            d = date(yy, m, 1)
            if start <= d <= end:
                out.append(d)
    return out


def commit_before(repo: Path, branch: str, d: date) -> str | None:
    sha = git(repo, "rev-list", "-1", f"--before={d.isoformat()} 23:59:59", branch)
    return sha or None


def strategy_at_commit(repo: Path, sha: str) -> str | None:
    """Newest NostalgiaForInfinityX*.py present at this commit -> class name."""
    files = set(git(repo, "ls-tree", "--name-only", sha).splitlines())
    for f in reversed(VERSION_FILES):
        if f in files:
            return f[:-3]  # class name == file stem
    return None


def load_ft_trades(results_dir: Path) -> pd.DataFrame:
    """Load trades from the most recent freqtrade backtest export (.zip or .json)."""
    candidates = sorted(results_dir.glob("*.zip")) + sorted(results_dir.glob("*.json"))
    candidates = [c for c in candidates if "meta" not in c.name and ".last_result" not in c.name]
    if not candidates:
        raise FileNotFoundError(f"no backtest export in {results_dir}")
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    if latest.suffix == ".zip":
        with zipfile.ZipFile(latest) as z:
            name = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
            data = json.loads(z.read(name))
    else:
        data = json.loads(latest.read_text())
    strat = next(iter(data["strategy"].values()))
    trades = pd.DataFrame(strat["trades"])
    if not trades.empty:
        trades["open_date"] = pd.to_datetime(trades["open_date"])
        trades["close_date"] = pd.to_datetime(trades["close_date"])
    return trades


def run_window(args, repo: Path, branch: str, universe: list[str], cp: date) -> dict | None:
    sha = commit_before(repo, branch, cp)
    if not sha:
        return {"checkpoint": cp.isoformat(), "status": "no_commit_before"}
    strat = strategy_at_commit(repo, sha)
    if not strat:
        return {"checkpoint": cp.isoformat(), "sha": sha, "status": "no_strategy_file"}

    window_end = cp + timedelta(days=args.window_days)
    wf_dir = Path(args.out) / f"wf_{cp.isoformat()}"
    wf_dir.mkdir(parents=True, exist_ok=True)

    # (1) time-frozen checkout
    wt = wf_dir / "strategy_src"
    if wt.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
    git(repo, "worktree", "add", "--detach", "--force", str(wt), sha)

    # (2) point-in-time pairlist + survivorship measurement
    data_dir = Path(args.data_dir)
    bias = estimate_survivorship_bias(data_dir, universe, cp)
    pit_pairs = pit_static_pairlist(data_dir, universe, cp, top_n=args.top_n)
    if not pit_pairs:
        return {"checkpoint": cp.isoformat(), "sha": sha, "strategy": strat,
                "status": "no_data_for_window", "survivorship": bias}

    # (3) per-window config
    base = json.loads(Path(args.base_config).read_text())
    base["fee"] = args.fee
    base["exchange"]["pair_whitelist"] = pit_pairs
    base["pairlists"] = [{"method": "StaticPairList"}]
    cfg_path = wf_dir / "config.json"
    cfg_path.write_text(json.dumps(base, indent=2))

    # (4) backtest strictly inside the window
    results_dir = wf_dir / "backtest_results"
    results_dir.mkdir(exist_ok=True)
    cmd = args.freqtrade_cmd.split() + [
        "backtesting",
        "--config", str(cfg_path),
        "--strategy", strat,
        "--strategy-path", str(wt),
        "--datadir", str(data_dir.parent),
        "--timerange", f"{cp.strftime('%Y%m%d')}-{window_end.strftime('%Y%m%d')}",
        "--timeframe", "5m",
        "--export", "trades",
        "--export-filename", str(results_dir / f"wf_{cp.isoformat()}"),
        "--fee", str(args.fee),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"checkpoint": cp.isoformat(), "sha": sha, "strategy": strat,
                "status": "freqtrade_failed", "stderr": proc.stderr[-1500:],
                "survivorship": bias}

    # (5) load + verify look-ahead-free
    trades = load_ft_trades(results_dir)
    cp_ts = pd.Timestamp(cp, tz="UTC")
    if not trades.empty:
        leaked = trades[trades["open_date"] < cp_ts]
        assert leaked.empty, f"LOOK-AHEAD: {len(leaked)} trades opened before {cp}"
    trades.to_parquet(wf_dir / "trades.parquet")

    summ = M.summarize(trades)
    summ.update({
        "checkpoint": cp.isoformat(),
        "window_end": window_end.isoformat(),
        "sha": sha, "strategy": strat,
        "version_age_days": None,  # filled from git_history.json if desired
        "n_pairs_pit": len(pit_pairs),
        "status": "ok",
        "contaminated": False,
        "survivorship": bias,
    })
    return summ


def plot_windows_separately(per_window: list[dict], out_png: Path, out_dir: Path) -> None:
    """Each window's equity curve in its OWN subplot (not concatenated)."""
    ok = [w for w in per_window if w.get("status") == "ok"]
    if not ok:
        print("[plot] no successful windows to plot")
        return
    n = len(ok)
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3 * rows), squeeze=False)
    for i, w in enumerate(ok):
        ax = axes[i // cols][i % cols]
        wf_dir = out_dir / f"wf_{w['checkpoint']}"
        tr = pd.read_parquet(wf_dir / "trades.parquet")
        eq = M.realized_equity_curve(tr)
        ax.plot(eq.index, eq.values, color="#4c72b0")
        ax.axhline(10_000, color="gray", ls="--", lw=0.8)
        ax.set_title(f"{w['checkpoint']} · {w['strategy']}\n"
                     f"{w['n_trades']} trades · MDD {w['max_drawdown']*100:.0f}%", fontsize=8)
        ax.tick_params(labelsize=6)
    for j in range(n, rows * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle("Phase 3 - Time-frozen walk-forward: each window plotted separately "
                 "(CLEAN / out-of-sample)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_png, dpi=130)
    print(f"[plot] wrote {out_png}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nfi-repo", required=True, type=Path)
    ap.add_argument("--data-dir", required=True, type=Path,
                    help="freqtrade data dir, e.g. user_data/data/binance")
    ap.add_argument("--base-config", required=True, type=Path)
    ap.add_argument("--universe", required=True, type=Path,
                    help="json with exchange.pair_whitelist (NFI static list)")
    ap.add_argument("--out", type=Path, default=Path("../outputs"))
    ap.add_argument("--fee", type=float, default=0.00075)
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--window-days", type=int, default=90)
    ap.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    ap.add_argument("--freqtrade-cmd", default="freqtrade",
                    help="e.g. 'freqtrade' or 'docker compose run --rm freqtrade'")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    repo = args.nfi_repo
    branch = default_branch(repo)
    universe = json.loads(args.universe.read_text())["exchange"]["pair_whitelist"]
    checkpoints = quarterly_checkpoints(args.start, date.today())
    print(f"[walk-forward] {len(checkpoints)} windows, branch={branch}, "
          f"universe={len(universe)} pairs, fee={args.fee}")

    per_window = []
    for cp in checkpoints:
        print(f"  -> window {cp} .. {cp + timedelta(days=args.window_days)}")
        res = run_window(args, repo, branch, universe, cp)
        per_window.append(res)
        print(f"     status={res.get('status')} "
              f"trades={res.get('n_trades')} pf={res.get('profit_factor')}")

    (args.out / "walk_forward.json").write_text(json.dumps(per_window, indent=2, default=str))
    print(f"[json] wrote {args.out / 'walk_forward.json'}")

    # aggregate clean trades for Phases 5/6
    frames = []
    for w in per_window:
        if w.get("status") == "ok":
            wf_dir = args.out / f"wf_{w['checkpoint']}"
            tr = pd.read_parquet(wf_dir / "trades.parquet")
            tr["window"] = w["checkpoint"]
            frames.append(tr)
    if frames:
        allt = pd.concat(frames, ignore_index=True)
        allt.to_parquet(args.out / "clean_trades_aggregated.parquet")
        print(f"[agg] {len(allt)} clean out-of-sample trades -> clean_trades_aggregated.parquet")
        plot_windows_separately(per_window, args.out / "equity_curves.png", args.out)
    else:
        print("[agg] NO clean trades produced (missing data or freqtrade). Nothing fabricated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
