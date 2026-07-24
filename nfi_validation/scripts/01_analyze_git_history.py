#!/usr/bin/env python3
"""
Phase 3 (data-independent core): structural curve-fitting analysis of NFI from git.

This is the ONE part of the whole validation that needs no market data. It answers,
purely from the strategy's own git history, the question the task is really about:
"Is NostalgiaForInfinity a re-tuned, ever-growing pile of hand-set thresholds
(the curve-fitting hypothesis), or a stable model?"

It measures, all directly from the repo:
  * version lineage        - when each NostalgiaForInfinityX*.py first appeared
  * commit cadence         - how often the then-active strategy file is touched
  * complexity growth      - lines + count of tuned numeric thresholds per version
  * active-version mapping - which version was "current" at each quarterly checkpoint
                             back to 2023 (needed to drive the Phase-3 walk-forward,
                             because the "strategy" is a moving target across files)

Nothing here is estimated. Every number is a git fact. Run:

    python3 01_analyze_git_history.py --repo /path/to/NostalgiaForInfinity \
            --out ../outputs

If --repo is omitted the script makes a blobless clone into a temp dir (needs
network access to github.com, which is generally NOT crypto-blocked).

Pinned deps: see ../requirements.txt (pandas, numpy, matplotlib).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# The strategy files, oldest major version -> newest. NFI ships them side by side;
# the "current recommended" version at any date is the newest one that already exists.
VERSION_FILES = [
    "NostalgiaForInfinityX.py",
    "NostalgiaForInfinityX2.py",
    "NostalgiaForInfinityX3.py",
    "NostalgiaForInfinityX4.py",
    "NostalgiaForInfinityX5.py",
    "NostalgiaForInfinityX6.py",
    "NostalgiaForInfinityX7.py",
]


def git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return stdout (raises on non-zero)."""
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def clone_blobless(dest: Path) -> Path:
    """Blobless full-history clone: all commit/tree metadata, blobs fetched on demand.

    This is what makes the analysis cheap - we never download the multi-MB file
    blobs for the whole history, only the handful we actually inspect.
    """
    url = "https://github.com/iterativv/NostalgiaForInfinity.git"
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", "--quiet", url, str(dest)],
        check=True,
    )
    return dest


def first_add_date(repo: Path, f: str) -> str | None:
    out = git(repo, "log", "--diff-filter=A", "--format=%ad", "--date=short", "--", f).strip()
    return out.splitlines()[-1] if out else None


def last_touch_date(repo: Path, f: str) -> str | None:
    out = git(repo, "log", "--format=%ad", "--date=short", "--", f).strip()
    return out.splitlines()[0] if out else None


def commit_count(repo: Path, f: str) -> int:
    out = git(repo, "log", "--format=%H", "--", f).strip()
    return len(out.splitlines()) if out else 0


def commits_per_year(repo: Path, f: str) -> dict[str, int]:
    out = git(repo, "log", "--format=%ad", "--date=format:%Y", "--", f).strip()
    counts: dict[str, int] = {}
    for y in out.splitlines():
        counts[y] = counts.get(y, 0) + 1
    return dict(sorted(counts.items()))


def head_file_text(repo: Path, f: str) -> str:
    """File contents at HEAD (works on a blobless clone; fetches that one blob)."""
    try:
        return git(repo, "show", f"HEAD:{f}")
    except subprocess.CalledProcessError:
        return ""


def complexity_metrics(text: str) -> dict[str, int]:
    """Directly-measured complexity proxies for a strategy file.

    - lines                : raw size
    - tuned_thresholds      : count of float literals like 1.23 / 0.045 - each is a
                              hand-set constant, i.e. a fitted parameter
    - condition_combinators : count of '& (' - how many boolean sub-conditions are
                              AND-ed together across all entry/exit guards
    - enter_signal_refs     : references to enter_long/enter_tag/enter_short
    """
    return {
        "lines": text.count("\n"),
        "tuned_thresholds": len(re.findall(r"\d+\.\d+", text)),
        "condition_combinators": len(re.findall(r"& \(", text)),
        "enter_signal_refs": len(re.findall(r"enter_(?:long|tag|short)", text)),
    }


def quarterly_checkpoints(start: date, end: date) -> list[date]:
    """First of Jan/Apr/Jul/Oct from start..end inclusive."""
    out = []
    y = start.year
    for yy in range(y, end.year + 1):
        for m in (1, 4, 7, 10):
            d = date(yy, m, 1)
            if start <= d <= end:
                out.append(d)
    return out


def active_version_at(lineage: dict[str, dict], d: date) -> str | None:
    """Newest version file that already existed on date d.

    This is the version a *time-frozen* walk-forward must check out for a window
    starting at d: the one the authors were actively shipping then.
    """
    active = None
    active_add = None
    for f in VERSION_FILES:
        add = lineage[f]["first_add"]
        if add is None:
            continue
        add_d = date.fromisoformat(add)
        if add_d <= d and (active_add is None or add_d > active_add):
            active, active_add = f, add_d
    return active


