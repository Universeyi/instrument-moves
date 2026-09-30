#!/usr/bin/env python3
"""How does completion-tokens-per-step behave as a change detector? -> data/detector_checks.json

    python3 scripts/detector_checks.py [--sentinel-alerts PATH]

Two checks the reviews asked for (FUJe, dzYe Q5).

in_harness   The endpoint sentinel's length test applied round by round to this
             paper's ten rounds: each round's per-run completion tokens per
             step against the pooled previous three rounds, two-sided
             permutation test on the median, Bonferroni over the nine tested
             rounds. The same test on pass/fail (Fisher's exact) shows what
             the score alone would have said at the same granularity.

sentinel     The sentinel's own record over its first weeks (a snapshot of its
             alerts.json is kept in data/): how many endpoint-probe-days were
             tested for a length shift, how many single days came out
             significant, how many shifts persisted a second day, and the
             day-shuffle false-alarm calibration. The sentinel measures
             completion length of fixed single requests, a close relative of
             tokens per agent step, not the same quantity.
"""
import argparse, json, random, statistics, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "sentinel_alerts_snapshot.json"
ARM = "A_temp0"


def perm_test_median(a, b, n_perm, rng):
    """Same test as the sentinel's drift.py, copied so this runs without it."""
    obs = abs(statistics.median(b) - statistics.median(a))
    pooled = a + b
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pooled)
        if abs(statistics.median(pooled[len(a):]) - statistics.median(pooled[:len(a)])) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def fisher_two_sided(k1, n1, k2, n2):
    from math import comb
    K, N = k1 + k2, n1 + n2
    def pr(k):
        return comb(n1, k) * comb(n2, K - k) / comb(N, K)
    p0 = pr(k1)
    lo, hi = max(0, K - n2), min(n1, K)
    return min(1.0, sum(pr(k) for k in range(lo, hi + 1) if pr(k) <= p0 * (1 + 1e-9)))


def in_harness():
    ts = json.loads((ROOT / "data" / "traj_stats.json").read_text())
    tok = {}
    for r in ts["runs"]:
        if r["arm"] == ARM and not r.get("is_backup"):
            tok.setdefault(r["trial"], []).append(r["completion_per_step"])
    runs = [json.loads(l) for l in open(ROOT / "data" / "runs.jsonl")]
    runs = [r for r in runs if r["arm"] == ARM and r.get("score") is not None]

    def kn(ts_):
        rs = [r for r in runs if r["trial_index"] in ts_]
        return sum(1 for r in rs if r["pass"]), len(rs)

    rounds = sorted(tok)
    alpha = 0.05 / (len(rounds) - 1)
    rng = random.Random(20260929)
    out = []
    for i, t in enumerate(rounds[1:], start=1):
        base = rounds[max(0, i - 3):i]
        a = [x for b in base for x in tok[b]]
        p_tok = perm_test_median(a, tok[t], 20000, rng)
        k1, n1 = kn([t])
        k0, n0 = kn(base)
        p_score = fisher_two_sided(k1, n1, k0, n0)
        out.append({"round": t, "baseline_rounds": base,
                    "tokens_median_baseline": round(statistics.median(a), 1),
                    "tokens_median_round": round(statistics.median(tok[t]), 1),
                    "tokens_p": round(p_tok, 4), "tokens_flag": p_tok < alpha,
                    "score_round": [k1, n1], "score_baseline": [k0, n0],
                    "score_p": round(p_score, 4), "score_flag": p_score < alpha})
    return {"alpha_per_round": round(alpha, 5), "rounds": out,
            "flagged_by_tokens": [x["round"] for x in out if x["tokens_flag"]],
            "flagged_by_score": [x["round"] for x in out if x["score_flag"]]}


def sentinel(path):
    d = json.loads(Path(path).read_text())
    tested, sig, persisted = Counter(), [], []
    days = set()
    for k, e in d["endpoints"].items():
        for f in e["days"]:
            for m, t in f["tests"].items():
                if t.get("advisory"):
                    continue
                tested[m] += 1
                days.add(f["day"])
                if t["significant"]:
                    sig.append({"endpoint_probe": k, "day": f["day"], "metric": m,
                                "shift_pct": t.get("shift_pct"), "shift_pp": t.get("shift_pp"), "p": t["p"]})
                if t.get("persisted"):
                    persisted.append({"endpoint_probe": k, "day": f["day"], "metric": m})
    cal = d.get("calibration") or {}
    rates = [v["false_alarm_rate"] for v in cal.values() if isinstance(v, dict) and "false_alarm_rate" in v]
    shuffled = sum(v.get("tests", 0) for v in cal.values() if isinstance(v, dict))
    fa = sum(v.get("false_alarms", 0) for v in cal.values() if isinstance(v, dict))
    return {"days": [min(days), max(days)], "n_days": len(days), "endpoint_probes": len(d["endpoints"]),
            "alpha_per_test": d["alpha_per_test"], "rule": "Bonferroni per day; drift = significant on two "
            "consecutive days in the same direction; baseline = previous 7 days",
            "tests": dict(tested),
            "significant_days": {m: sum(1 for s in sig if s["metric"] == m) for m in tested},
            "persisted": persisted, "significant": sig,
            "calibration": {"shuffled_tests": shuffled, "false_alarms": fa,
                            "pooled_rate": round(fa / shuffled, 5) if shuffled else None,
                            "per_endpoint_max": max(rates) if rates else None}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sentinel-alerts", default=None,
                    help="refresh the snapshot from this alerts.json first")
    a = ap.parse_args()
    if a.sentinel_alerts:
        SNAPSHOT.write_text(Path(a.sentinel_alerts).read_text())
    out = {"in_harness": in_harness(), "sentinel": sentinel(SNAPSHOT) if SNAPSHOT.exists() else None}
    (ROOT / "data" / "detector_checks.json").write_text(json.dumps(out, indent=1))
    h, s = out["in_harness"], out["sentinel"]
    print("in-harness flagged by tokens:", h["flagged_by_tokens"], " by score:", h["flagged_by_score"])
    for x in h["rounds"]:
        print(f'  round {x["round"]:2d}: tokens p={x["tokens_p"]:.4f}  score p={x["score_p"]:.4f}')
    if s:
        print("sentinel", s["days"], s["tests"], "significant", s["significant_days"],
              "persisted", len(s["persisted"]), "calibration", s["calibration"])


if __name__ == "__main__":
    sys.exit(main())
