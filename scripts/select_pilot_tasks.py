#!/usr/bin/env python3
"""Pick the pilot task set from public data only — no compute spent.

Strategy (from the audit plan): don't sample at random. Take tasks the
published leaderboard runs all pass, tasks they all fail, and tasks they split
on. The third group is where run-to-run instability is most likely to show up;
the first two are controls.

Important caveat, repeated in the output: cross-model disagreement is not the
same thing as within-model run-to-run variance. It is a *prior* over where to
look, nothing more.

Inputs : data/public_matrix.json (scripts/build_public_matrix.py)
         data/tasks.json         (scripts/extract_task_registry.py)
Output : data/pilot_tasks.json + one .txt task list per bucket
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default="data/public_matrix.json")
    ap.add_argument("--tasks", default="data/tasks.json")
    ap.add_argument("--per-bucket", type=int, default=10)
    ap.add_argument("--suite", default="gui_only",
                    choices=["gui_only", "user_interaction", "mcp", "any"],
                    help="gui_only avoids needing a user-agent LLM or MCP keys")
    ap.add_argument("--min-models", type=int, default=10,
                    help="Ignore tasks scored by fewer than this many published runs")
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()

    matrix = json.loads(Path(args.matrix).read_text())
    registry = json.loads(Path(args.tasks).read_text())
    tasks = matrix["tasks"]

    def eligible(name: str) -> bool:
        meta = registry.get(name)
        if meta is None:
            return False
        if args.suite != "any" and meta["suite"] != args.suite:
            return False
        return tasks[name]["n_models_scored"] >= args.min_models

    pool = [t for t in tasks if eligible(t)]

    def entry(name: str) -> dict:
        rec = tasks[name]
        meta = registry[name]
        return {
            "task_id": name,
            "group": meta["group"],
            "suite": meta["suite"],
            "randomized_setup": meta["randomized_setup"],
            "unseeded_random_calls": meta["unseeded_random_calls"],
            "public_n_models": rec["n_models_scored"],
            "public_n_pass": rec["n_pass"],
            "public_pass_fraction": rec["pass_fraction"],
            "public_steps_min": rec["steps_min"],
            "public_steps_max": rec["steps_max"],
            "goal": (meta["goal"] or "")[:160] or None,
        }

    # Split: closest to 50/50 first; ties broken by how many runs saw the task,
    # then by name so the selection is deterministic.
    split = sorted(
        (t for t in pool if 0.0 < tasks[t]["pass_fraction"] < 1.0),
        key=lambda t: (abs(tasks[t]["pass_fraction"] - 0.5),
                       -tasks[t]["n_models_scored"], t),
    )
    all_pass = sorted((t for t in pool if tasks[t]["pass_fraction"] == 1.0),
                      key=lambda t: (-tasks[t]["n_models_scored"], t))
    all_fail = sorted((t for t in pool if tasks[t]["pass_fraction"] == 0.0),
                      key=lambda t: (-tasks[t]["n_models_scored"], t))

    buckets = {
        "split": [entry(t) for t in split[: args.per_bucket]],
        "all_pass": [entry(t) for t in all_pass[: args.per_bucket]],
        "all_fail": [entry(t) for t in all_fail[: args.per_bucket]],
    }

    out = {
        "selection": {
            "suite": args.suite,
            "per_bucket": args.per_bucket,
            "min_models": args.min_models,
            "pool_size": len(pool),
            "available": {k: v for k, v in
                          [("split", len(split)), ("all_pass", len(all_pass)),
                           ("all_fail", len(all_fail))]},
        },
        "caveat": "Cross-model disagreement is a prior for where to look, not a "
                  "measurement of within-model run-to-run variance.",
        "buckets": buckets,
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "pilot_tasks.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))

    allsel = []
    for bucket, rows in buckets.items():
        names = [r["task_id"] for r in rows]
        (outdir / f"pilot_{bucket}.txt").write_text("\n".join(names) + "\n")
        allsel += names
    (outdir / "pilot_all.txt").write_text("\n".join(allsel) + "\n")

    print(f"pool ({args.suite}, >={args.min_models} published runs): {len(pool)} tasks")
    for bucket, rows in buckets.items():
        avail = out["selection"]["available"][bucket]
        print(f"\n--- {bucket}  ({len(rows)} chosen of {avail} available) ---")
        for r in rows:
            flag = "  [randomized setup]" if r["randomized_setup"] else ""
            print(f"  {r['task_id']:48s} {r['public_n_pass']:>2}/{r['public_n_models']:<2} "
                  f"{r['group']:9s} steps {r['public_steps_min']}-{r['public_steps_max']}{flag}")
    print(f"\nwrote {outdir/'pilot_tasks.json'} and pilot_*.txt "
          f"({len(allsel)} tasks total)")


if __name__ == "__main__":
    main()
