#!/usr/bin/env python3
"""Turn data/runs.jsonl into data/analysis.json.

Every number that appears in report/ must come out of this file. Nothing here
reads the environment, calls a model, or costs anything; given a runs.jsonl it
is fully deterministic (the resampling is seeded).

Three quantities are easy to confuse and are therefore computed separately and
named explicitly:

  rerun          If you ran this same task set again, where would the total
                 score land? This is the noise floor -- the headline.
  rerun_plugin   Same, but treating each measured pass rate as if it were the
                 true one. Ignores the fact that 10 trials only pin a rate down
                 to roughly +/-15 points, so it is a *lower bound* on the noise.
  estimate       How well do our own 10 trials pin down our own total score?
                 Uncertainty about this experiment, not about a future run.

Usage:
    python3 scripts/analyze.py                     # every arm in runs.jsonl
    python3 scripts/analyze.py --arm A_temp0
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS_THRESHOLD = 0.99   # upstream's own, see ENVIRONMENT.md §2.4
Z95 = 1.959963984540054
Z80_POWER = 0.8416212335729143

# Fields that must not vary inside one arm: if any of them does, the rows are
# not measuring the same thing and the arm's variance figure is meaningless.
PROVENANCE_FIELDS = [
    "model", "agent_type", "llm_base_url", "image", "image_digest",
    "taskset_commit", "max_round", "step_wait_time", "auto_retry",
    "enable_user_interaction", "enable_mcp", "recycle_policy",
]


# --------------------------------------------------------------------- helpers

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def redact_path(value):
    """Keep a path recognisable without shipping somebody's home directory.

    analysis.json is published. run_matrix.py absolutises --agent-type (mw eval
    runs with a different cwd, so it has to), and an absolute path on the
    machine that ran the matrix carries a username. Only the tail is
    identifying-free and it is the only part a reader needs.
    """
    if not isinstance(value, str) or "/" not in value:
        return value
    parts = [p for p in value.split("/") if p]
    return ("…/" + "/".join(parts[-2:])) if len(parts) > 2 else value


def repo_path(value) -> str | None:
    """Record where a file was, without recording whose machine it was on.

    analysis.json is published. It names its own inputs, and an absolute path
    on the machine that ran the analysis carries a username -- on this project,
    one derived from the operator's email address. Inside the repository the
    relative path is both cleaner and more useful; outside it, fall back to the
    same tail-only redaction used for the agent path.
    """
    if value is None:
        return None
    try:
        return str(Path(value).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return redact_path(str(value))


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval.

    Not the normal approximation: at n=10 the normal interval is badly wrong at
    the ends (it gives zero width at k=0 and k=n, and can leave [0,1]).
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def flip_rate(k: int, n: int) -> float | None:
    """Fraction of trial *pairs* that disagree.

    k passes out of n trials gives k(n-k) discordant pairs out of C(n,2).
    Note the maximum is not 1: at n=10 a 5/5 split scores 25/45 = 0.5556.
    flip_rate_max is reported alongside so the number can be read in scale.
    """
    if n < 2:
        return None
    return k * (n - k) / (n * (n - 1) / 2)


def flip_rate_max(n: int) -> float | None:
    if n < 2:
        return None
    return (n // 2) * ((n + 1) // 2) / (n * (n - 1) / 2)


def pct(xs: list[float], q: float) -> float:
    """Percentile by linear interpolation on a sorted list."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def summarize(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 4),
        "sd": round(statistics.stdev(xs), 4) if len(xs) > 1 else 0.0,
        "min": round(min(xs), 4),
        "p05": round(pct(xs, 0.05), 4),
        "median": round(pct(xs, 0.5), 4),
        "p95": round(pct(xs, 0.95), 4),
        "max": round(max(xs), 4),
    }


# ------------------------------------------------------------------- load rows

def load_rows(path: Path) -> tuple[list[dict], list[str]]:
    rows, problems = [], []
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                problems.append(f"line {lineno}: unparseable ({e})")
    return rows, problems


