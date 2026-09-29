#!/usr/bin/env python3
"""Extract per-step token statistics from raw trajectories -> data/traj_stats.json.

run_matrix.py records one `total_tokens` per run, which is dominated by the
prompt (98.5% measured) and therefore says nothing about how much the model
actually reasoned. Reasoning length is `completion_tokens / steps`, and it turned
out to matter: it is the quantity that exposed a serving-provider configuration
change in the middle of Arm A (paper, Section 4).

Kept out of analyze.py on purpose. analyze.py's contract is "reads runs.jsonl,
runs anywhere"; this one needs the raw artifact tree, which normally lives only
on the machine that produced it.

    python3 scripts/scan_trajectories.py --runs-dir runs --out data/traj_stats.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIAL_RE = re.compile(r"trial_(\d+)")


def summarize(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 2),
        "median": round(statistics.median(xs), 2),
        "min": round(xs[0], 2),
        "max": round(xs[-1], 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--arm", default=None, help="Restrict to one arm")
    ap.add_argument("--out", default=str(ROOT / "data" / "traj_stats.json"))
    args = ap.parse_args()

    runs = Path(args.runs_dir)
    if not runs.is_dir():
        print(f"{runs} is not a directory -- run this where the artifacts are "
              f"(normally the evaluation host).")
        return 2

    per_run: list[dict] = []
    for traj in runs.glob("*/*/trial_*/*/traj.json"):
        arm = traj.relative_to(runs).parts[0]
        if args.arm and arm != args.arm:
            continue
        m = TRIAL_RE.search(str(traj))
        try:
            episode = json.loads(traj.read_text()).get("0") or {}
        except (json.JSONDecodeError, OSError):
            continue
        steps = len(episode.get("traj") or [])
        usage = episode.get("token_usage") or {}
        comp = usage.get("completion_tokens")
        if not steps or not comp:
            continue
        per_run.append({
            "arm": arm,
            "task_id": traj.parent.name,
            "trial": int(m.group(1)) if m else None,
            "steps": steps,
            "completion_tokens": comp,
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_tokens": usage.get("cached_tokens"),
            "completion_per_step": round(comp / steps, 3),
            # A backup directory means the harness silently restarted this run.
            "is_backup": "_backup_" in str(traj),
        })

    by_arm: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in per_run:
        if r["trial"] is not None and not r["is_backup"]:
            by_arm[r["arm"]][r["trial"]].append(r["completion_per_step"])

    out = {
        "generator": "scripts/scan_trajectories.py",
        "note": "completion_per_step is how much the model reasoned per action. "
                "A step change in it across trials is the signature of the "
                "serving endpoint changing configuration, which no flag pins "
                "and no leaderboard entry records.",
        "n_runs": len(per_run),
        "by_arm": {
            arm: {
                "per_trial_completion_per_step": {
                    str(t): summarize(v) for t, v in sorted(trials.items())},
            } for arm, trials in sorted(by_arm.items())
        },
        "runs": per_run,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    for arm, d in out["by_arm"].items():
        print(f"\n=== {arm} — reasoning tokens per action ===")
        for t, s in d["per_trial_completion_per_step"].items():
            print(f"  trial {t:>2}  n={s['n']:>3}  median={s['median']:>7.1f}  "
                  f"mean={s['mean']:>7.1f}")
    print(f"\nwrote {args.out}  ({len(per_run)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
