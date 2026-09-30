#!/usr/bin/env python3
"""Generate the paper's appendix tables as LaTeX, from the analysis files.

Appendices are uncounted pages, so there is no reason for them to be prose
describing a table that exists only as JSON. These are emitted rather than
typed so they cannot drift from the data, in the same way the body's numbers
are audited rather than trusted.

    python3 scripts/make_appendix_tables.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "paper" / "sections" / "tables"

BUCKET = {"split": "disagreed", "all_pass": "all-pass", "all_fail": "all-fail"}


def esc(s: str) -> str:
    return s.replace("_", r"\_")


def load(name):
    return json.loads((ROOT / "data" / name).read_text())["arms"]["A_temp0"]


def per_task_table(a) -> str:
    rows = sorted((t for t in a["tasks"] if t["n_scored"]),
                  key=lambda t: (-(t["flip_rate"] or 0), -t["pass_rate"], t["task_id"]))
    out = [r"\begin{tabular}{llccccc}", r"\toprule",
           r"task & group & passed & pass rate & Wilson 95\% & flip rate & steps (median) \\",
           r"\midrule"]
    for t in rows:
        lo, hi = t["wilson95"]
        st = t["steps"].get("median")
        out.append(r"\texttt{%s} & %s & %d/%d & %.0f\%% & %.0f--%.0f\%% & %.3f & %s \\" % (
            esc(t["task_id"]), BUCKET.get(t["bucket"], t["bucket"] or "?"),
            t["passes"], t["n_scored"], 100 * t["pass_rate"], 100 * lo, 100 * hi,
            t["flip_rate"], ("%.0f" % st) if st is not None else "--"))
    out += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(out)


def stable_table(a) -> str:
    rows = [t for t in a["tasks"] if t["n_scored"] and not t["flipped"]]
    rows.sort(key=lambda t: (t["bucket"] or "", t["task_id"]))
    out = [r"\begin{tabular}{llcl}", r"\toprule",
           r"task & group & passed & outcome \\", r"\midrule"]
    for t in rows:
        out.append(r"\texttt{%s} & %s & %d/%d & %s \\" % (
            esc(t["task_id"]), BUCKET.get(t["bucket"], t["bucket"] or "?"),
            t["passes"], t["n_scored"],
            "always passed" if t["pass_rate"] == 1 else "always failed"))
    out += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(out)


def retries_table(rows_path: Path) -> tuple[str, str]:
    """By round and by task, over the whole arm -- the incident is the point."""
    by_round, by_task = {}, {}
    for line in rows_path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("arm") != "A_temp0":
            continue
        n = r.get("absorbed_retries") or 0
        by_round[r["trial_index"]] = by_round.get(r["trial_index"], 0) + n
        if n:
            by_task[r["task_id"]] = by_task.get(r["task_id"], 0) + n

    a = [r"\begin{tabular}{l" + "c" * len(by_round) + "}", r"\toprule",
         "round & " + " & ".join(str(k) for k in sorted(by_round)) + r" \\",
         "silent restarts & " + " & ".join(
             (r"\textbf{%d}" % v if v == max(by_round.values()) else str(v))
             for _, v in sorted(by_round.items())) + r" \\",
         r"\bottomrule", r"\end{tabular}"]

    b = [r"\begin{tabular}{lc}", r"\toprule", r"task & silent restarts \\", r"\midrule"]
    for k, v in sorted(by_task.items(), key=lambda kv: (-kv[1], kv[0])):
        b.append(r"\texttt{%s} & %d \\" % (esc(k), v))
    b += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(a), "\n".join(b)


def sensitivity_tables(S) -> tuple[str, str, str]:
    """Appendix E: the round-6 comparison, as numbers rather than a promise."""
    w, n = S["with_round6"], S["without_round6"]
    rows = [
        ("complete rounds", str(w["n_trials"]), str(n["n_trials"])),
        ("best $-$ worst spread (pp)", "%.2f" % w["best_worst_spread_pp"],
         "%.2f" % n["best_worst_spread_pp"]),
        (r"95\% interval, 29-task scale",
         r"%.1f--%.1f\%%" % tuple(w["headline_ci95_pct"]),
         r"%.1f--%.1f\%%" % tuple(n["headline_ci95_pct"])),
        (r"$\sigma$, 29-task scale (pp)", "%.2f" % w["headline_sd_pp"],
         "%.2f" % n["headline_sd_pp"]),
        (r"95\% interval, 117-task scale",
         r"%.1f--%.1f\%%" % tuple(w["extrapolated_117_ci95_pct"]),
         r"%.1f--%.1f\%%" % tuple(n["extrapolated_117_ci95_pct"])),
        (r"$\sigma$, 117-task scale (pp)", "%.2f" % w["extrapolated_117_sd_pp_exact"],
         "%.2f" % n["extrapolated_117_sd_pp_exact"]),
        ("same-system threshold (pp)", "%.2f" % w["mdd95_pp"], "%.2f" % n["mdd95_pp"]),
        ("tasks that flipped", "%d / 29" % w["n_flipped"], "%d / 29" % n["n_flipped"]),
        ("adjacent published gaps within noise",
         "%d / 17" % w["leaderboard_adjacent_within_noise"],
         "%d / 17" % n["leaderboard_adjacent_within_noise"]),
        ("silent restarts", str(w["absorbed_retries"]), str(n["absorbed_retries"])),
    ]
    a = [r"\begin{tabular}{lcc}", r"\toprule",
         r"& with round 6 & without round 6 \\", r"\midrule"]
    a += ["%s & %s & %s \\\\" % r for r in rows]
    a += [r"\bottomrule", r"\end{tabular}"]

    moved = [t for t in S["per_task"] if t["rate_delta_pp"]]
    b = [r"\begin{tabular}{llccr}", r"\toprule",
         r"task & group & with round 6 & without & $\Delta$ (pp) \\", r"\midrule"]
    for t in sorted(moved, key=lambda x: x["rate_delta_pp"]):
        w_, n_ = t["with_round6"], t["without_round6"]
        b.append(r"\texttt{%s} & %s & %d/%d & %d/%d & %+.1f \\" % (
            esc(t["task_id"]), BUCKET.get(t["bucket"], t["bucket"]),
            w_["passes"], w_["n"], n_["passes"], n_["n"], t["rate_delta_pp"]))
    b += [r"\bottomrule", r"\end{tabular}"]

    d = S["round6_denominator_check"]
    c = [r"\begin{tabular}{lc}", r"\toprule",
         r"task the outage cost round 6 & pass rate in the other six rounds \\",
         r"\midrule"]
    for k, v in sorted(d["lost_tasks"].items(), key=lambda kv: -kv[1]["rate"]):
        c.append(r"\texttt{%s} & %d/%d = %.0f\%% \\" % (
            esc(k), v["passes"], v["n"], 100 * v["rate"]))
    c += [r"\midrule",
          r"\textbf{mean of the lost tasks} & \textbf{%.1f\%%} \\" % (
              100 * sum(v["rate"] for v in d["lost_tasks"].values()) / len(d["lost_tasks"])),
          r"mean of all tasks, other rounds & %.1f\%% \\" % (
              100 * d["mean_pass_rate_all_tasks_other_rounds"]),
          r"\bottomrule", r"\end{tabular}"]
    return "\n".join(a), "\n".join(b), "\n".join(c)


def provenance_table(a, analysis_full) -> str:
    """Appendix F: the six things the prose used to merely promise."""
    v = a["provenance"]["values"]
    pm = a["provenance"]["model_params"] or {}
    host = a["provenance"].get("host") or {}
    inp = analysis_full["inputs"]
    st = analysis_full["settings"]
    rows = [
        ("model", r"\texttt{%s}" % esc(str(v.get("model")))),
        ("serving provider / quantization",
         r"\texttt{%s} / \texttt{%s}" % (pm.get("or_provider"), pm.get("or_quant"))),
        ("sampling", r"temperature \texttt{%s}, seed \texttt{%s}" % (
            pm.get("temperature"), pm.get("seed"))),
        # The stored value abbreviates the path with a Unicode ellipsis, which
        # the typewriter font lacks (the character is silently dropped and the
        # path renders as absolute). Substitute ASCII dots.
        ("agent", r"\texttt{%s}" % esc(str(v.get("agent_type")).replace("…", "..."))),
        ("image", r"\texttt{%s}" % esc(str(v.get("image")))),
        # Full digest: a truncated one cannot be checked against a registry,
        # which is the only reason to print it at all. Split across two lines
        # because 64 hex characters do not fit a table column.
        ("image digest", r"\begin{tabular}[t]{@{}l@{}}\texttt{sha256:%s}\\"
                         r"\texttt{%s}\end{tabular}"
                         % ((v.get("image_digest") or "")[:32],
                            (v.get("image_digest") or "")[32:])),
        ("task-set commit", r"\texttt{%s}" % (v.get("taskset_commit") or "")),
        ("max rounds / step wait", "%s / %ss" % (v.get("max_round"), v.get("step_wait_time"))),
        ("harness auto-retry", str(v.get("auto_retry"))),
        ("container recycling", str(v.get("recycle_policy"))),
        ("host kernel / cores", "%s / %s" % (host.get("kernel"), host.get("cpu_count"))),
        ("pass threshold", r"score $>$ %s" % st.get("pass_threshold")),
        ("resampling", "%s draws, seed \\texttt{%s}" % (
            st.get("bootstrap_resamples"), st.get("seed"))),
        ("rows analysed (headline basis)",
         "%s (rounds 4--10 less round 6; 203 over all seven, 290 over all ten)"
         % inp.get("n_rows_used")),
        ("duplicate trial keys", str(len(inp.get("duplicate_keys") or [])) + " (none expected)"),
        ("provenance consistent across rows", "yes" if a["provenance"]["consistent"] else "NO"),
    ]
    out = [r"\begin{tabular}{ll}", r"\toprule"]
    out += ["%s & %s \\\\" % r for r in rows]
    out += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(out)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    head = load("analysis_headline.json")     # six rounds: the headline basis
    (OUT / "app_per_task.tex").write_text(per_task_table(head) + "\n")
    (OUT / "app_stable.tex").write_text(stable_table(head) + "\n")
    r1, r2 = retries_table(ROOT / "data" / "runs.jsonl")
    (OUT / "app_retries_round.tex").write_text(r1 + "\n")
    (OUT / "app_retries_task.tex").write_text(r2 + "\n")

    S = json.loads((ROOT / "data" / "sensitivity_round6.json").read_text())
    e1, e2, e3 = sensitivity_tables(S)
    (OUT / "app_sens_headline.tex").write_text(e1 + "\n")
    (OUT / "app_sens_pertask.tex").write_text(e2 + "\n")
    (OUT / "app_sens_lost.tex").write_text(e3 + "\n")

    # Generated from the headline analysis, not the seven-round one: a row
    # count that disagrees with the basis the paper quotes is exactly the kind
    # of inconsistency this appendix exists to rule out.
    head_full = json.loads((ROOT / "data" / "analysis_headline.json").read_text())
    (OUT / "app_provenance.tex").write_text(
        provenance_table(head_full["arms"]["A_temp0"], head_full) + "\n")

    n_flip = head["flips"]["n_flipped"]
    n_stable = sum(1 for t in head["tasks"] if t["n_scored"] and not t["flipped"])
    print(f"per-task rows      : {sum(1 for t in head['tasks'] if t['n_scored'])}")
    print(f"  of which flipped : {n_flip}")
    print(f"  never flipped    : {n_stable}")
    print(f"wrote {len(list(OUT.glob('*.tex')))} tables to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
