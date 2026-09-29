#!/usr/bin/env python3
"""Rank individual runs that are worth opening by hand, looking for scoring bugs.

The variance measurement says *that* a task flipped. It cannot say why. Some
flips will turn out not to be environment noise at all but the checker getting
it wrong -- the agent visibly did the task and was scored 0, or did not and was
scored 1. Those are a separate finding, and a stronger one, because anybody can
verify them from the artifacts.

Opening every run is not practical, so this ranks them. The ordering is a
prior, not a verdict: everything it surfaces still has to be looked at, and
report/checker_issues.md records the human judgement, not this script's.

What gets ranked up, in order of how often it pays off:

  minority outcome   the one trial out of ten that disagreed with the rest, in
                     a task that flipped. Whatever happened, it happened here.
  suspicious pass    scored 1 in far fewer steps than any published run of the
                     same task needed. Passing without doing the work is what a
                     false positive looks like from the outside.
  truncated run      steps pinned at --max-round: the agent ran out of budget,
                     so a 0 measures the budget, not the agent.
  degenerate run     scored after almost no steps at all.
  absorbed retry     the harness silently restarted this run (ENVIRONMENT.md §2.2).
  no score           finished without writing result.txt.

Usage:
    python3 scripts/find_candidates.py                  # top 25
    python3 scripts/find_candidates.py --top 60 --arm A_temp0
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Below this many steps a run cannot plausibly have done a multi-app task.
DEGENERATE_STEPS = 3

# Directories that exist at the repository root. A recorded path is anchored on
# whichever of these it contains, rather than on this machine's layout.
_ANCHORS = ("runs", "data", "agents", "scripts", "report", "vendor")


def repo_relative(value):
    """Turn a path recorded on another machine into a repository-relative one.

    run_matrix.py records absolute paths, and the machine that runs the matrix
    is usually not the machine that analyses it -- the evaluation needs a Linux
    host with /dev/kvm, the analysis runs anywhere. So `relative_to(ROOT)`
    fails on exactly the normal workflow, and falling back to the basename
    throws away the arm and the trial, which is most of what makes the path
    useful. Anchor on the first known repository directory instead.
    """
    if not isinstance(value, str) or "/" not in value:
        return value
    parts = [p for p in value.split("/") if p]
    for i, part in enumerate(parts):
        if part in _ANCHORS:
            return "/".join(parts[i:])
    return "/".join(parts[-3:]) if len(parts) > 3 else "/".join(parts)


def load_pilot(path: Path) -> dict:
    if not path.exists():
        return {}
    pilot = json.loads(path.read_text())
    out = {}
    for bucket, items in pilot["buckets"].items():
        for it in items:
            out[it["task_id"]] = {**it, "bucket": bucket}
    return out


def candidates_for_task(task: dict, meta: dict, max_round: int | None) -> list[dict]:
    trials = [t for t in task["trial_outcomes"]]
    scored = [t for t in trials if t["pass"] is not None]
    outcome_counts = Counter(t["pass"] for t in scored)
    n = len(scored)
    pub_min = (meta.get(task["task_id"]) or {}).get("public_steps_min")

    out = []
    for t in trials:
        reasons, score = [], 0.0

        if t["pass"] is not None and n >= 2:
            share = outcome_counts[t["pass"]] / n
            if share < 0.5:
                # The rarer the disagreement, the more likely it is a specific
                # cause rather than a coin flip.
                score += 40 * (task["flip_rate"] or 0) * (1 - share)
                reasons.append(
                    f"minority outcome: {'passed' if t['pass'] else 'failed'} in "
                    f"{outcome_counts[t['pass']]} of {n} scored trials")
            elif share == 0.5:
                score += 20 * (task["flip_rate"] or 0)
                reasons.append(f"even split: {n} trials, {outcome_counts[True]} passed")

        steps = t.get("steps")
        if t["pass"] and isinstance(steps, int):
            if isinstance(pub_min, int) and steps < pub_min:
                score += 25 + min(15, pub_min - steps)
                reasons.append(
                    f"passed in {steps} steps; no published run of this task "
                    f"finished in fewer than {pub_min}")
            if steps <= DEGENERATE_STEPS:
                score += 30
                reasons.append(f"passed after only {steps} steps")

        if isinstance(steps, int) and max_round and steps >= max_round:
            score += 12
            reasons.append(
                f"steps pinned at --max-round ({max_round}): the run was cut "
                f"off, so a 0 here measures the round budget")

        if t.get("outcome") and t["outcome"] != "scored":
            score += 8
            reasons.append(f"outcome={t['outcome']}: no score was produced")

        if reasons:
            out.append({
                "priority": round(score, 3),
                "task_id": task["task_id"],
                "bucket": task["bucket"],
                "trial": t["trial"],
                "run_id": t["run_id"],
                "outcome": t["outcome"],
                "score": t["score"],
                "pass": t["pass"],
                "steps": steps,
                "reason_text": t.get("reason"),
                "task_pass_rate": task["pass_rate"],
                "task_flip_rate": task["flip_rate"],
                "public_steps_min": pub_min,
                "why": reasons,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", default=str(ROOT / "data" / "analysis.json"))
    ap.add_argument("--runs", default=str(ROOT / "data" / "runs.jsonl"),
                    help="Only used to recover each run's artifact directory.")
    ap.add_argument("--pilot", default=str(ROOT / "data" / "pilot_tasks.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "checker_candidates.json"))
    ap.add_argument("--arm", default=None)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    apath = Path(args.analysis)
    if not apath.exists():
        print(f"{apath} does not exist -- run scripts/analyze.py first.")
        return 2
    analysis = json.loads(apath.read_text())
    arms = analysis.get("arms") or {}
    if not arms:
        print("analysis.json contains no arms")
        return 2
    name = args.arm or sorted(arms)[0]
    if name not in arms:
        print(f"arm {name!r} not in {apath}; have {sorted(arms)}")
        return 2
    arm = arms[name]

    # traj_path is per row and not carried into analysis.json, so the paths are
    # looked up here rather than duplicated there.
    traj_by_run = {}
    runs_path = Path(args.runs)
    if runs_path.exists():
        for line in runs_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("run_id"):
                traj_by_run[r["run_id"]] = r.get("traj_path")

    meta = load_pilot(Path(args.pilot))
    max_round = arm["provenance"]["values"].get("max_round")

    rows = []
    for task in arm["tasks"]:
        rows += candidates_for_task(task, meta, max_round)
    rows.sort(key=lambda r: (-r["priority"], r["task_id"], r["trial"] or 0))
    for r in rows:
        r["traj_path"] = repo_relative(traj_by_run.get(r["run_id"]))

    out = {
        "generated_at": analysis["generated_at"],
        "source_analysis": str(apath),
        "runs_sha256": analysis["inputs"]["runs_sha256"],
        "arm": name,
        "n_candidates": len(rows),
        "ranking_note": "A prior over where to look, not a verdict. Every entry "
                        "still has to be judged by hand; the judgement goes in "
                        "report/checker_issues.md.",
        "candidates": rows,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    print(f"{len(rows)} candidate runs in arm {name}; top {min(args.top, len(rows))}:\n")
    for r in rows[:args.top]:
        head = (f"{r['priority']:6.1f}  {r['task_id']}  trial {r['trial']}  "
                f"score={r['score']}  steps={r['steps']}")
        print(head)
        for w in r["why"]:
            print(f"          - {w}")
        if r["traj_path"]:
            print(f"          {r['traj_path']}/traj.json")
            print(f"          {r['traj_path']}/screenshots/")
        print()
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