def dedupe(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """One row per (arm, task_id, trial_index).

    run_matrix.py's resume logic should make duplicates impossible, so if any
    turn up it is a real integrity problem and gets reported, not swallowed.
    The later row wins -- it is the one whose artifacts are still on disk.
    """
    seen: dict[tuple, dict] = {}
    dups = []
    for r in rows:
        key = (r.get("arm"), r.get("task_id"), r.get("trial_index"))
        if key in seen:
            dups.append(f"{key[0]}/{key[1]}/trial {key[2]}")
        seen[key] = r
    return list(seen.values()), sorted(set(dups))


def is_scored(r: dict) -> bool:
    return r.get("outcome") == "scored" and isinstance(r.get("score"), (int, float))


def passed(r: dict) -> bool:
    return is_scored(r) and r["score"] > PASS_THRESHOLD


# ------------------------------------------------------------------ provenance

def check_provenance(rows: list[dict]) -> dict:
    violations = []
    values = {}
    for field in PROVENANCE_FIELDS:
        seen = {json.dumps(r.get(field), sort_keys=True, ensure_ascii=False)
                for r in rows}
        if len(seen) > 1:
            violations.append({
                "field": field,
                "distinct_values": sorted(seen)[:8],
                "n_distinct": len(seen),
            })
        values[field] = rows[0].get(field) if rows else None

    params = {json.dumps(r.get("model_params") or {}, sort_keys=True) for r in rows}
    if len(params) > 1:
        violations.append({"field": "model_params", "n_distinct": len(params),
                           "distinct_values": sorted(params)[:8]})

    # A field that is uniformly null is "consistent" and still useless: the
    # digest is what ties a result to an environment.
    for field in ("image_digest", "taskset_commit"):
        if all(r.get(field) is None for r in rows):
            violations.append({
                "field": field,
                "note": f"{field} is null on every row — the run did not record "
                        f"which environment produced it",
                "n_rows": len(rows),
            })

    dirty = [r["run_id"] for r in rows if r.get("taskset_dirty")]
    if dirty:
        violations.append({"field": "taskset_dirty",
                           "note": "vendor/ checkout was modified during these runs",
                           "n_rows": len(dirty)})

    # run_matrix.py records `pass` itself; recomputing it here from `score`
    # keeps the analysis self-contained and catches a threshold drift.
    mismatched = [r["run_id"] for r in rows
                  if is_scored(r) and r.get("pass") is not None
                  and bool(r["pass"]) != passed(r)]
    if mismatched:
        violations.append({"field": "pass",
                           "note": f"recorded `pass` disagrees with score>{PASS_THRESHOLD}",
                           "n_rows": len(mismatched)})

    partial = sorted({r["score"] for r in rows
                      if is_scored(r) and 0.0 < r["score"] <= PASS_THRESHOLD})

    values["agent_type"] = redact_path(values.get("agent_type"))

    # Same reason: the host spec is useful for reproduction, the machine's name
    # is not, and this file is meant to be publishable as it stands.
    host = dict((rows[0].get("host_spec") or {}) if rows else {})
    host.pop("hostname", None)

    return {
        "consistent": not violations,
        "violations": violations,
        "values": values,
        "model_params": rows[0].get("model_params") if rows else None,
        "host": host,
        "partial_scores_seen": partial,
    }


# ------------------------------------------------------------------- per task

def per_task(rows: list[dict], meta: dict) -> list[dict]:
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)

    out = []
    for task_id, trs in sorted(by_task.items()):
        trs.sort(key=lambda r: r.get("trial_index", 0))
        scored = [r for r in trs if is_scored(r)]
        k = sum(1 for r in scored if passed(r))
        n = len(scored)
        lo, hi = wilson(k, n)
        m = meta.get(task_id, {})
        steps = [r.get("steps") for r in scored if isinstance(r.get("steps"), int)]
        durs = [r.get("duration_s") for r in trs
                if isinstance(r.get("duration_s"), (int, float))]
        out.append({
            "task_id": task_id,
            "bucket": m.get("bucket"),
            "group": m.get("group"),
            "randomized_setup": m.get("randomized_setup"),
            "public_pass_fraction": m.get("public_pass_fraction"),
            "n_trials": len(trs),
            "n_scored": n,
            "n_unscored": len(trs) - n,
            "unscored_outcomes": dict(Counter(r.get("outcome") for r in trs
                                              if not is_scored(r))),
            "passes": k,
            "pass_rate": round(k / n, 4) if n else None,
            "wilson95": [round(lo, 4), round(hi, 4)],
            "wilson95_width": round(hi - lo, 4),
            "flipped": bool(n >= 2 and 0 < k < n),
            "flip_rate": round(flip_rate(k, n), 4) if flip_rate(k, n) is not None else None,
            "flip_rate_max": round(flip_rate_max(n), 4) if flip_rate_max(n) is not None else None,
            # A run treated as failed rather than dropped, so the report can
            # state how much the choice of denominator moves the answer.
            "pass_rate_unscored_as_fail": round(k / len(trs), 4) if trs else None,
            "steps": summarize([float(s) for s in steps]),
            "duration_s": summarize(durs),
            "absorbed_retries": sum(r.get("absorbed_retries") or 0 for r in trs),
            "trial_outcomes": [
                {"trial": r.get("trial_index"), "outcome": r.get("outcome"),
                 "score": r.get("score"), "pass": passed(r) if is_scored(r) else None,
                 "steps": r.get("steps"), "run_id": r.get("run_id"),
                 "reason": r.get("reason")}
                for r in trs
            ],
        })
    return out


def per_trial_suite_scores(rows: list[dict]) -> dict:
    """The assumption-free measurement: the suite score of each repetition.

    No model, no resampling. If trial 3 scores 48.3 and trial 7 scores 55.2,
    that spread happened, on identical inputs.
    """
    by_trial: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_trial[r.get("trial_index")].append(r)

    per = []
    for trial in sorted(by_trial):
        trs = by_trial[trial]
        scored = [r for r in trs if is_scored(r)]
        if not scored:
            continue
        k = sum(1 for r in scored if passed(r))
        per.append({
            "trial": trial,
            "n_tasks_attempted": len(trs),
            "n_tasks_scored": len(scored),
            "n_passed": k,
            "score_pct": round(100 * k / len(scored), 4),
        })

    # A trial still in progress has a smaller denominator, and the tasks it has
    # not reached yet are not a random subset -- run_matrix.py walks the task
    # file in order, so an interrupted trial is missing whichever bucket sits at
    # the end of it. Comparing that against a complete trial produces a spread
    # that is an artifact of the interruption.
    #
    # "Complete" must mean *attempted*, not *scored*. A round in which the
    # provider went down still attempted every task; it simply has a smaller
    # denominator. Judging completeness by the number of scored tasks silently
    # drops exactly the rounds an incident hit -- which are the rounds most
    # worth reporting. Those are kept, and flagged.
    attempted = max((p["n_tasks_attempted"] for p in per), default=0)
    for p in per:
        p["complete"] = p["n_tasks_attempted"] == attempted
        p["reduced_denominator"] = (p["complete"]
                                    and p["n_tasks_scored"] < p["n_tasks_attempted"])
    complete = [p for p in per if p["complete"]]

    scores = [p["score_pct"] for p in complete]
    gaps = [abs(x - y) for i, x in enumerate(scores) for y in scores[i + 1:]]
    return {
        "per_trial": per,
        "n_trials_complete": len(complete),
        "n_trials_partial": len(per) - len(complete),
        "n_trials_reduced_denominator": sum(1 for p in per if p.get("reduced_denominator")),
        "tasks_per_complete_trial": attempted,
        "summary": summarize(scores),
        "observed_range_pp": round(max(scores) - min(scores), 4) if scores else None,
        "pairwise_abs_difference_pp": summarize(gaps),
        "note": "Computed over rounds that attempted the whole suite. A round "
                "that attempted fewer tasks is still in progress and is "
                "excluded, because the tasks it has not reached are not a "
                "random subset. A round that attempted every task but scored "
                "fewer -- an outage, say -- is KEPT and flagged with "
                "reduced_denominator, since dropping it would hide exactly the "
                "rounds an incident touched.",
    }


