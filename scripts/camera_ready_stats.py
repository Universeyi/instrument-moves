#!/usr/bin/env python3
"""Sensitivity analyses the reviews asked for -> data/camera_ready_stats.json.

    python3 scripts/camera_ready_stats.py

Reads data/runs.jsonl and reuses analyze.py's own functions, so every number
here is computed the same way as the paper's headline. Headline basis: arm
A_temp0, rounds 4-10 without round 6 (six rounds x 29 tasks).

1. round_bootstrap      resample whole rounds; interval on the 8.15 threshold
2. round_effect         does the independent-task model under-predict the
                        spread of round scores? size of a shared round effect,
                        and the threshold once it is allowed for
3. leave_one_task_out   threshold with each task removed (CVEmailTask singled out)
4. pre_vs_post          single-run sigma on rounds 1-3 vs the headline rounds
5. power80              the 80%-power figure analyze.py already computes
6. probe_dispersion     within-day spread of the out-of-harness probes
7. token_agreement      how closely the pre-shift token medians agree
"""
import importlib.util, json, math, random, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("analyze", ROOT / "scripts" / "analyze.py")
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

ARM = "A_temp0"
HEADLINE = [4, 5, 7, 8, 9, 10]
PRE = [1, 2, 3]
SEED = 20260929
Z95, Z80 = A.Z95, A.Z80_POWER


def load():
    rows, _ = A.load_rows(ROOT / "data" / "runs.jsonl")
    rows, _ = A.dedupe(rows)
    rows = [r for r in rows if r.get("arm") == ARM]
    pilot = json.loads((ROOT / "data" / "pilot_tasks.json").read_text())
    meta = {it["task_id"]: {**it, "bucket": b} for b, items in pilot["buckets"].items() for it in items}
    strata = dict(pilot["selection"]["available"])
    return rows, meta, strata


def rates(rows, meta):
    """task_id -> (bucket, passes, scored) over the given rows."""
    k, n = defaultdict(int), defaultdict(int)
    for r in rows:
        if A.is_scored(r):
            n[r["task_id"]] += 1
            k[r["task_id"]] += A.passed(r)
    return {t: (meta[t]["bucket"], k[t], n[t]) for t in n}


def fixed_ps(rt, strata):
    by = defaultdict(list)
    for t, (b, k, n) in rt.items():
        if n:
            by[b].append(k / n)
    ps = []
    for b, size in sorted(strata.items()):
        ps += A._fill_slots(sorted(by[b]), size)
    return ps


def sd_suite(ps):
    """Exact SD (pp) of the suite score when tasks are independent Bernoullis."""
    return 100 * math.sqrt(sum(p * (1 - p) for p in ps)) / len(ps)


def mdd(sd):
    return Z95 * math.sqrt(2) * sd


def pct(xs, q):
    return A.pct(xs, q)


def round_scores(rows, rounds):
    out = []
    for t in rounds:
        rs = [r for r in rows if r.get("trial_index") == t and A.is_scored(r)]
        out.append(100 * sum(A.passed(r) for r in rs) / len(rs))
    return out


def expit(x):
    return 1 / (1 + math.exp(-x))


def logit(p):
    return math.log(p / (1 - p))


def shifted(p, d):
    if p <= 0 or p >= 1:
        return p
    return expit(logit(p) + d)


