#!/usr/bin/env python3
"""Build a cross-model per-task outcome matrix from MobileWorld's published trajectories.

This uses only data shipped in the public MobileWorld repo
(`site/trajs/*.json.gz` + `site/leaderboard.json`). It does not run anything.

Purpose: pick the pilot task set for the stability audit without burning any
compute, and record what the public data already tells us about which tasks
different systems disagree on.

Output: data/public_matrix.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter
from pathlib import Path

# Upstream's own pass threshold. src/mobile_world/core/log_viewer/utils.py:489
# and generate_pass_k_report() in core/subcommands/eval.py both use score > 0.99.
PASS_THRESHOLD = 0.99

SCORE_RE = re.compile(r"score:\s*([-\d.eE]+)")


LENIENT = False


def parse_result_lenient(result) -> tuple[float | None, str | None]:
    """Three of the 18 published bundles carry results in formats the strict
    mirror does not read: 'score:' on the second line inside a chunked-transfer
    wrapper ('25\\nscore: 1.0\\nreason: ...\\n0\\nx-amz-checksum-...'), or a bare
    float followed by the reason ('1.0\\nCorrect email sent'). Read leniently,
    all three reproduce their leaderboard numbers exactly (checked 2026-09-03).
    Strict remains the default so that the as-submitted data/public_matrix.json
    is reproducible; --lenient writes the corrected matrix."""
    if not isinstance(result, str):
        return None, None
    m = re.search(r"score:\s*([-\d.eE]+)", result)
    if m:
        r = re.search(r"reason:\s*(.*)", result)
        try:
            return float(m.group(1)), (r.group(1).strip() if r else None)
        except ValueError:
            return None, None
    lines = [l for l in result.splitlines() if l.strip()]
    try:
        return float(lines[0]), (lines[1].strip() if len(lines) > 1 else None)
    except (ValueError, IndexError):
        return None, None


def parse_result(result) -> tuple[float | None, str | None]:
    """Mirror mobile_world.runtime.client.parse_result_file, but for the bundled string."""
    if LENIENT:
        return parse_result_lenient(result)
    if not isinstance(result, str):
        return None, None
    lines = result.splitlines()
    score = None
    if lines:
        m = SCORE_RE.search(lines[0])
        if m:
            try:
                score = float(m.group(1))
            except ValueError:
                score = None
    reason = lines[1].strip() if len(lines) > 1 else None
    if reason and reason.startswith("reason:"):
        reason = reason[len("reason:"):].strip()
    return score, reason


def load_bundle(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--site",
        default="vendor/MobileWorld/site",
        help="Path to the upstream site/ directory",
    )
    ap.add_argument("--out", default="data/public_matrix.json")
    ap.add_argument("--lenient", action="store_true",
                    help="also read the two non-standard result formats (see parse_result_lenient)")
    args = ap.parse_args()

    site = Path(args.site)
    global LENIENT
    LENIENT = args.lenient
    lb = json.loads((site / "leaderboard.json").read_text())

    # traj_file -> leaderboard entry, so we can name bundles by their leaderboard model
    by_traj = {}
    for entry in lb["results"]:
        tf = entry.get("traj_file")
        if tf:
            by_traj[Path(tf).name] = entry

    models: dict[str, dict] = {}
    tasks: dict[str, dict] = {}

    for gz in sorted((site / "trajs").glob("*.json.gz")):
        entry = by_traj.get(gz.name)
        label = entry["model"] if entry else gz.name[: -len(".json.gz")]
        bundle = load_bundle(gz)

        scored = 0
        passed = 0
        unparsed = 0
        for task_name, payload in bundle.items():
            score, reason = parse_result(payload.get("result"))
            traj = payload.get("traj") or []
            rec = tasks.setdefault(task_name, {"models": {}})
            rec["models"][label] = {
                "score": score,
                "pass": (score is not None and score > PASS_THRESHOLD),
                "steps": len(traj),
                "reason": reason,
                "total_tokens": (payload.get("token_usage") or {}).get("total_tokens"),
            }
            if score is None:
                unparsed += 1
            else:
                scored += 1
                if score > PASS_THRESHOLD:
                    passed += 1

        models[label] = {
            "bundle": gz.name,
            "tasks_in_bundle": len(bundle),
            "tasks_with_score": scored,
            "tasks_passed": passed,
            "tasks_without_score": unparsed,
            "leaderboard_runs": entry.get("runs") if entry else None,
            "leaderboard_gui_only": entry.get("gui_only") if entry else None,
            "leaderboard_user_int": entry.get("user_int") if entry else None,
            "agent_type": entry.get("agent_type") if entry else None,
            "leaderboard_notes": (entry.get("notes") if entry else None),
            "organization": entry.get("organization") if entry else None,
        }

    # Per-task aggregation across models
    for task_name, rec in tasks.items():
        outcomes = [m["pass"] for m in rec["models"].values() if m["score"] is not None]
        rec["n_models_scored"] = len(outcomes)
        rec["n_pass"] = sum(outcomes)
        rec["n_fail"] = len(outcomes) - sum(outcomes)
        rec["pass_fraction"] = (sum(outcomes) / len(outcomes)) if outcomes else None
        # Partial (non-binary) scores are a separate signal from pass/fail disagreement.
        rec["distinct_scores"] = sorted(
            {m["score"] for m in rec["models"].values() if m["score"] is not None}
        )
        rec["has_partial_score"] = any(
            s is not None and 0.0 < s <= PASS_THRESHOLD for s in rec["distinct_scores"]
        )
        steps = [m["steps"] for m in rec["models"].values() if m["steps"]]
        rec["steps_min"] = min(steps) if steps else None
        rec["steps_max"] = max(steps) if steps else None

    # Pilot buckets, by cross-model agreement on the published single runs.
    scored_tasks = {k: v for k, v in tasks.items() if v["n_models_scored"] >= 5}
    all_pass = sorted(k for k, v in scored_tasks.items() if v["pass_fraction"] == 1.0)
    all_fail = sorted(k for k, v in scored_tasks.items() if v["pass_fraction"] == 0.0)
    split = sorted(
        scored_tasks,
        key=lambda k: (abs(scored_tasks[k]["pass_fraction"] - 0.5), k),
    )

    out = {
        "provenance": {
            "source": "MobileWorld public repo site/trajs + site/leaderboard.json",
            "pass_threshold": PASS_THRESHOLD,
            "note": "Each published bundle is a single run per model unless the "
                    "leaderboard entry says otherwise (leaderboard_runs).",
        },
        "leaderboard_task_counts": lb.get("task_counts"),
        "leaderboard_runs_distribution": dict(
            Counter(str(e.get("runs")) for e in lb["results"])
        ),
        "models": models,
        "tasks": tasks,
        "buckets": {
            "all_models_pass": all_pass,
            "all_models_fail": all_fail,
            "most_split": split[:40],
        },
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"models: {len(models)}   tasks seen: {len(tasks)}")
    print(f"tasks scored by >=5 models: {len(scored_tasks)}")
    print(f"  all models pass : {len(all_pass)}")
    print(f"  all models fail : {len(all_fail)}")
    print(f"  mixed           : {len(scored_tasks) - len(all_pass) - len(all_fail)}")
    print(f"wrote {outp}")


if __name__ == "__main__":
    main()
