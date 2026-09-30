#!/usr/bin/env python3
"""Check every numeric claim in the paper against the data files that produced it.

The paper argues that the difference between claiming to have done something
and having done it must be demonstrated by an artefact. This script is that
artefact for its own numbers. It is meant to be re-run against the final
sources, and its output pasted into the submission checklist.

    python3 scripts/audit_paper_numbers.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    return json.loads((ROOT / "data" / name).read_text())


def main() -> int:
    H = load("analysis_headline.json")["arms"]["A_temp0"]   # rounds 4-10 less 6
    A = load("analysis.json")["arms"]["A_temp0"]            # rounds 4-10
    AL = load("analysis_all.json")["arms"]["A_temp0"]       # all 10 rounds
    S = load("sensitivity_round6.json")
    PR = load("probe_determinism.json")
    rc = AL["regime_comparison"]
    ext = A["gui_only_extrapolation"]["fixed_taskset"]
    lost = S["round6_denominator_check"]["lost_tasks"]

    CR = load("camera_ready_stats.json")
    DC = load("detector_checks.json")

    def probe_sd(i):
        import statistics
        return statistics.stdev(PR["runs"][i]["completion_tokens"])

    _post = [v for k, v in CR["token_agreement"]["median_completion_per_step_by_round"].items() if int(k) >= 4]
    tok_halfrange_pct = 100 * (max(_post) - min(_post)) / 2 / ((max(_post) + min(_post)) / 2)

    # Strand-3 regime statistics, recomputed from the raw per-run records on
    # the paper's stated basis: rounds 1-3 vs rounds 4-10, round 6 in, its six
    # zero-step no-verdict runs out. Hard-coding these literals would make the
    # check tautological.
    runs = [json.loads(l) for l in (ROOT / "data" / "runs.jsonl").read_text().splitlines()]
    arm_rows = [r for r in runs if r.get("arm") == "A_temp0"]
    pre = [r for r in arm_rows if 1 <= r["trial_index"] <= 3]
    post = [r for r in arm_rows if 4 <= r["trial_index"] <= 10 and r["steps"] > 0]

    def s_per_step(g):
        return sum(r["duration_s"] / r["steps"] for r in g) / len(g)

    def steps_mean(g):
        return sum(r["steps"] for r in g) / len(g)

    def cap_share_pct(g):
        return 100 * sum(1 for r in g if r["steps"] >= r["max_round"]) / len(g)

    # Step-count census of the published trajectories (Table 3, row 2).
    # Read in full (public_matrix.lenient.json): three bundles use result
    # formats the strict parser skips. The strict matrix is kept for the
    # footnote that discloses what the reviewed version printed.
    def cap_census(name):
        pub = load(name)
        runs = [m for t in pub["tasks"].values() for m in t["models"].values() if m.get("steps") == 50]
        return (len(runs), sum(1 for m in runs if m.get("score") == 0),
                sum(1 for m in runs if m.get("pass")), sum(1 for m in runs if m.get("score") is None))
    cap_n, cap_scored0, cap_passed, cap_noscore = cap_census("public_matrix.lenient.json")
    _, strict_scored0, strict_passed, strict_noscore = cap_census("public_matrix.json")
    assert cap_noscore == 0, "a run at the cap still has no score under the full read"

    loo = {v["dropped_trial"]: v["mdd95_pp"] for v in A["leave_one_out"]["variants"]}
    # (string as printed, exact value, where it comes from)
    claims = [
        ("8.07",  S["without_round6"]["mdd95_pp"],            "MDD, 6 rounds"),
        ("8.64",  A["detectable_difference"]["mdd95_pp"],     "MDD, 7 rounds"),
        ("11.11", S["without_round6"]["extrapolated_117_width_pp"], "117-task width, 6 rounds"),
        ("12.82", ext["ci95_width_pp"],                       "117-task width, 7 rounds"),
        ("7.91",  A["leave_one_out"]["mdd95_pp_min"],         "leave-one-out MDD min"),
        ("8.96",  A["leave_one_out"]["mdd95_pp_max"],         "leave-one-out MDD max"),
    ] + [
        (printed, loo[r], f"leave-one-out MDD, round {r} dropped")
        for r, printed in ((4, "8.96"), (5, "8.47"), (7, "8.38"), (8, "7.91"), (9, "8.69"), (10, "8.96"))
    ] + [
        ("5.87",  S["with_round6"]["headline_sd_pp"],         "29-task sigma, 7 rounds"),
        ("5.92",  S["without_round6"]["headline_sd_pp"],      "29-task sigma, 6 rounds"),
        ("3.12",  ext["sd_pp_exact"],                               "117-task sigma, 7 rounds"),
        ("2.91",  S["without_round6"]["extrapolated_117_sd_pp_exact"], "117-task sigma, 6 rounds"),
        ("8.10",  A["per_trial_suite_score"]["observed_range_pp"], "spread, 7 rounds"),
        ("6.90",  S["without_round6"]["best_worst_spread_pp"], "spread, 6 rounds"),
        ("39.13", S["round6_denominator_check"]["round6_as_scored_pct"], "round 6 as scored"),
        ("40.2",  S["round6_denominator_check"]["round6_imputed_at_own_rates_pct"], "round 6 imputed"),
        # Recomputed from the per-task rates, not from the file's 4dp rounding:
        # comparing a 1dp claim against a 4dp store produces false alarms.
        ("44.4",  100 * sum(v["rate"] for v in lost.values()) / len(lost),
                  "mean rate of the tasks round 6 lost"),
        ("20.1",  abs(rc["reasoning_tokens_per_action"]["change_pct"]), "reasoning drop"),
        ("132.5", rc["reasoning_tokens_per_action"]["before_median_of_medians"], "reasoning before"),
        ("105.9", rc["reasoning_tokens_per_action"]["after_median_of_medians"], "reasoning after"),
        ("0.002", rc["sign_test_p"],                          "sign test p"),
        # Flip counts: the body quotes the six-round basis, the comparison
        # table quotes both. Both spellings are checked.
        ("Seven of 29", float(H["flips"]["n_flipped"]),      "flipped, headline basis (spelled out)"),
        ("6 of 10",     float(H["subgroups"]["by_bucket"]["split"]["n_flipped"]), "split-bucket flips, headline"),
        ("1 of 9",      float(H["subgroups"]["by_bucket"]["all_pass"]["n_flipped"]), "all-pass flips, headline"),
        ("0 of 10",     float(H["subgroups"]["by_bucket"]["all_fail"]["n_flipped"]), "all-fail flips, headline"),
        # The pair counts were the one wrong number in the paper and were not
        # covered here, which is how it survived. Both thresholds now checked.
        ("35 of the 153", float(load("analysis_headline.json")["arms"]["A_temp0"]
                                ["leaderboard_gaps"]["n_all_pairs_within_noise"]),
                          "pairs within noise, headline threshold"),
        ("39 at the",     float(A["leaderboard_gaps"]["n_all_pairs_within_noise"]),
                          "pairs within noise, seven-round threshold"),
        ("16.0",  s_per_step(pre),   "s/step before, regime basis"),
        ("13.7",  s_per_step(post),  "s/step after, regime basis"),
        ("28.7",  steps_mean(pre),   "mean steps before, regime basis"),
        ("30.6",  steps_mean(post),  "mean steps after, regime basis"),
        ("31",    cap_share_pct(pre),  "budget-stopped share before, regime basis"),
        ("45",    cap_share_pct(post), "budget-stopped share after, regime basis"),
        ("490",   float(cap_scored0), "runs at the cap scored 0 (all 18 bundles read)"),
        ("93.3",  100.0 * cap_scored0 / cap_n, "share of runs at the cap scored 0"),
        ("35",    float(cap_passed), "runs at the cap that passed"),
        ("90",    float(strict_noscore), "footnote: runs at the cap the reviewed version printed as unscored"),
        ("83",    float(cap_scored0 - strict_scored0), "footnote: of those 90, zeros"),
        ("7",     float(cap_passed - strict_passed), "footnote: of those 90, passes"),
        ("110",   AL["absorbed_retries"]["total"],            "silent retries, all rounds"),
        ("95",    A["absorbed_retries"]["total"],             "silent retries, headline rounds"),
        # Strand 4 reports dispersion (sample SD) since the camera-ready.
        ("761",   probe_sd(1),                                "probe sample SD, pinned endpoint day 1"),
        # Numbers the camera-ready added (scripts/camera_ready_stats.py, scripts/detector_checks.py).
        ("5.18",  CR["round_bootstrap"]["threshold95_pp_ci95"][0], "round-bootstrap interval, low"),
        ("8.53",  CR["round_bootstrap"]["threshold95_pp_ci95"][1], "round-bootstrap interval, high"),
        ("11.54", CR["power80"]["threshold_pp"],              "threshold at 80% power"),
        ("3.08",  CR["round_effect"]["observed_sd_pp"],       "round-score SD, six rounds"),
        ("3.98",  CR["round_effect"]["predicted_sd_pp_independent_tasks"], "round-score SD predicted under independence"),
        ("0.70",  CR["round_effect"]["p_value_spread_at_least_observed"], "p, excess dispersion"),
        ("5.25",  CR["round_effect"]["additive_round_sd_pp_upper95"], "largest additive round effect not ruled out"),
        ("16.6",  CR["round_effect"]["threshold95_pp_if_additive_round_sd_at_upper"], "threshold at that round effect"),
        ("3.40",  CR["pre_vs_post"]["rounds_1_3_sd_pp"],      "sigma, rounds 1-3"),
        ("9.41",  CR["pre_vs_post"]["rounds_1_3_threshold95_pp"], "threshold, rounds 1-3"),
        ("7.61",  CR["leave_one_task_out"]["min"],            "leave-one-task-out min (without CVEmailTask)"),
        ("8.56",  CR["leave_one_task_out"]["max"],            "leave-one-task-out max"),
        ("0.55",  CR["token_agreement"]["pre_shift_spread_pct"], "pre-shift token medians agree within"),
        ("4.8",   tok_halfrange_pct,                          "post-shift token medians, half-range around midpoint"),
        ("328",   float(DC["sentinel"]["tests"]["length"]),   "sentinel endpoint-days tested for length"),
        ("0.06",  100 * DC["sentinel"]["calibration"]["pooled_rate"], "sentinel shuffled false-alarm rate, %"),
    ]

    tex = "\n".join(p.read_text() for p in sorted((ROOT / "paper" / "sections").glob("*.tex")))
    tex += (ROOT / "paper" / "main.tex").read_text()

    print(f"{'printed':>12}  {'exact':>12}  {'':4} source")
    problems = []
    for lit, val, where in claims:
        # Some claims are phrases ("Seven of 29", "6 of 10"); for those only
        # presence in the text is checkable, and the leading count is compared.
        if not lit.replace(".", "").isdigit():
            present = lit in tex
            lead = lit.split()[0]
            words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                     "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
            got = words.get(lead.lower(), None)
            if got is None:
                got = float(lead) if lead.replace(".", "").isdigit() else None
            close = got is not None and abs(got - float(val)) < 1e-9
            ok = present and close
            if not ok:
                problems.append((lit, val, where,
                                 "absent from text" if not present else "count mismatch"))
            print(f"{lit:>12}  {float(val):>12.4f}  {'ok  ' if ok else 'FAIL'} {where}")
            continue
        dec = len(lit.split(".")[1]) if "." in lit else 0
        tol = 0.5 * 10 ** (-dec)
        present = lit in tex
        close = abs(float(lit) - float(val)) <= tol + 1e-9
        ok = present and close
        if not ok:
            problems.append((lit, val, where, "absent from text" if not present else "value mismatch"))
        print(f"{lit:>12}  {float(val):>12.4f}  {'ok  ' if ok else 'FAIL'} {where}")

    # A printed sha256 would be a perfect deanonymising fingerprint during review.
    leak = load("analysis.json")["inputs"]["runs_sha256"][:8] in tex
    print(f"\nruns.jsonl hash printed in the paper: {'YES - REMOVE IT' if leak else 'no'}")

    print(f"\nclaims checked: {len(claims)}   problems: {len(problems)}")
    for p_ in problems:
        print("   !!", p_)
    return 1 if problems or leak else 0


if __name__ == "__main__":
    sys.exit(main())