def main():
    rows, meta, strata = load()
    head = [r for r in rows if r.get("trial_index") in HEADLINE]
    rt = rates(head, meta)
    ps117 = fixed_ps(rt, strata)
    sd0 = sd_suite(ps117)
    out = {"basis": {"arm": ARM, "rounds": HEADLINE, "n_tasks": len(rt), "suite_slots": len(ps117)},
           "headline": {"single_run_sd_pp": round(sd0, 4), "threshold95_pp": round(mdd(sd0), 4),
                        "note": "Exact Bernoulli SD; analyze.py simulates the same quantity (2.9412 / 8.1524)."}}
    matrix = ROOT / "data" / "public_matrix.json"

    def gaps(th):
        g = A.leaderboard_gaps(matrix, th)
        return None if not g else {"within": g.get("n_adjacent_within_noise"), "of": g.get("n_adjacent_pairs")}

    out["headline"]["leaderboard_adjacent"] = gaps(mdd(sd0))

    # 1. round bootstrap
    rng = random.Random(SEED)
    by_round = {t: [r for r in head if r.get("trial_index") == t] for t in HEADLINE}
    ths = []
    for _ in range(5000):
        pick = [rng.choice(HEADLINE) for _ in HEADLINE]
        rs = [r for t in pick for r in by_round[t]]
        ths.append(mdd(sd_suite(fixed_ps(rates(rs, meta), strata))))
    lo, hi = pct(ths, 0.025), pct(ths, 0.975)
    out["round_bootstrap"] = {
        "method": "Six headline rounds resampled with replacement (5000 draws); per-task rates, the 117-slot "
                  "reweighting and the threshold recomputed on each draw.",
        "threshold95_pp_median": round(statistics.median(ths), 4),
        "threshold95_pp_ci95": [round(lo, 4), round(hi, 4)],
        "leaderboard_adjacent_at_ci_low": gaps(lo), "leaderboard_adjacent_at_ci_high": gaps(hi)}

    # 2. shared round effect
    ps29 = [k / n for (_, k, n) in rt.values()]
    obs = round_scores(head, HEADLINE)
    obs_var = statistics.variance(obs)
    pred_sd29 = sd_suite(ps29)

    def sim_var(sigma, draws, seed):
        g = random.Random(seed)
        vs = []
        for _ in range(draws):
            sc = []
            for _ in HEADLINE:
                d = g.gauss(0, sigma) if sigma else 0.0
                sc.append(100 * sum(g.random() < shifted(p, d) for p in ps29) / len(ps29))
            vs.append(statistics.variance(sc))
        return vs

    null = sim_var(0.0, 20000, SEED + 1)
    p_over = sum(v >= obs_var for v in null) / len(null)
    # largest logit-scale round SD still consistent with the observed spread
    grid = [i / 20 for i in range(0, 41)]
    upper = None
    for s in grid:
        vs = sim_var(s, 4000, SEED + 2)
        if sum(v <= obs_var for v in vs) / len(vs) < 0.05:
            upper = s
            break

    def sd117_with(sigma, draws=20000, seed=SEED + 3):
        g = random.Random(seed)
        xs = []
        for _ in range(draws):
            d = g.gauss(0, sigma) if sigma else 0.0
            xs.append(100 * sum(g.random() < shifted(p, d) for p in ps117) / len(ps117))
        return statistics.stdev(xs)

    # additive version: the whole round's score moves by N(0, c) points
    def sim_var_add(c, draws, seed):
        g = random.Random(seed)
        vs = []
        for _ in range(draws):
            sc = [100 * sum(g.random() < p for p in ps29) / len(ps29) + (g.gauss(0, c) if c else 0.0)
                  for _ in HEADLINE]
            vs.append(statistics.variance(sc))
        return vs

    upper_add = None
    for c in [i / 4 for i in range(0, 81)]:
        vs = sim_var_add(c, 4000, SEED + 4)
        if sum(v <= obs_var for v in vs) / len(vs) < 0.05:
            upper_add = c
            break
    th_add = mdd(math.sqrt(sd0 ** 2 + upper_add ** 2)) if upper_add is not None else None

    out["round_effect"] = {
        "additive_round_sd_pp_upper95": upper_add,
        "threshold95_pp_if_additive_round_sd_at_upper": round(th_add, 4) if th_add else None,
        "leaderboard_adjacent_at_additive_upper": gaps(th_add) if th_add else None,
        "observed_round_scores_pct": [round(x, 2) for x in obs],
        "observed_sd_pp": round(math.sqrt(obs_var), 4),
        "predicted_sd_pp_independent_tasks": round(pred_sd29, 4),
        "p_value_spread_at_least_observed": round(p_over, 4),
        "logit_round_sd_upper95": upper,
        "threshold95_pp_if_round_sd_at_upper": (round(mdd(sd117_with(upper)), 4) if upper else None),
        "leaderboard_adjacent_at_that_threshold": (gaps(mdd(sd117_with(upper))) if upper else None),
        "method": "Round-to-round SD of the 29-task score compared with what independent tasks at the measured "
                  "rates predict (20000 simulated sets of six rounds). A shared round effect is modelled as a "
                  "common logit shift N(0, s) applied to every task in a round; tasks at 0 or 1 stay there. "
                  "`logit_round_sd_upper95` is the smallest s at which the observed spread falls below the 5th "
                  "percentile of simulated spreads, i.e. the largest round effect the six rounds cannot rule out."}

    # 3. leave one task out
    loo = []
    for t in sorted(rt):
        rt2 = {k: v for k, v in rt.items() if k != t}
        loo.append({"task": t, "bucket": rt[t][0], "threshold95_pp": round(mdd(sd_suite(fixed_ps(rt2, strata))), 4)})
    vals = [x["threshold95_pp"] for x in loo]
    out["leave_one_task_out"] = {
        "min": min(vals), "max": max(vals),
        "without_CVEmailTask": next(x["threshold95_pp"] for x in loo if x["task"] == "CVEmailTask"),
        "split_bucket": [x for x in loo if x["bucket"] == "split"],
        "method": "Each task removed in turn; its bucket's remaining rates fill the bucket's slots."}

    # 4. pre vs post
    pre = [r for r in rows if r.get("trial_index") in PRE]
    sd_pre = sd_suite(fixed_ps(rates(pre, meta), strata))
    out["pre_vs_post"] = {"rounds_1_3_sd_pp": round(sd_pre, 4), "rounds_1_3_threshold95_pp": round(mdd(sd_pre), 4),
                          "headline_sd_pp": round(sd0, 4),
                          "note": "Three rounds only; the pre-shift figure is weakly determined."}

    # 5. power
    out["power80"] = {"threshold_pp": round((Z95 + Z80) * math.sqrt(2) * sd0, 4),
                      "leaderboard_adjacent": gaps((Z95 + Z80) * math.sqrt(2) * sd0)}

    # 6. probe dispersion
    probe = json.loads((ROOT / "data" / "probe_determinism.json").read_text())
    disp = []
    for run in probe["runs"]:
        ct = run["completion_tokens"]
        disp.append({"date": run["date"], "endpoint": f'{run["provider"]}/{run["quantization"]}',
                     "tokens": ct, "sd": round(statistics.stdev(ct), 1), "range": max(ct) - min(ct),
                     "mean": round(statistics.fmean(ct), 1)})
    out["probe_dispersion"] = disp

    # 7. token agreement before the shift
    ts = json.loads((ROOT / "data" / "traj_stats.json").read_text())
    med = {int(k): v["median"] for k, v in ts["by_arm"][ARM]["per_trial_completion_per_step"].items()}
    pre_m = [med[t] for t in PRE]
    post_m = [med[t] for t in HEADLINE]
    out["token_agreement"] = {
        "median_completion_per_step_by_round": {str(k): med[k] for k in sorted(med)},
        "pre_shift_spread_pct": round(100 * (max(pre_m) - min(pre_m)) / min(pre_m), 2),
        "post_shift_headline_spread_pct": round(100 * (max(post_m) - min(post_m)) / min(post_m), 2),
        "change_round3_to_round4_pct": round(100 * (med[4] - med[3]) / med[3], 2)}

    (ROOT / "data" / "camera_ready_stats.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1)[:6000])


if __name__ == "__main__":
    main()