# ----------------------------------------------------------------- resampling

def _bernoulli_suite(rng: random.Random, ps: list[float], n_resamples: int) -> Counter:
    """Simulate n_resamples independent runs of a suite with these pass rates."""
    hist: Counter = Counter()
    for _ in range(n_resamples):
        hist[sum(1 for p in ps if rng.random() < p)] += 1
    return hist


def _hist_stats(hist: Counter, n_slots: int) -> dict:
    total = sum(hist.values())
    xs = []
    for passes, freq in sorted(hist.items()):
        xs.extend([100.0 * passes / n_slots] * freq)
    lo, hi = pct(xs, 0.025), pct(xs, 0.975)
    return {
        "n_slots": n_slots,
        "n_resamples": total,
        "mean_pct": round(statistics.fmean(xs), 4),
        "sd_pp": round(statistics.stdev(xs), 4),
        "ci95_pct": [round(lo, 4), round(hi, 4)],
        "ci95_width_pp": round(hi - lo, 4),
        "counts_hist": {str(k): v for k, v in sorted(hist.items())},
    }


def rerun_distributions(tasks: list[dict], n_resamples: int, seed: int) -> dict:
    """Where would the total score land on an independent re-run?

    Two variants. `plugin` treats each measured rate as truth; `posterior`
    first draws the rate from its Jeffreys posterior Beta(k+.5, n-k+.5), which
    is the honest version -- 10 trials do not pin a rate down.
    """
    usable = [t for t in tasks if t["n_scored"] > 0]
    ks = [t["passes"] for t in usable]
    ns = [t["n_scored"] for t in usable]
    n_slots = len(usable)

    rng = random.Random(seed)
    plugin = _bernoulli_suite(rng, [k / n for k, n in zip(ks, ns)], n_resamples)

    rng = random.Random(seed + 1)
    post: Counter = Counter()
    for _ in range(n_resamples):
        c = 0
        for k, n in zip(ks, ns):
            p = rng.betavariate(k + 0.5, n - k + 0.5)
            if rng.random() < p:
                c += 1
        post[c] += 1

    return {
        "plugin": {**_hist_stats(plugin, n_slots),
                   "definition": "Bernoulli(p_hat) per task. Lower bound: it "
                                 "pretends 10 trials measured p exactly."},
        "posterior": {**_hist_stats(post, n_slots),
                      "definition": "p drawn from Jeffreys posterior "
                                    "Beta(k+0.5, n-k+0.5), then Bernoulli(p). "
                                    "Headline: includes the fact that p itself "
                                    "is only estimated."},
    }


def estimate_bootstrap(tasks: list[dict], n_resamples: int, seed: int) -> dict:
    """Nonparametric bootstrap of *our own* suite pass rate.

    Resamples trials with replacement inside each task. Answers "how precise is
    the number this experiment produced", which is not the same question as
    "how much would a re-run move".
    """
    rng = random.Random(seed + 2)
    outcomes = [[1 if t_["pass"] else 0
                 for t_ in t["trial_outcomes"] if t_["pass"] is not None]
                for t in tasks if t["n_scored"] > 0]
    if not outcomes:
        return {"n_resamples": 0}
    xs = []
    for _ in range(n_resamples):
        acc = 0.0
        for obs in outcomes:
            acc += statistics.fmean(rng.choices(obs, k=len(obs)))
        xs.append(100.0 * acc / len(outcomes))
    lo, hi = pct(xs, 0.025), pct(xs, 0.975)
    return {
        "n_resamples": n_resamples,
        "mean_pct": round(statistics.fmean(xs), 4),
        "sd_pp": round(statistics.stdev(xs), 4),
        "ci95_pct": [round(lo, 4), round(hi, 4)],
        "ci95_width_pp": round(hi - lo, 4),
        "definition": "Trials resampled with replacement within each task; "
                      "the spread is the precision of this experiment's own "
                      "estimate, not the spread of a future run.",
    }


def _fill_slots(ps: list[float], n_slots: int) -> list[float]:
    """Spread len(ps) measured rates over n_slots task slots, round-robin.

    Used to carry a stratum's measured rates up to the stratum's real size
    without inventing tasks: each measured task simply stands for the same
    number of slots, +/- one.
    """
    if not ps:
        return []
    return [ps[i % len(ps)] for i in range(n_slots)]


