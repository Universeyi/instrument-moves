#!/usr/bin/env python3
"""Render the report's figures from data/analysis.json. Nothing else is read.

Deliberately plain matplotlib: no seaborn, no style sheet, no rcParams file.
A figure in a public report should redraw identically on a bare install five
years from now.

Three figures:

  fig1  per-task pass rate with Wilson 95% intervals, ordered by instability
  fig2  the re-run distribution of the total score, with the real leaderboard
        entries drawn on the same axis, and a noise band around each entry
  fig3  how the flip rate is distributed across tasks

Every figure carries a provenance footer -- arm, model, image digest, task-set
commit -- because a figure travels away from its report.

Usage:
    python3 scripts/plots.py
    python3 scripts/plots.py --arm A_temp0 --outdir report/figures
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
except ModuleNotFoundError as exc:      # the one third-party dependency here
    raise SystemExit(
        "scripts/plots.py needs matplotlib; every other script in this "
        "repository is standard library only.\n"
        "  apt:  sudo apt-get install -y python3-matplotlib\n"
        "  pip:  python3 -m pip install matplotlib\n"
        "Alternatively copy data/analysis.json to any machine that has it -- "
        "the figures are drawn from that file alone.\n"
        f"({exc})") from exc

ROOT = Path(__file__).resolve().parent.parent

# One muted palette, used consistently across the three figures.
C_PASS = "#2f6f4e"
C_FAIL = "#8c3b3b"
C_MID = "#b07d2b"
C_HIST = "#4a6fa5"
C_GREY = "#6b6b6b"
C_LINE = "#222222"


def footer(fig, arm: dict, analysis: dict) -> None:
    prov = arm["provenance"]["values"]
    digest = (prov.get("image_digest") or "")[:12]
    commit = (prov.get("taskset_commit") or "")[:7]
    trials = arm["trials_per_task"].get("median")
    params = arm["provenance"].get("model_params") or {}
    bits = [
        f"arm={arm['arm']}",
        f"model={prov.get('model')}",
        f"provider={params.get('or_provider')}/{params.get('or_quant')}",
        f"temp={params.get('temperature')}",
        f"tasks={arm['n_tasks']}",
        f"trials={int(trials) if trials else '?'}",
        f"image={digest}",
        f"taskset={commit}",
        f"analysis={analysis['inputs']['runs_sha256'][:12]}",
    ]
    fig.text(0.005, 0.005, "  ".join(str(b) for b in bits),
             fontsize=5.5, color=C_GREY, ha="left", va="bottom")


def refuse(fig, ax, title: str, reason: str, arm: dict, analysis: dict) -> None:
    """Draw the reason a figure was not drawn, instead of drawing it anyway.

    analyze.py withholds suite-level statistics for an arm too small to support
    them, and render_report.py refuses to build a report from one. A figure
    escapes further than either -- it gets pasted into a slide with its caption
    left behind -- so it has to honour the same gate. A 95% interval computed
    from one trial per task is arithmetic, not evidence, and a chart is a very
    confident way to present it.
    """
    ax.set_axis_off()
    ax.text(0.5, 0.62, title, ha="center", va="center", fontsize=13,
            color=C_LINE, transform=ax.transAxes)
    ax.text(0.5, 0.45, "not drawn", ha="center", va="center", fontsize=11,
            color=C_FAIL, transform=ax.transAxes)
    ax.text(0.5, 0.32, reason, ha="center", va="top", fontsize=9,
            color=C_GREY, transform=ax.transAxes, wrap=True)
    footer(fig, arm, analysis)


def bar_colour(rate: float) -> str:
    if rate >= 0.999:
        return C_PASS
    if rate <= 0.001:
        return C_FAIL
    return C_MID


# ------------------------------------------------------------------- figure 1

def fig_per_task(arm: dict, analysis: dict, out: Path) -> Path:
    tasks = [t for t in arm["tasks"] if t["n_scored"] > 0]
    # Unstable at the top; among equally stable tasks put the passes above the
    # failures so the two control groups read as blocks.
    tasks.sort(key=lambda t: (t["flip_rate"] or 0.0, t["pass_rate"]))

    ys = range(len(tasks))
    rates = [100 * t["pass_rate"] for t in tasks]
    lo = [100 * t["pass_rate"] - 100 * t["wilson95"][0] for t in tasks]
    hi = [100 * t["wilson95"][1] - 100 * t["pass_rate"] for t in tasks]

    fig, ax = plt.subplots(figsize=(9, max(4.5, 0.30 * len(tasks) + 2.2)))
    ax.barh(list(ys), rates, height=0.62,
            color=[bar_colour(t["pass_rate"]) for t in tasks], alpha=0.85, zorder=2)
    ax.errorbar(rates, list(ys), xerr=[lo, hi], fmt="none",
                ecolor=C_LINE, elinewidth=1.0, capsize=2.5, zorder=3)
    # A task that never passed has a zero-length bar; the marker keeps it on
    # the page instead of leaving a bare error bar floating at the axis.
    ax.plot(rates, list(ys), linestyle="none", marker="|", markersize=7,
            markeredgewidth=1.6, color=C_LINE, zorder=4)

    labels = []
    for t in tasks:
        mark = " *" if t.get("randomized_setup") else ""
        miss = f"  ({t['n_unscored']} unscored)" if t["n_unscored"] else ""
        labels.append(f"{t['task_id']}{mark}  [{t['bucket']}]{miss}")
    ax.set_yticks(list(ys))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(PercentFormatter())
    ax.set_xlabel(f"pass rate over {int(arm['trials_per_task'].get('median') or 0)} "
                  f"identical trials, with Wilson 95% interval")
    ax.set_title("Per-task pass rate, ordered by instability\n"
                 "same agent, same configuration, same task", fontsize=11)
    ax.grid(axis="x", color="#dddddd", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    n_flip = arm["flips"]["n_flipped"]
    n_tot = arm["flips"]["n_tasks_with_2plus_scored_trials"]
    ax.text(0.99, 0.01, f"{n_flip} of {n_tot} tasks did not give the same answer every time"
                        + ("\n*  task randomises its own initial state" if
                           any(t.get("randomized_setup") for t in tasks) else ""),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5, color=C_GREY)

    fig.tight_layout(rect=(0, 0.02, 1, 1))
    footer(fig, arm, analysis)
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


# ------------------------------------------------------------------- figure 2

def _hist_points(block: dict) -> tuple[list[float], list[float]]:
    """counts_hist ({n_passes: freq}) -> (score in %, probability)."""
    slots = block["n_slots"]
    total = block["n_resamples"]
    xs, ys = [], []
    for k, freq in sorted(block["counts_hist"].items(), key=lambda kv: int(kv[0])):
        xs.append(100.0 * int(k) / slots)
        ys.append(freq / total)
    return xs, ys


def fig_noise_vs_leaderboard(arm: dict, analysis: dict, out: Path) -> Path:
    power = arm.get("statistical_power") or {"sufficient": True}
    if not power.get("sufficient", True):
        fig, ax = plt.subplots(figsize=(10, 5.2))
        refuse(fig, ax, "How far a score moves when nothing changes",
               "This arm cannot support a suite-level interval:\n"
               + "; ".join(power.get("reasons") or []) + ".\n\n"
               "Per-task pass rates are still valid and are in figure 1.",
               arm, analysis)
        fig.savefig(out, dpi=200)
        plt.close(fig)
        return out

    extrap = arm.get("gui_only_extrapolation")
    if extrap:
        block = extrap["fixed_taskset"]
        scale_note = (f"re-run distribution of the total score, "
                      f"{block['n_slots']} GUI-Only tasks\n"
                      f"(measured rates carried to the real stratum sizes; "
                      f"see report limitations)")
    else:
        block = arm["rerun"]["posterior"]
        scale_note = (f"re-run distribution of the total score, "
                      f"{block['n_slots']}-task pilot\n"
                      f"(a 117-task suite would be narrower)")

    lb = arm.get("leaderboard_gaps")
    mdd = arm["detectable_difference"]["mdd95_pp"]
    sd = arm["detectable_difference"]["single_run_sd_pp"]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 9.0), sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.45], "hspace": 0.30})

    # --- top: the distribution itself ------------------------------------
    xs, ys = _hist_points(block)
    width = 100.0 / block["n_slots"]
    ax1.bar(xs, ys, width=width * 0.92, color=C_HIST, alpha=0.85, zorder=2,
            label="one re-run of the identical configuration")
    lo, hi = block["ci95_pct"]
    ax1.axvspan(lo, hi, color=C_HIST, alpha=0.13, zorder=1,
                label=f"95% interval: {lo:.1f}–{hi:.1f}  ({hi - lo:.1f} pp wide)")
    ax1.set_ylabel("probability")
    ax1.set_title("How far a score moves when nothing changes\n" + scale_note,
                  fontsize=11)
    ax1.legend(fontsize=7.5, frameon=False, loc="upper left")
    ax1.grid(axis="x", color="#eeeeee", linewidth=0.6, zorder=0)
    ax1.set_axisbelow(True)
    for spine in ("top", "right"):
        ax1.spines[spine].set_visible(False)

    # --- bottom: the published entries, with that width around each -------
    if lb:
        entries = lb["entries"]
        overlapping = set()
        for pair in lb["adjacent_pairs"]:
            if pair["within_noise"]:
                overlapping.add(pair["higher"])
                overlapping.add(pair["lower"])

        ys2 = list(range(len(entries)))[::-1]
        for y, e in zip(ys2, entries):
            s = e["gui_only"]
            hot = e["model"] in overlapping
            ax2.plot([s - mdd / 2, s + mdd / 2], [y, y],
                     color=(C_MID if hot else C_GREY), linewidth=5.0,
                     alpha=0.30 if hot else 0.18, solid_capstyle="butt", zorder=2)
            ax2.plot([s], [y], marker="o", markersize=4.5,
                     color=(C_MID if hot else C_LINE), zorder=3)
            # Names on the right, except near the top of the scale where
            # there is no room left on the axis.
            if s + mdd / 2 > 74:
                ax2.text(s - mdd / 2 - 0.8, y, e["model"], fontsize=7,
                         va="center", ha="right",
                         color=(C_MID if hot else C_LINE))
            else:
                ax2.text(s + mdd / 2 + 0.8, y, e["model"], fontsize=7,
                         va="center", ha="left",
                         color=(C_MID if hot else C_LINE))
        ax2.set_yticks([])
        ax2.set_ylim(-1, len(entries))
        ax2.set_xlabel("MobileWorld GUI-Only score (%)")
        ax2.set_title(
            f"Published GUI-Only entries, each with the measured noise band "
            f"(±{mdd / 2:.1f} pp)\n"
            f"{lb['n_adjacent_within_noise']} of {len(lb['adjacent_pairs'])} "
            f"adjacent gaps, and {lb['n_all_pairs_within_noise']} of "
            f"{lb['n_all_pairs']} pairs overall, are smaller than the "
            f"difference a single run can resolve",
            fontsize=10, loc="left", pad=10)
        ax2.grid(axis="x", color="#eeeeee", linewidth=0.6, zorder=0)
        ax2.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax2.spines[spine].set_visible(False)
        ax2.text(0.995, 0.02,
                 f"band = minimum detectable difference at 95%, "
                 f"1.96·√2·σ with σ = {sd:.2f} pp\n"
                 f"every entry above is a single run; none reports an interval",
                 transform=ax2.transAxes, ha="right", va="bottom",
                 fontsize=7, color=C_GREY)
    else:
        why = (arm.get("leaderboard_gaps_withheld")
               or "data/public_matrix.json was not available")
        ax2.text(0.5, 0.5,
                 "Leaderboard comparison withheld:\n" + why,
                 ha="center", va="center", transform=ax2.transAxes, color=C_GREY,
                 fontsize=9)
        ax2.set_axis_off()

    ax1.set_xlim(0, 100)
    fig.subplots_adjust(left=0.085, right=0.975, top=0.90, bottom=0.075)
    footer(fig, arm, analysis)
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


# ------------------------------------------------------------------- figure 3

def fig_flip_rates(arm: dict, analysis: dict, out: Path) -> Path:
    tasks = [t for t in arm["tasks"] if t["flip_rate"] is not None]
    if not tasks:
        fig, ax = plt.subplots(figsize=(10, 4.2))
        refuse(fig, ax, "Flip rate per task",
               "A flip rate needs at least two scored trials of the same task.\n"
               "No task in this arm has that.", arm, analysis)
        fig.savefig(out, dpi=200)
        plt.close(fig)
        return out

    rates = sorted((t["flip_rate"] for t in tasks), reverse=True)
    # The ceiling depends on n, and n is not identical across tasks when a
    # trial failed to produce a score. Draw the line for the commonest n and
    # say so, rather than drawing one line and implying it applies to all.
    ns = Counter(t["n_scored"] for t in tasks)
    n_mode = ns.most_common(1)[0][0] if ns else 0
    fmax = max((t["flip_rate_max"] or 0) for t in tasks
               if t["n_scored"] == n_mode) if tasks else 0.5556
    n_varies = len(ns) > 1

    fig, (axa, axb) = plt.subplots(1, 2, figsize=(10, 4.2),
                                   gridspec_kw={"width_ratios": [1.25, 1.0]})

    axa.bar(range(len(rates)), rates, color=C_HIST, alpha=0.85, width=0.8, zorder=2)
    axa.axhline(fmax, color=C_FAIL, linewidth=1.0, linestyle="--", zorder=3)
    axa.text(len(rates) - 0.5, fmax,
             f"maximum possible at n={n_mode}: {fmax:.3f}"
             + ("  (n varies; see analysis.json)" if n_varies else ""),
             fontsize=7, va="bottom", ha="right", color=C_FAIL)
    axa.set_xlabel("task, ordered by flip rate")
    axa.set_ylabel("flip rate")
    axa.set_ylim(0, max(fmax * 1.18, 0.05))
    axa.set_title("Flip rate per task", fontsize=10)
    axa.grid(axis="y", color="#eeeeee", linewidth=0.6, zorder=0)
    axa.set_axisbelow(True)

    n_zero = sum(1 for r in rates if r == 0)
    bins = [i / 20 for i in range(0, math.ceil(fmax * 20) + 2)]
    axb.hist(rates, bins=bins, color=C_HIST, alpha=0.85, zorder=2)
    axb.set_xlabel("flip rate")
    axb.set_ylabel("tasks")
    axb.set_title(f"Distribution\n{n_zero} of {len(rates)} tasks never flipped",
                  fontsize=10)
    axb.grid(axis="y", color="#eeeeee", linewidth=0.6, zorder=0)
    axb.set_axisbelow(True)

    for ax in (axa, axb):
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    fig.suptitle("flip rate = disagreeing trial pairs / all trial pairs = k(n−k)/C(n,2)",
                 fontsize=9, color=C_GREY, y=0.995)
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    footer(fig, arm, analysis)
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", default=str(ROOT / "data" / "analysis.json"))
    ap.add_argument("--outdir", default=str(ROOT / "report" / "figures"))
    ap.add_argument("--arm", default=None,
                    help="Default: the first arm in analysis.json")
    args = ap.parse_args()

    apath = Path(args.analysis)
    if not apath.exists():
        print(f"{apath} does not exist -- run scripts/analyze.py first.")
        return 2
    analysis = json.loads(apath.read_text())
    arms = analysis.get("arms") or {}
    if not arms:
        print("analysis.json contains no arms")
        return 2
    name = args.arm or sorted(arms)[0]
    if name not in arms:
        print(f"arm {name!r} not in {apath}; have {sorted(arms)}")
        return 2
    arm = arms[name]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    made = [
        fig_per_task(arm, analysis, outdir / f"fig1_per_task_pass_rate_{name}.png"),
        fig_noise_vs_leaderboard(arm, analysis, outdir / f"fig2_noise_vs_leaderboard_{name}.png"),
        fig_flip_rates(arm, analysis, outdir / f"fig3_flip_rate_distribution_{name}.png"),
    ]
    for p in made:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
