#!/usr/bin/env python3
"""Refresh the threshold-dependent fields of data/sensitivity_round6.json.

    python3 scripts/refresh_sensitivity.py

The sensitivity file compares data/analysis.json (rounds 4-10, with round 6)
with data/analysis_headline.json (without it). Only the threshold and what
depends on it are rewritten here; every other field is checked against the two
analysis files and the script stops if one disagrees, so a stale file cannot be
half-refreshed.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "sensitivity_round6.json"


def arm(name):
    return json.loads((ROOT / "data" / name).read_text())["arms"]["A_temp0"]


def check(side, a):
    fixed = a["gui_only_extrapolation"]["fixed_taskset"]
    expect = {
        "suite_scores_pct": [t["score_pct"] for t in a["per_trial_suite_score"]["per_trial"]],
        "extrapolated_117_width_pp": fixed["ci95_width_pp"],
        "n_flipped": a["flips"]["n_flipped"],
        "absorbed_retries": a["absorbed_retries"]["total"],
    }
    bad = {k: (side[k], v) for k, v in expect.items() if side.get(k) != v}
    return bad


def main():
    s = json.loads(PATH.read_text())
    sides = {"with_round6": arm("analysis.json"), "without_round6": arm("analysis_headline.json")}
    for key, a in sides.items():
        bad = check(s[key], a)
        if bad:
            print(f"{key}: fields disagree with the analysis files, refusing to refresh: {bad}", file=sys.stderr)
            return 1
        dd = a["detectable_difference"]
        s[key]["extrapolated_117_sd_pp_exact"] = a["gui_only_extrapolation"]["fixed_taskset"]["sd_pp_exact"]
        s[key]["mdd95_pp"] = dd["mdd95_pp"]
        s[key]["mdd95_power80_pp"] = dd["mdd95_power80_pp"]
        s[key]["leaderboard_adjacent_within_noise"] = a["leaderboard_gaps"]["n_adjacent_within_noise"]
    s["deltas"]["mdd95_pp"] = round(s["without_round6"]["mdd95_pp"] - s["with_round6"]["mdd95_pp"], 4)
    s["threshold_note"] = ("mdd95_pp is computed from the exact Bernoulli SD (extrapolated_117_sd_pp_exact). "
                           "extrapolated_117_sd_pp is the simulated SD, kept for the interval; the submitted "
                           "version used it for the threshold (8.1524 without round 6).")
    PATH.write_text(json.dumps(s, indent=1, ensure_ascii=False) + "\n")
    print({k: (s[k]["mdd95_pp"], s[k]["leaderboard_adjacent_within_noise"]) for k in sides}, "delta", s["deltas"]["mdd95_pp"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