def suite_extrapolation(tasks: list[dict], strata: dict[str, int],
                        n_resamples: int, seed: int) -> dict | None:
    """Carry the measured rates up to the full GUI-Only suite.

    The pilot is deliberately not a random sample of the 117 GUI-Only tasks: it
    is 10 of the 98 tasks published runs disagree on, plus *every* task they
    all pass (9) and all fail (10). So the pilot's own total is not comparable
    in level to a leaderboard score -- but with the stratum sizes known, the
    measured rates can be reweighted back to 117 tasks, and *that* is.

    Two variants:
      fixed_taskset   the 117 slots are fixed; only trial-to-trial noise moves
                      the score. Comparable to a leaderboard gap.
      resampled_tasks additionally resamples which split-bucket tasks are in
                      the suite, i.e. also asks "what if the other 88 split
                      tasks behave like these 10". Wider, and about
                      generalisation rather than about noise.
    """
    by_bucket: dict[str, list[float]] = defaultdict(list)
    for t in tasks:
        if t["n_scored"] > 0 and t["bucket"]:
            by_bucket[t["bucket"]].append(t["pass_rate"])
    if not by_bucket or not strata:
        return None
    missing = [b for b in strata if b not in by_bucket]
    if missing:
        return None

    total_slots = sum(strata.values())

    fixed_ps: list[float] = []
    for bucket, size in sorted(strata.items()):
        fixed_ps += _fill_slots(sorted(by_bucket[bucket]), size)

    rng = random.Random(seed + 3)
    fixed = _bernoulli_suite(rng, fixed_ps, n_resamples)
    # The spread of a sum of independent Bernoullis needs no simulation. The
    # simulated SD above carries Monte Carlo noise (about +/-0.03 pp at 10,000
    # draws; the submitted version printed 2.9412 against an exact 2.9124), so
    # the threshold is computed from this exact value.
    sd_exact = 100.0 * math.sqrt(sum(p * (1 - p) for p in fixed_ps)) / total_slots

    rng = random.Random(seed + 4)
    resampled: Counter = Counter()
    for _ in range(n_resamples):
        c = 0
        for bucket, size in sorted(strata.items()):
            pool = by_bucket[bucket]
            if len(pool) >= size:          # bucket measured in full: a census
                ps = pool[:size]
            else:
                ps = rng.choices(pool, k=size)
            c += sum(1 for p in ps if rng.random() < p)
        resampled[c] += 1

    return {
        "strata_sizes": strata,
        "strata_measured": {b: len(v) for b, v in sorted(by_bucket.items())},
        "strata_unmeasured": {b: strata[b] - len(by_bucket[b]) for b in sorted(strata)},
        "total_slots": total_slots,
        "fixed_taskset": {**_hist_stats(fixed, total_slots),
                          "sd_pp_exact": round(sd_exact, 4),
                          "definition": "Stratum rates carried to their real "
                                        "sizes; only Bernoulli noise. This is "
                                        "the width to compare with a "
                                        "leaderboard gap."},
        "resampled_tasks": {**_hist_stats(resampled, total_slots),
                            "definition": "Also resamples which split-bucket "
                                          "tasks are in the suite. Wider; it "
                                          "answers a different question."},
        "caveat": "An extrapolation, not a measurement. It assumes the 10 "
                  "measured split-bucket tasks are representative of the other "
                  "88, and that one model's instability is informative about "
                  "another's.",
    }


def regime_comparison(tasks: list[dict], rows: list[dict], boundary: int,
                      traj_stats: dict | None) -> dict:
    """Compare trials before `boundary` with trials from `boundary` on.

    The boundary is supplied by the analyst, never fitted. Searching for the
    split that maximises a difference would manufacture one out of noise; the
    only defensible use of this is "something identifiable happened between
    trial N-1 and trial N, here is the before and after".

    Reports a sign test over per-task changes, because the question that
    separates an incident from noise is not "did the score move" but "did
    everything move the same way".
    """
    early_t = [r for r in rows if (r.get("trial_index") or 0) < boundary]
    late_t = [r for r in rows if (r.get("trial_index") or 0) >= boundary]
    if not early_t or not late_t:
        return {"applicable": False,
                "reason": f"boundary {boundary} leaves one side empty"}

    # A trial still in progress has a short denominator and would contribute a
    # meaningless score; the main per-trial series already excludes those and
    # this must agree with it or the report quotes two different numbers.
    per_complete = max(
        (len([r for r in rows if r.get("trial_index") == t])
         for t in {r.get("trial_index") for r in rows}), default=0)

    def suite_scores(rs: list[dict]) -> tuple[list[float], list[int]]:
        by_trial: dict[int, list[dict]] = defaultdict(list)
        for r in rs:
            by_trial[r.get("trial_index")].append(r)
        out, skipped = [], []
        for t in sorted(by_trial):
            if len(by_trial[t]) < per_complete:
                skipped.append(t)
                continue
            scored = [x for x in by_trial[t] if is_scored(x)]
            if scored:
                out.append(100 * sum(1 for x in scored if passed(x)) / len(scored))
        return out, skipped

    early_scores, early_skipped = suite_scores(early_t)
    late_scores, late_skipped = suite_scores(late_t)

    # Per task: pass rate before vs after, and which way it moved.
    worse = better = same = 0
    moved = []
    for t in tasks:
        e = [r for r in early_t if r["task_id"] == t["task_id"] and is_scored(r)]
        l = [r for r in late_t if r["task_id"] == t["task_id"] and is_scored(r)]
        if not e or not l:
            continue
        re_ = sum(1 for r in e if passed(r)) / len(e)
        rl = sum(1 for r in l if passed(r)) / len(l)
        if rl < re_:
            worse += 1
        elif rl > re_:
            better += 1
        else:
            same += 1
            continue
        moved.append({"task_id": t["task_id"], "bucket": t["bucket"],
                      "before": round(re_, 4), "after": round(rl, 4),
                      "delta_pp": round(100 * (rl - re_), 2)})

    d = worse + better
    p_sign = (sum(math.comb(d, k) for k in range(min(worse, better) + 1))
              / 2 ** d * 2) if d else 1.0

    block = {
        "applicable": True,
        "boundary_trial": boundary,
        "note": "The boundary is supplied, not fitted. See the report for what "
                "happened at it.",
        "before": {"trials": sorted({r.get("trial_index") for r in early_t}),
                   "suite_scores_pct": [round(x, 2) for x in early_scores],
                   "incomplete_trials_excluded": early_skipped,
                   "n_runs": len(early_t)},
        "after": {"trials": sorted({r.get("trial_index") for r in late_t}),
                  "suite_scores_pct": [round(x, 2) for x in late_scores],
                  "incomplete_trials_excluded": late_skipped,
                  "n_runs": len(late_t)},
        "tasks_worse": worse,
        "tasks_better": better,
        "tasks_unchanged": same,
        "sign_test_p": round(min(p_sign, 1.0), 6),
        "sign_test_meaning": "Probability of a split this one-sided if the two "
                             "regimes differed only by chance. Small means "
                             "something changed, not that the score is noisy.",
        "tasks_that_moved": sorted(moved, key=lambda m: m["delta_pp"]),
    }

    # Reasoning volume per action is the quantity that exposes a serving change;
    # it lives in traj_stats.json because it needs the raw artifacts.
    if traj_stats:
        per_trial = ((traj_stats.get("by_arm") or {})
                     .get(rows[0].get("arm"), {})
                     .get("per_trial_completion_per_step") or {})
        if per_trial:
            e = [v["median"] for k, v in per_trial.items() if int(k) < boundary]
            l = [v["median"] for k, v in per_trial.items() if int(k) >= boundary]
            if e and l:
                block["reasoning_tokens_per_action"] = {
                    "per_trial_median": {k: v["median"]
                                         for k, v in sorted(per_trial.items(),
                                                            key=lambda kv: int(kv[0]))},
                    "before_median_of_medians": round(statistics.median(e), 2),
                    "after_median_of_medians": round(statistics.median(l), 2),
                    "change_pct": round(100 * (statistics.median(l) /
                                               statistics.median(e) - 1), 2),
                    # Signed for arithmetic, unsigned for prose: "dropped
                    # -20.1%" is a double negative wherever the verb already
                    # carries the direction.
                    "change_pct_abs": abs(round(100 * (statistics.median(l) /
                                                       statistics.median(e) - 1), 2)),
                    "source": "data/traj_stats.json (scripts/scan_trajectories.py)",
                }
    return block


