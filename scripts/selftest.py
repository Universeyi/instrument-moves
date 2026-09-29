#!/usr/bin/env python3
"""Prove the analysis chain works, on this machine, without spending anything.

The pilot costs about 27 hours and about $29. Finding out afterwards that
analyze.py crashes on the shape of the data would be an expensive way to learn
it. This fabricates a runs.jsonl with the right shape, runs the whole chain over
it in a temporary directory, and checks the results are internally consistent.

The numbers it produces are meaningless by construction and never leave the
temporary directory. Nothing under data/ or report/ is touched, and the fixture
is deleted on the way out unless --keep is given.

    python3 scripts/selftest.py
    python3 scripts/selftest.py --keep      # leave the fixture for inspection
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Chosen so the fixture exercises all three buckets and both failure outcomes.
TRUE_RATE = {"all_pass": 0.9, "all_fail": 0.05, "split": 0.55}


def make_fixture(path: Path, trials: int, seed: int) -> int:
    pilot = json.loads((ROOT / "data" / "pilot_tasks.json").read_text())
    rng = random.Random(seed)
    rows = []
    for trial in range(1, trials + 1):
        for bucket, items in pilot["buckets"].items():
            for it in items:
                roll = rng.random()
                outcome = ("no_result" if roll < 0.015 else
                           "env_unhealthy" if roll < 0.02 else "scored")
                score = (1.0 if rng.random() < TRUE_RATE[bucket] else 0.0) \
                    if outcome == "scored" else None
                rows.append({
                    "run_id": uuid.uuid4().hex, "task_id": it["task_id"],
                    "trial_index": trial, "arm": "SELFTEST_SYNTHETIC",
                    "outcome": outcome, "score": score,
                    "pass": score is not None and score > 0.99,
                    "reason": "SYNTHETIC FIXTURE — not a measurement",
                    "steps": rng.choice([4, 9, 15, 22, 31, 50]),
                    "n_screenshots": 12, "total_tokens": 200000,
                    "absorbed_retries": 1 if rng.random() < 0.03 else 0,
                    "duration_s": round(rng.uniform(180, 900), 2),
                    "started_at": "1970-01-01T00:00:00+00:00",
                    "ended_at": "1970-01-01T00:10:00+00:00",
                    "model": "SYNTHETIC/model",
                    "model_params": {"or_provider": "synthetic", "or_quant": "fp4",
                                     "temperature": "0.0", "seed": "42"},
                    "agent_env": {}, "agent_type": "agents/pinned_e2e_agent.py",
                    "llm_base_url": "https://example.invalid/v1",
                    "max_round": 50, "step_wait_time": 3.0, "auto_retry": 10,
                    "enable_user_interaction": False, "enable_mcp": False,
                    "image": "SYNTHETIC", "image_digest": "0" * 64,
                    "taskset_commit": "0" * 40, "taskset_dirty": False,
                    "host_spec": {"kernel": "synthetic", "hostname": "synthetic",
                                  "cpu_count": 1},
                    "recycle_policy": "trial", "env_recycled": False,
                    "traj_path": f"runs/SELFTEST/{it['task_id']}/trial_{trial:03d}",
                    "eval_returncode": 0,
                })
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return len(rows)


def step(label: str, cmd: list[str]) -> bool:
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    if not ok:
        print((r.stdout + r.stderr).strip()[-1500:])
    return ok


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}{('  — ' + detail) if detail and not condition else ''}")
    return condition


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="mw-selftest-"))
    runs = tmp / "runs.jsonl"
    analysis = tmp / "analysis.json"
    ok = True
    try:
        print(f"fixture: {tmp}")
        n = make_fixture(runs, args.trials, args.seed)
        print(f"  {n} synthetic rows over {args.trials} trials\n")

        print("chain:")
        ok &= step("analyze.py", [str(ROOT / "scripts/analyze.py"),
                                  "--runs", str(runs), "--out", str(analysis)])
        have_mpl = subprocess.run(
            [sys.executable, "-c", "import matplotlib"],
            capture_output=True).returncode == 0
        if ok:
            if have_mpl:
                ok &= step("plots.py", [str(ROOT / "scripts/plots.py"),
                                        "--analysis", str(analysis),
                                        "--outdir", str(tmp / "figures")])
            else:
                # The only third-party dependency in the repository. Its absence
                # is a missing package, not a broken chain, so it is not a
                # failure -- but it is not a pass either.
                print("  skip  plots.py — matplotlib is not installed here")
            ok &= step("render_report.py --check",
                       [str(ROOT / "scripts/render_report.py"),
                        "--analysis", str(analysis), "--check"])

            # The template carries sections that only apply to some arms. The
            # plain path above proves they vanish cleanly when their data is
            # absent; this proves they still render when it is present. Both
            # directions have broken before -- once each.
            opt = tmp / "analysis_optional.json"
            ok &= step("analyze.py (optional sections)",
                       [str(ROOT / "scripts/analyze.py"),
                        "--runs", str(runs), "--out", str(opt),
                        "--regime-split", "4", "--leave-one-out"])
            ok &= step("render_report.py --check (optional sections)",
                       [str(ROOT / "scripts/render_report.py"),
                        "--analysis", str(opt), "--check"])
            ok &= step("find_candidates.py",
                       [str(ROOT / "scripts/find_candidates.py"),
                        "--analysis", str(analysis), "--runs", str(runs),
                        "--out", str(tmp / "candidates.json"), "--top", "0"])
            ok &= step("scrub_runs.py", [str(ROOT / "scripts/scrub_runs.py"),
                                         "--in", str(runs),
                                         "--out", str(tmp / "runs.public.jsonl")])

        if analysis.exists():
            a = json.loads(analysis.read_text())["arms"]["SELFTEST_SYNTHETIC"]
            print("\nconsistency:")
            ok &= check("provenance is consistent", a["provenance"]["consistent"])
            ok &= check("no duplicate trial keys",
                        not json.loads(analysis.read_text())["inputs"]["duplicate_keys"])
            ok &= check("every task has a Wilson interval containing its rate",
                        all(t["wilson95"][0] <= t["pass_rate"] <= t["wilson95"][1]
                            for t in a["tasks"] if t["n_scored"]))
            ok &= check("flip rate never exceeds its own maximum",
                        all(t["flip_rate"] <= t["flip_rate_max"] for t in a["tasks"]
                            if t["flip_rate"] is not None))
            ok &= check("posterior spread >= plug-in spread",
                        a["rerun"]["posterior"]["sd_pp"] >= a["rerun"]["plugin"]["sd_pp"],
                        "the posterior must not be narrower than the plug-in")
            ok &= check("extrapolation covers the whole GUI-Only suite",
                        a["gui_only_extrapolation"]["total_slots"] == 117)
            ok &= check("resampling tasks widens the interval",
                        a["gui_only_extrapolation"]["resampled_tasks"]["sd_pp"]
                        >= a["gui_only_extrapolation"]["fixed_taskset"]["sd_pp"])
            ok &= check("statistical power sufficient for suite claims",
                        a["statistical_power"]["sufficient"])
            ok &= check("no hostname in the published analysis",
                        "hostname" not in a["provenance"]["host"])
            if have_mpl:
                ok &= check("three figures were drawn",
                            len(list((tmp / "figures").glob("*.png"))) == 3)

        if ok:
            print("\nPASS — the chain runs and its output is internally "
                  "consistent."
                  + ("" if have_mpl else "  (figures not checked: no matplotlib)"))
        else:
            print("\nFAIL — see above.")
        if args.keep:
            print(f"fixture kept at {tmp}")
        return 0 if ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