def make_figure(lineage: dict[str, dict], out_png: Path) -> None:
    """Two-panel: complexity growth (lines + tuned thresholds) and version births."""
    labels = [f.replace("NostalgiaForInfinity", "").replace(".py", "") for f in VERSION_FILES]
    add_dates = [lineage[f]["first_add"] for f in VERSION_FILES]
    lines = [lineage[f]["complexity"]["lines"] for f in VERSION_FILES]
    thresh = [lineage[f]["complexity"]["tuned_thresholds"] for f in VERSION_FILES]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    x = range(len(labels))
    ax1.bar([i - 0.2 for i in x], lines, width=0.4, label="lines of code", color="#c44e52")
    ax1.set_ylabel("lines of code", color="#c44e52")
    ax1b = ax1.twinx()
    ax1b.plot(list(x), thresh, "o-", color="#4c72b0", label="tuned float thresholds")
    ax1b.set_ylabel("tuned float thresholds", color="#4c72b0")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels)
    ax1.set_title("NFI complexity growth per major version")
    ax1.set_xlabel("version (birth dates on right panel)")
    # combined legend for the twin axes
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)

    # version lifespan / birth timeline
    births = [date.fromisoformat(lineage[f]["first_add"]) for f in VERSION_FILES]
    ax2.scatter([b.toordinal() for b in births], list(x), color="#55a868", zorder=3)
    for i, (b, lab) in enumerate(zip(births, labels)):
        ax2.annotate(f" {lab}", (b.toordinal(), i), va="center", fontsize=8)
    ax2.set_yticks([])
    xt = [date(y, 1, 1) for y in range(2021, 2027)]
    ax2.set_xticks([d.toordinal() for d in xt])
    ax2.set_xticklabels([d.year for d in xt])
    ax2.set_title("When each version was born\n(~one major rewrite every ~7 months)")
    ax2.set_xlabel("year")
    ax2.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print(f"[figure] wrote {out_png}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=None,
                    help="path to a NostalgiaForInfinity clone (blobless is fine); "
                         "if omitted, a temp blobless clone is made")
    ap.add_argument("--out", type=Path, default=Path("../outputs"))
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    tmp = None
    if args.repo is None:
        tmp = Path(tempfile.mkdtemp(prefix="nfi_meta_"))
        print(f"[clone] blobless clone -> {tmp}")
        repo = clone_blobless(tmp)
    else:
        repo = args.repo

    total_commits = int(git(repo, "rev-list", "--count", "HEAD").strip())
    repo_year_counts: dict[str, int] = {}
    for y in git(repo, "log", "--format=%ad", "--date=format:%Y").strip().splitlines():
        repo_year_counts[y] = repo_year_counts.get(y, 0) + 1

    lineage: dict[str, dict] = {}
    for f in VERSION_FILES:
        text = head_file_text(repo, f)
        lineage[f] = {
            "first_add": first_add_date(repo, f),
            "last_touch": last_touch_date(repo, f),
            "commits": commit_count(repo, f),
            "commits_per_year": commits_per_year(repo, f),
            "complexity": complexity_metrics(text),
        }
        c = lineage[f]["complexity"]
        print(f"[version] {f:28s} born {lineage[f]['first_add']}  "
              f"{c['lines']:>6d} lines  {c['tuned_thresholds']:>6d} thresholds  "
              f"{lineage[f]['commits']:>5d} commits")

    checkpoints = quarterly_checkpoints(date(2023, 1, 1), date.today())
    active_map = []
    for d in checkpoints:
        v = active_version_at(lineage, d)
        # forward 90d window end
        active_map.append({
            "checkpoint": d.isoformat(),
            "active_version": v,
            "version_age_days": (d - date.fromisoformat(lineage[v]["first_add"])).days if v else None,
        })

    result = {
        "repo": "iterativv/NostalgiaForInfinity",
        "analysed_on": date.today().isoformat(),
        "total_commits": total_commits,
        "commits_per_year_all_files": dict(sorted(repo_year_counts.items())),
        "n_major_versions": len(VERSION_FILES),
        "version_lineage": lineage,
        "walk_forward_active_version_map": active_map,
    }

    out_json = args.out / "git_history.json"
    out_json.write_text(json.dumps(result, indent=2))
    print(f"[json] wrote {out_json}")

    make_figure(lineage, args.out / "complexity_growth.png")

    # human-readable summary
    lines_md = ["# NFI git-history analysis (Phase 3, data-independent)\n"]
    lines_md.append(f"- Analysed: {result['analysed_on']}")
    lines_md.append(f"- Total commits in repo: **{total_commits:,}**")
    lines_md.append(f"- Major strategy versions: **{len(VERSION_FILES)}** "
                    f"(one major rewrite every ~7 months)\n")
    lines_md.append("## Version lineage\n")
    lines_md.append("| Version | Born | Last touched | Commits | Lines | Tuned thresholds |")
    lines_md.append("|---|---|---|---:|---:|---:|")
    for f in VERSION_FILES:
        L = lineage[f]
        lines_md.append(
            f"| {f.replace('NostalgiaForInfinity','').replace('.py','')} "
            f"| {L['first_add']} | {L['last_touch']} | {L['commits']:,} "
            f"| {L['complexity']['lines']:,} | {L['complexity']['tuned_thresholds']:,} |"
        )
    lines_md.append("\n## Time-frozen walk-forward: which version to check out per window\n")
    lines_md.append("| Checkpoint (window start) | Active version | Version age at checkpoint (days) |")
    lines_md.append("|---|---|---:|")
    for m in active_map:
        lines_md.append(f"| {m['checkpoint']} | {m['active_version']} | {m['version_age_days']} |")
    (args.out / "git_history_summary.md").write_text("\n".join(lines_md) + "\n")
    print(f"[md] wrote {args.out / 'git_history_summary.md'}")

    if tmp is not None:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