def leave_one_out(arm_rows: list[dict], meta: dict, strata: dict[str, int],
                  matrix_path: Path, n_resamples: int, seed: int,
                  analyse) -> dict:
    """Recompute the headline with each repetition round dropped in turn.

    A reader's first question about any round that caught an incident is
    "does the result survive without it". Answering it for one round invites
    the suspicion that the round was chosen after seeing the answer, so it is
    answered for every round.
    """
    trials = sorted({r.get("trial_index") for r in arm_rows
                     if r.get("trial_index") is not None})
    variants = []
    for t in trials:
        kept = [r for r in arm_rows if r.get("trial_index") != t]
        if len({r.get("trial_index") for r in kept}) < 2:
            continue
        a = analyse(kept)
        e = a.get("gui_only_extrapolation") or {}
        variants.append({
            "dropped_trial": t,
            "n_trials_left": len({r.get("trial_index") for r in kept}),
            "observed_range_pp": a["per_trial_suite_score"]["observed_range_pp"],
            "mdd95_pp": a["detectable_difference"]["mdd95_pp"],
            "ci95_width_pp": (e.get("fixed_taskset") or {}).get("ci95_width_pp"),
            "n_flipped": a["flips"]["n_flipped"],
            "leaderboard_adjacent_within_noise":
                (a.get("leaderboard_gaps") or {}).get("n_adjacent_within_noise"),
        })
    mdds = [v["mdd95_pp"] for v in variants if v["mdd95_pp"] is not None]
    ranges = [v["observed_range_pp"] for v in variants
              if v["observed_range_pp"] is not None]
    return {
        "method": "Each repetition round dropped in turn; every figure "
                  "recomputed from scratch on what is left.",
        "variants": variants,
        "mdd95_pp_min": round(min(mdds), 4) if mdds else None,
        "mdd95_pp_max": round(max(mdds), 4) if mdds else None,
        "observed_range_pp_min": round(min(ranges), 4) if ranges else None,
        "observed_range_pp_max": round(max(ranges), 4) if ranges else None,
        "leaderboard_adjacent_within_noise_min": min(
            (v["leaderboard_adjacent_within_noise"] for v in variants
             if v["leaderboard_adjacent_within_noise"] is not None), default=None),
        "leaderboard_adjacent_within_noise_max": max(
            (v["leaderboard_adjacent_within_noise"] for v in variants
             if v["leaderboard_adjacent_within_noise"] is not None), default=None),
    }


def detectable_difference(sd_pp: float) -> dict:
    """How large must a gap be before it means anything?

    Two models compared by one run each: the difference has SD sd*sqrt(2).
    """
    diff_sd = sd_pp * math.sqrt(2)
    return {
        "single_run_sd_pp": round(sd_pp, 4),
        "difference_sd_pp": round(diff_sd, 4),
        "mdd95_pp": round(Z95 * diff_sd, 4),
        "mdd95_power80_pp": round((Z95 + Z80_POWER) * diff_sd, 4),
        "definition": {
            "mdd95_pp": "A gap smaller than this between two single runs is "
                        "within what noise alone produces 95% of the time when "
                        "the two models are in fact equal.",
            "mdd95_power80_pp": "The gap needed for a single-run comparison to "
                                "come out significant 80% of the time.",
        },
    }


def leaderboard_gaps(matrix_path: Path, mdd_pp: float) -> dict | None:
    if not matrix_path.exists():
        return None
    matrix = json.loads(matrix_path.read_text())
    entries = sorted(
        ((name, m["leaderboard_gui_only"]) for name, m in matrix["models"].items()
         if isinstance(m.get("leaderboard_gui_only"), (int, float))),
        key=lambda kv: -kv[1])
    adjacent = []
    for (n1, s1), (n2, s2) in zip(entries, entries[1:]):
        gap = round(s1 - s2, 4)
        adjacent.append({"higher": n1, "lower": n2, "gap_pp": gap,
                         "within_noise": gap < mdd_pp})
    all_pairs = list(itertools.combinations(entries, 2))
    within = sum(1 for (_, s1), (_, s2) in all_pairs if abs(s1 - s2) < mdd_pp)
    # The board is larger than the set of models that published a trajectory
    # bundle, so how many entries are single runs is counted from the
    # leaderboard itself, not from `entries`.
    runs_dist = {str(k): v for k, v in
                 (matrix.get("leaderboard_runs_distribution") or {}).items()}
    n_board = sum(runs_dist.values())

    return {
        "source": repo_path(matrix_path),
        "mdd95_pp_applied": round(mdd_pp, 4),
        "n_entries": len(entries),
        "n_entries_on_board": n_board,
        "runs_distribution": runs_dist,
        "n_single_run_entries": runs_dist.get("1", 0),
        "n_repeated_entries": n_board - runs_dist.get("1", 0),
        "n_adjacent_pairs": len(adjacent),
        "entries": [{"model": n, "gui_only": s} for n, s in entries],
        "adjacent_pairs": adjacent,
        "n_adjacent_within_noise": sum(1 for a in adjacent if a["within_noise"]),
        "n_all_pairs": len(all_pairs),
        "n_all_pairs_within_noise": within,
        "caveat": "The noise floor was measured for one model on a 29-task "
                  "pilot and is applied here to entries produced by other "
                  "agents, other models and (see the report) other verifier "
                  "versions. It is an indication of scale, not a re-scoring.",
    }


