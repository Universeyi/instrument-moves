#!/usr/bin/env python3
"""Forensic signals over the published trajectory bundles, computed from
data/public_matrix.json alone. Zero compute, zero inference.

Each signal is something a reader could check from the public repository. A
signal that fires is a lead, not a finding: it says where to open trajectories
by hand. A signal that does not fire is recorded too, so that the absence is
not mistaken for "not checked".

    python3 scripts/forensic_signals.py            # -> data/forensic_signals.json
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAP = 50
FAST = 3


def main() -> int:
    pm = json.loads((ROOT / "data" / "public_matrix.json").read_text())
    reg = json.loads((ROOT / "data" / "tasks.json").read_text())
    tasks, models = pm["tasks"], pm["models"]

    runs = [(t, m, x) for t, r in tasks.items() for m, x in r["models"].items()]
    stepped = [(t, m, x) for t, m, x in runs if x.get("steps") is not None]
    scored = [(t, m, x) for t, m, x in runs if x.get("score") is not None]

    # S1 -- the round cap. Already in the paper (section 6); restated here so
    # the file is complete on its own.
    at_cap = [(t, m, x) for t, m, x in stepped if x["steps"] == CAP]
    s1 = {
        "runs_with_steps": len(stepped),
        "at_exactly_cap": len(at_cap),
        "at_cap_scored_zero": sum(1 for _, _, x in at_cap if x.get("score") == 0.0),
        "at_cap_passed": sum(1 for _, _, x in at_cap if x.get("pass")),
        "neighbourhood": {k: sum(1 for _, _, x in stepped if x["steps"] == k) for k in range(CAP - 2, CAP + 5)},
        "per_model_cap_rate": {},
    }
    tot, cap = Counter(), Counter()
    for t, m, x in stepped:
        tot[m] += 1
        cap[m] += x["steps"] >= CAP
    for m in sorted(tot, key=lambda m: -cap[m] / tot[m]):
        s1["per_model_cap_rate"][m] = {"at_cap": cap[m], "n": tot[m], "pct": round(100 * cap[m] / tot[m], 1)}
    rates = [v["pct"] for v in s1["per_model_cap_rate"].values()]
    s1["cap_rate_range_pct"] = [min(rates), max(rates)]

    # S2 -- passes in very few steps. A pass at <=3 steps on a task that needs
    # UI navigation would suggest the verifier accepted an unfinished run. On
    # this benchmark every such pass is on an answer-type task ("return the
    # number"), where two steps is a plausible path. Recorded as NOT firing.
    fast = defaultdict(list)
    for t, m, x in stepped:
        if x.get("pass") and x["steps"] <= FAST:
            fast[t].append({"model": m, "steps": x["steps"]})
    s2 = {"threshold_steps": FAST, "n_fast_passes": sum(len(v) for v in fast.values()), "by_task": {}}
    for t, v in sorted(fast.items(), key=lambda kv: -len(kv[1])):
        allsteps = [x["steps"] for x in tasks[t]["models"].values() if x.get("steps") is not None]
        s2["by_task"][t] = {
            "fast_passes": len(v),
            "passes_total": sum(1 for x in tasks[t]["models"].values() if x.get("pass")),
            "min_steps_any_model": min(allsteps),
            "goal": reg.get(t, {}).get("goal", ""),
            "runs": v,
        }
    s2["fires"] = False
    s2["reading"] = ("All fast passes are on answer-type tasks (the goal asks for a number or a value), "
                     "where a 2-3 step path is plausible. No lead.")

    # S3 -- bundles whose results the strict parser does not read. First seen as
    # "3 of 18 bundles carry no verdict"; on inspection the verdicts are there in
    # two non-standard formats (a chunked-transfer wrapper around the score
    # lines, and a bare float followed by the reason). The strict matrix is what
    # the paper used; data/public_matrix.lenient.json reads both formats.
    # Read leniently, all three reproduce their leaderboard numbers exactly. So
    # this signal fires against OUR pipeline, not against the benchmark, and the
    # paper's "90 carry no recorded score at all" (section 6) is a parser artefact.
    lenient_path = ROOT / "data" / "public_matrix.lenient.json"
    lenient = json.loads(lenient_path.read_text()) if lenient_path.exists() else None
    s3 = {"models_unparsed_strict": {}, "lenient_matrix_present": lenient is not None}
    for m, v in models.items():
        if v["tasks_in_bundle"] and v["tasks_without_score"] == v["tasks_in_bundle"]:
            s3["models_unparsed_strict"][m] = {"tasks_in_bundle": v["tasks_in_bundle"],
                                               "leaderboard_gui_only": v.get("leaderboard_gui_only")}
    if lenient:
        s3["after_lenient_parse"] = {m: {"tasks_without_score": lenient["models"][m]["tasks_without_score"]}
                                     for m in s3["models_unparsed_strict"]}
        lstepped = [x for r in lenient["tasks"].values() for x in r["models"].values() if x.get("steps") is not None]
        lcap = [x for x in lstepped if x["steps"] == CAP]
        s3["round_cap_corrected"] = {
            "at_exactly_cap": len(lcap),
            "at_cap_scored_zero": sum(1 for x in lcap if x.get("score") == 0.0),
            "at_cap_passed": sum(1 for x in lcap if x.get("pass")),
            "at_cap_unscored": sum(1 for x in lcap if x.get("score") is None),
            "paper_as_submitted": {"at_cap_not_passed": 497, "pct": 94.7, "scored_zero": 407, "unscored": 90},
        }
        s3["bucket_changes"] = {b: {"left": sorted(set(pm["buckets"][b]) - set(lenient["buckets"][b])),
                                    "joined": sorted(set(lenient["buckets"][b]) - set(pm["buckets"][b]))}
                                for b in pm["buckets"]}
    s3["fires"] = "pipeline"
    s3["reading"] = ("3 of 18 bundles are unreadable to the strict parser but carry verdicts in two other formats; "
                     "read leniently they reproduce their leaderboard numbers exactly. A correction to our own "
                     "matrix, not a finding about the benchmark.")

    # S6 -- does each published bundle reproduce its own leaderboard number?
    # Pass rate over the bundle's GUI-only tasks (and User-Interaction tasks)
    # against the board's figure for the same entry. Needs the lenient matrix,
    # otherwise three entries cannot be checked at all.
    s6 = {"entries": {}, "note": "delta = bundle - board, percentage points; unscored tasks are left out of the bundle rate"}
    src = lenient or pm
    suite = {t: v.get("suite") for t, v in reg.items()}
    for m, v in src["models"].items():
        acc = {}
        for t, r in src["tasks"].items():
            x = r["models"].get(m)
            if x and x.get("score") is not None:
                a = acc.setdefault(suite.get(t), [0, 0]); a[0] += bool(x["pass"]); a[1] += 1
        g, u = acc.get("gui_only", [0, 0]), acc.get("user_interaction", [0, 0])
        gb = round(100 * g[0] / g[1], 1) if g[1] else None
        ub = round(100 * u[0] / u[1], 1) if u[1] else None
        lg, lu = v.get("leaderboard_gui_only"), v.get("leaderboard_user_int")
        s6["entries"][m] = {
            "leaderboard_runs": v.get("leaderboard_runs"),
            "leaderboard_notes": v.get("leaderboard_notes"),
            "gui_only": {"bundle_pass": g[0], "bundle_scored": g[1], "bundle_pct": gb, "board_pct": lg,
                         "delta": round(gb - lg, 1) if (gb is not None and lg is not None) else None},
            "user_interaction": {"bundle_pass": u[0], "bundle_scored": u[1], "bundle_pct": ub, "board_pct": lu,
                                 "delta": round(ub - lu, 1) if (ub is not None and lu is not None) else None},
        }
    big = {m: e["gui_only"]["delta"] for m, e in s6["entries"].items()
           if e["gui_only"]["delta"] is not None and abs(e["gui_only"]["delta"]) >= 2.0
           and e["gui_only"]["bundle_scored"] >= 115}
    s6["entries_off_by_2pp_or_more_with_full_bundle"] = big
    s6["disclosed_upstream"] = {m: src["models"][m].get("leaderboard_notes") for m in big
                                if src["models"][m].get("leaderboard_notes")}
    s6["fires"] = bool(big)
    s6["reading"] = (f"{len(big)} entry(ies) with a fully scored GUI-only bundle differ from the board by 2 points or more: {big}. "
                     f"Of these, {len(s6['disclosed_upstream'])} carry an upstream note explaining the gap (a different "
                     "configuration than the one scored; the original logs were lost). Small deltas elsewhere are a few "
                     "unscored tasks counted as fails on the board.")

    # S4 -- verdicts with no reason. A pass whose result.txt carries no
    # explanation cannot be re-adjudicated from the bundle: the reader has the
    # agent's actions and a bare 1.0. Counted per task so that tasks whose
    # verifier never explains itself are visible.
    def noreason(x):
        r = x.get("reason")
        return (not r) or r.startswith("No reason provided")
    s4 = {
        "passes_without_reason": sum(1 for _, _, x in scored if x.get("pass") and noreason(x)),
        "passes_total": sum(1 for _, _, x in scored if x.get("pass")),
        "fails_without_reason": sum(1 for _, _, x in scored if not x.get("pass") and noreason(x)),
        "fails_total": sum(1 for _, _, x in scored if not x.get("pass")),
    }
    per_task = defaultdict(lambda: [0, 0])
    for t, m, x in scored:
        if x.get("pass"):
            per_task[t][1] += 1
            per_task[t][0] += noreason(x)
    s4["tasks_whose_passes_never_carry_a_reason"] = sorted(t for t, (nr, n) in per_task.items() if n and nr == n)
    s4["n_such_tasks"] = len(s4["tasks_whose_passes_never_carry_a_reason"])
    s4["fires"] = s4["passes_without_reason"] > 0
    s4["reading"] = (f"{s4['passes_without_reason']} of {s4['passes_total']} published passes carry no verifier reason; "
                     f"on {s4['n_such_tasks']} tasks no pass ever does.")

    # S5 -- timestamps. Not recorded anywhere upstream; the bundle has actions
    # and a result string. Batch structure cannot be recovered on this benchmark.
    s5 = {"fires": None, "reading": "Upstream records no timestamps per run or per step; published bundles carry "
                                     "trajectory and result only. The timestamp-clustering signal is not "
                                     "computable on this benchmark."}

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/forensic_signals.py",
        "inputs": {"public_matrix": "data/public_matrix.json", "public_matrix_lenient": "data/public_matrix.lenient.json", "task_registry": "data/tasks.json"},
        "signals": {
            "S1_round_cap": s1,
            "S2_fast_passes": s2,
            "S3_bundles_without_verdicts": s3,
            "S4_verdicts_without_reason": s4,
            "S5_timestamps": s5,
            "S6_bundle_vs_board": s6,
        },
    }
    (ROOT / "data" / "forensic_signals.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    for k, s in out["signals"].items():
        print(f"{k:32s} fires={s.get('fires')}  {s.get('reading', '')[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