# ----------------------------------------------------------------------- arm

# An arm with a handful of runs -- a smoke test, say -- still produces every
# statistic in this file, and every one of them is meaningless. Rather than let
# a 2-task, 2-trial arm emit a minimum detectable difference of 70 points and a
# claim about the leaderboard, arms below these thresholds are marked and the
# leaderboard comparison is withheld. render_report.py then cannot build a
# report out of one, because the placeholders it needs are absent.
MIN_TASKS_FOR_CLAIMS = 5
MIN_TRIALS_FOR_CLAIMS = 3


def power_check(tasks: list[dict]) -> dict:
    usable = [t for t in tasks if t["n_scored"] > 0]
    min_trials = min((t["n_scored"] for t in usable), default=0)
    reasons = []
    if len(usable) < MIN_TASKS_FOR_CLAIMS:
        reasons.append(f"only {len(usable)} task(s) produced a score "
                       f"(need {MIN_TASKS_FOR_CLAIMS})")
    if min_trials < MIN_TRIALS_FOR_CLAIMS:
        reasons.append(f"a task has only {min_trials} scored trial(s) "
                       f"(need {MIN_TRIALS_FOR_CLAIMS})")
    return {
        "sufficient": not reasons,
        "reasons": reasons,
        "n_tasks_scored": len(usable),
        "min_scored_trials_per_task": min_trials,
        "note": "Per-task numbers are always valid. The suite-level interval, "
                "the minimum detectable difference and the leaderboard "
                "comparison are only reported when this passes.",
    }


def analyse_arm(arm: str, rows: list[dict], meta: dict, strata: dict[str, int],
                matrix_path: Path, n_resamples: int, seed: int) -> dict:
    tasks = per_task(rows, meta)
    usable = [t for t in tasks if t["n_scored"] > 0]
    power = power_check(tasks)

    flips = sorted((t for t in tasks if t["flip_rate"] is not None),
                   key=lambda t: (-t["flip_rate"], t["task_id"]))
    # The commonest number of scored trials, so the report can state the
    # ceiling for the n it actually ran rather than for a round number.
    _counts = Counter(t["n_scored"] for t in tasks if t["n_scored"] >= 2)
    _typical_n = _counts.most_common(1)[0][0] if _counts else None
    rerun = rerun_distributions(tasks, n_resamples, seed)
    extrap = suite_extrapolation(tasks, strata, n_resamples, seed)

    # The width that gets compared with leaderboard gaps comes from the
    # 117-task extrapolation when it is available, and from the pilot itself
    # otherwise -- a 29-task suite is noisier, so falling back overstates.
    if extrap:
        sd_for_mdd = extrap["fixed_taskset"]["sd_pp_exact"]
        sd_basis = "gui_only_extrapolation.fixed_taskset (exact Bernoulli SD)"
    else:
        sd_for_mdd = rerun["posterior"]["sd_pp"]
        sd_basis = "rerun.posterior (pilot scale -- overstates a 117-task suite)"
    mdd = {**detectable_difference(sd_for_mdd), "basis": sd_basis}
    if not power["sufficient"]:
        mdd["not_reportable"] = ("; ".join(power["reasons"])
                                 + " -- this number is arithmetic, not evidence")

    by_bucket = defaultdict(list)
    for t in usable:
        by_bucket[t["bucket"] or "unknown"].append(t)

    randomized = [t for t in usable if t.get("randomized_setup")]

    return {
        "arm": arm,
        "n_rows": len(rows),
        "n_tasks": len(tasks),
        "trials_per_task": summarize([float(t["n_trials"]) for t in tasks]),
        "outcomes": dict(Counter(r.get("outcome") for r in rows)),
        # Unscored runs cluster when the cause is an incident rather than a
        # task; reporting them per trial is what separates the two.
        "outcomes_by_trial": {
            str(t): dict(Counter(r.get("outcome") for r in rows
                                 if r.get("trial_index") == t))
            for t in sorted({r.get("trial_index") for r in rows
                             if r.get("trial_index") is not None})},
        "provenance": check_provenance(rows),
        "per_trial_suite_score": per_trial_suite_scores(rows),
        "tasks": tasks,
        "flips": {
            "n_tasks_with_2plus_scored_trials": sum(1 for t in tasks if t["n_scored"] >= 2),
            "n_flipped": sum(1 for t in tasks if t["flipped"]),
            "flipped_task_ids": [t["task_id"] for t in tasks if t["flipped"]],
            "mean_flip_rate": round(statistics.fmean([t["flip_rate"] for t in flips]), 4)
                              if flips else None,
            "ranked_by_instability": [
                {"task_id": t["task_id"], "bucket": t["bucket"],
                 "passes": t["passes"], "n_scored": t["n_scored"],
                 "pass_rate": t["pass_rate"], "flip_rate": t["flip_rate"],
                 "wilson95": t["wilson95"]}
                for t in flips
            ],
            "n_trials_typical": _typical_n,
            "flip_rate_max_typical": (round(flip_rate_max(_typical_n), 4)
                                      if _typical_n and _typical_n >= 2 else None),
            "definition": "flip_rate = discordant trial pairs / all trial pairs "
                          "= k(n-k) / C(n,2). Its maximum is below 1 and "
                          "depends on n; flip_rate_max is recorded per task.",
        },
        "rerun": rerun,
        "estimate": estimate_bootstrap(tasks, n_resamples, seed),
        "gui_only_extrapolation": extrap,
        "detectable_difference": mdd,
        "statistical_power": power,
        "leaderboard_gaps": (leaderboard_gaps(matrix_path, mdd["mdd95_pp"])
                             if power["sufficient"] else None),
        "leaderboard_gaps_withheld": (None if power["sufficient"]
                                      else "; ".join(power["reasons"])),
        "subgroups": {
            "by_bucket": {
                b: {"n_tasks": len(ts),
                    "mean_pass_rate": round(statistics.fmean([t["pass_rate"] for t in ts]), 4),
                    "n_flipped": sum(1 for t in ts if t["flipped"]),
                    "mean_flip_rate": round(statistics.fmean(
                        [t["flip_rate"] for t in ts if t["flip_rate"] is not None]), 4)
                        if any(t["flip_rate"] is not None for t in ts) else None}
                for b, ts in sorted(by_bucket.items())
            },
            "randomized_setup": {
                "note": "7 tasks randomise their own initial state and nothing "
                        "in the codebase seeds the RNG (ENVIRONMENT.md §5). These "
                        "measure instability of a task that is literally "
                        "different each run -- a different quantity from the "
                        "rest, so they are never pooled with them.",
                "task_ids": [t["task_id"] for t in randomized],
                "n_tasks": len(randomized),
                "n_flipped": sum(1 for t in randomized if t["flipped"]),
                "mean_flip_rate": round(statistics.fmean(
                    [t["flip_rate"] for t in randomized if t["flip_rate"] is not None]), 4)
                    if any(t["flip_rate"] is not None for t in randomized) else None,
            },
        },
        "absorbed_retries": {
            "total": sum(t["absorbed_retries"] for t in tasks),
            "by_task": {t["task_id"]: t["absorbed_retries"] for t in tasks
                        if t["absorbed_retries"]},
            # Per trial as well as per task: an infrastructure incident hits one
            # repetition round, not one task, so concentration by trial is what
            # makes it visible. Arm A trial 6 caught an hour-long provider
            # authentication outage this way.
            "by_trial": {str(k): v for k, v in sorted(
                Counter({t: sum(r.get("absorbed_retries") or 0
                                for r in rows if r.get("trial_index") == t)
                         for t in {r.get("trial_index") for r in rows}}).items())},
            "max_trial_share": (
                round(max((sum(r.get("absorbed_retries") or 0 for r in rows
                               if r.get("trial_index") == t)
                           for t in {r.get("trial_index") for r in rows}), default=0)
                      / max(1, sum(t["absorbed_retries"] for t in tasks)), 4)),
            "note": "Backup directories left behind when the harness silently "
                    "retried inside a task (ENVIRONMENT.md §2.2). Every one of "
                    "these is instability that was absorbed before it could "
                    "reach a score, so the measured variance is a floor.",
        },
        "duration_s_total": round(sum(r.get("duration_s") or 0 for r in rows), 2),
    }


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default=str(ROOT / "data" / "runs.jsonl"))
    ap.add_argument("--pilot", default=str(ROOT / "data" / "pilot_tasks.json"))
    ap.add_argument("--matrix", default=str(ROOT / "data" / "public_matrix.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "analysis.json"))
    ap.add_argument("--arm", action="append", default=None,
                    help="Restrict to these arms (repeatable). Default: all.")
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--trials", default=None, metavar="FROM:TO",
                    help="Restrict to a trial range, e.g. 4:10. The headline "
                         "noise floor has to be computed inside ONE serving "
                         "regime; pooling across a configuration change "
                         "measures the change, not the noise.")
    ap.add_argument("--leave-one-out", action="store_true",
                    help="Recompute the headline with each round dropped in "
                         "turn, and report the range. The robustness check a "
                         "reader asks for first.")
    ap.add_argument("--exclude-trials", default=None, metavar="N,M",
                    help="Drop these trials. For sensitivity checks: a round "
                         "hit by an incident has a different denominator and a "
                         "different failure profile, and the honest thing is to "
                         "report the headline with and without it.")
    ap.add_argument("--only-tasks", default=None, metavar="FILE",
                    help="Restrict to the task ids in FILE, one per line. Used "
                         "to build a balanced panel when an incident cost some "
                         "tasks their score in one round.")
    ap.add_argument("--regime-split", type=int, default=None, metavar="TRIAL",
                    help="Compare trials before TRIAL with TRIAL onward. The "
                         "boundary must come from something identifiable that "
                         "happened, never from searching for the biggest gap.")
    ap.add_argument("--traj-stats", default=str(ROOT / "data" / "traj_stats.json"),
                    help="Optional, from scripts/scan_trajectories.py; adds the "
                         "per-trial reasoning-length series to the comparison.")
    ap.add_argument("--seed", type=int, default=20260824,
                    help="Resampling is seeded so the same runs.jsonl always "
                         "produces the same analysis.json.")
    args = ap.parse_args()

    runs_path = Path(args.runs)
    if not runs_path.exists():
        print(f"{runs_path} does not exist -- no evaluation has been run yet.")
        return 2

    rows, problems = load_rows(runs_path)
    rows, dups = dedupe(rows)
    if not rows:
        print(f"{runs_path} is empty")
        return 2

    meta, strata = {}, {}
    pilot_path = Path(args.pilot)
    if pilot_path.exists():
        pilot = json.loads(pilot_path.read_text())
        for bucket, items in pilot["buckets"].items():
            for it in items:
                meta[it["task_id"]] = {**it, "bucket": bucket}
        # The real GUI-Only stratum sizes, needed to reweight the pilot back to
        # the 117-task suite the leaderboard reports.
        strata = {k: v for k, v in (pilot.get("selection", {})
                                    .get("available", {}) or {}).items()}

    trial_lo, trial_hi = None, None
    all_rows_by_arm: dict[str, list[dict]] = {}
    if args.trials:
        try:
            lo, hi = args.trials.split(":")
            trial_lo, trial_hi = int(lo), int(hi)
        except ValueError:
            print(f"--trials expects FROM:TO, got {args.trials!r}", file=sys.stderr)
            return 2
        all_rows_by_arm: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            all_rows_by_arm[r.get("arm")].append(r)
        rows = [r for r in rows
                if trial_lo <= (r.get("trial_index") or 0) <= trial_hi]
        if not rows:
            print(f"no rows in trial range {args.trials}", file=sys.stderr)
            return 2

    if args.exclude_trials:
        drop = {int(x) for x in args.exclude_trials.split(",") if x.strip()}
        rows = [r for r in rows if r.get("trial_index") not in drop]
        out_excluded = sorted(drop)
    else:
        out_excluded = []

    if args.only_tasks:
        keep = {ln.strip() for ln in Path(args.only_tasks).read_text().splitlines()
                if ln.strip() and not ln.startswith("#")}
        rows = [r for r in rows if r.get("task_id") in keep]
        if not rows:
            print(f"no rows left after --only-tasks {args.only_tasks}", file=sys.stderr)
            return 2

    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r.get("arm")].append(r)
    wanted = args.arm or sorted(by_arm)

    out = {
        "generated_at": now_iso(),
        "generator": "scripts/analyze.py",
        "inputs": {
            "runs_jsonl": repo_path(runs_path),
            "runs_sha256": sha256_file(runs_path),
            "n_rows_read": len(rows) + len(dups),
            "n_rows_used": len(rows),
            "duplicate_keys": dups,
            "unparseable_lines": problems,
            "pilot_tasks": repo_path(pilot_path) if pilot_path.exists() else None,
            "public_matrix": (repo_path(Path(args.matrix))
                              if Path(args.matrix).exists() else None),
        },
        "settings": {
            "trial_range": ([trial_lo, trial_hi] if trial_lo is not None else None),
            "trials_excluded": out_excluded,
            "tasks_restricted_to": args.only_tasks,
            "pass_threshold": PASS_THRESHOLD,
            "bootstrap_resamples": args.bootstrap,
            "seed": args.seed,
            "interval": "Wilson score, 95%",
        },
        "arms": {},
    }
    traj_stats = None
    tp = Path(args.traj_stats)
    if tp.exists():
        try:
            traj_stats = json.loads(tp.read_text())
            out["inputs"]["traj_stats"] = repo_path(tp)
        except json.JSONDecodeError:
            pass

    for arm in wanted:
        if arm not in by_arm:
            print(f"arm {arm!r} not present in {runs_path}")
            continue
        a = analyse_arm(arm, by_arm[arm], meta, strata,
                        Path(args.matrix), args.bootstrap, args.seed)
        if args.leave_one_out:
            a["leave_one_out"] = leave_one_out(
                by_arm[arm], meta, strata, Path(args.matrix), args.bootstrap,
                args.seed,
                lambda rs: analyse_arm(arm, rs, meta, strata, Path(args.matrix),
                                       args.bootstrap, args.seed))
        if args.regime_split:
            # Deliberately computed over every trial, not just the filtered
            # range: the headline belongs to one regime, but the comparison is
            # about the boundary between two.
            a["regime_comparison"] = regime_comparison(
                a["tasks"], all_rows_by_arm.get(arm) or by_arm[arm],
                args.regime_split, traj_stats)
            a["regime_comparison"]["headline_regime"] = (
                [trial_lo, trial_hi] if trial_lo is not None else "pooled")
        out["arms"][arm] = a

    if len(out["arms"]) >= 2 and {"A_temp0", "B_default"} <= set(out["arms"]):
        a = out["arms"]["A_temp0"]["rerun"]["posterior"]["sd_pp"]
        b = out["arms"]["B_default"]["rerun"]["posterior"]["sd_pp"]
        out["arm_comparison"] = {
            "A_temp0_sd_pp": a,
            "B_default_sd_pp": b,
            "difference_pp": round(b - a, 4),
            "definition": "What default sampling ADDS on top of arm A. Not "
                          "'the model's contribution': temperature=0 with a "
                          "fixed seed was measured to leave substantial "
                          "sampling variance in place (ENVIRONMENT.md §12), so A "
                          "already contains an unknown share of it and the "
                          "difference is a lower bound on the model's part.",
        }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    for arm, a in out["arms"].items():
        p = a["per_trial_suite_score"]["summary"]
        print(f"\n=== {arm} ===")
        if not a["statistical_power"]["sufficient"]:
            print("  !! not enough data for suite-level claims: "
                  + "; ".join(a["statistical_power"]["reasons"]))
        print(f"  rows {a['n_rows']}  tasks {a['n_tasks']}  outcomes {a['outcomes']}")
        print(f"  provenance consistent: {a['provenance']['consistent']}")
        pts = a["per_trial_suite_score"]
        print(f"  observed per-trial suite score: {p.get('min')}..{p.get('max')} "
              f"(range {pts['observed_range_pp']} pp) "
              f"over {pts['n_trials_complete']} complete trial(s)"
              + (f", {pts['n_trials_partial']} partial excluded"
                 if pts["n_trials_partial"] else ""))
        print(f"  tasks that flipped: {a['flips']['n_flipped']}/"
              f"{a['flips']['n_tasks_with_2plus_scored_trials']}")
        print(f"  re-run 95% interval (pilot scale): "
              f"{a['rerun']['posterior']['ci95_pct']} "
              f"width {a['rerun']['posterior']['ci95_width_pp']} pp")
        if a["gui_only_extrapolation"]:
            f = a["gui_only_extrapolation"]["fixed_taskset"]
            print(f"  extrapolated to 117 GUI-Only tasks: {f['ci95_pct']} "
                  f"width {f['ci95_width_pp']} pp")
        print(f"  minimum detectable difference (95%): "
              f"{a['detectable_difference']['mdd95_pp']} pp  [{a['detectable_difference']['basis']}]")
        if a["leaderboard_gaps"]:
            g = a["leaderboard_gaps"]
            print(f"  leaderboard: {g['n_adjacent_within_noise']}/"
                  f"{len(g['adjacent_pairs'])} adjacent gaps inside that")
        else:
            print(f"  leaderboard comparison withheld: "
                  f"{a['leaderboard_gaps_withheld']}")
        print(f"  absorbed retries: {a['absorbed_retries']['total']}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
