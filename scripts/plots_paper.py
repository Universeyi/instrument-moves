#!/usr/bin/env python3
"""The two figures that carry the endpoint-change argument.

Kept separate from plots.py because these are built for the paper's argument
rather than for the report's structure, and because both need
data/traj_stats.json alongside the analysis.

    python3 scripts/plots_paper.py --analysis data/analysis_all.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    raise SystemExit(f"needs matplotlib ({exc})") from exc

ROOT = Path(__file__).resolve().parent.parent
C_SCORE, C_TOK, C_GREY, C_LINE = "#2f5d8a", "#8a5a2f", "#6b6b6b", "#222222"
C_BOUND, C_INCIDENT = "#b3341f", "#7a7a7a"


def fig_regime(a: dict, traj: dict, out: Path) -> Path:
    """Two stacked panels on a shared round axis.

    Deliberately NOT a twin-axis single plot: two y-scales invite the reading
    that the scales were chosen to make the curves agree. Stacked panels show
    the same simultaneous step and cannot be accused of it.
    """
    per = a["per_trial_suite_score"]["per_trial"]
    med = traj["by_arm"][a["arm"]]["per_trial_completion_per_step"]
    rounds = [p["trial"] for p in per]
    score = [p["score_pct"] for p in per]
    nsc = [p["n_tasks_scored"] for p in per]
    toks = [med[str(r)]["median"] for r in rounds]
    boundary = a["regime_comparison"]["boundary_trial"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6.4, 3.7), sharex=True,
                                   gridspec_kw={"hspace": 0.16})

    def mark(ax):
        # The configuration change and the infrastructure incident are marked
        # differently and sit at different rounds. A reader must be able to see
        # at a glance that the step is not the outage.
        ax.axvline(boundary - 0.5, color=C_BOUND, lw=1.4, zorder=1)
        ax.axvspan(5.5, 6.5, color=C_INCIDENT, alpha=0.13, lw=0, zorder=0)

    mark(ax1); mark(ax2)
    ax1.plot(rounds, score, "o-", color=C_SCORE, lw=1.8, ms=5, zorder=3)
    ax1.set_ylabel("suite score (%)", fontsize=9)
    ax1.set_ylim(min(score) - 6, max(score) + 8)
    for r, s, n in zip(rounds, score, nsc):
        if n != max(nsc):
            ax1.annotate(f"{n} of {max(nsc)}\nscored", (r, s), textcoords="offset points",
                         xytext=(0, 9), ha="center", fontsize=6.2, color=C_GREY)

    ax2.plot(rounds, toks, "s-", color=C_TOK, lw=1.8, ms=4.5, zorder=3)
    ax2.set_ylabel("completion tokens\nper action (median)", fontsize=9)
    ax2.set_xlabel("repetition round", fontsize=9)
    ax2.set_ylim(min(toks) - 14, max(toks) + 14)
    ax2.set_xticks(rounds)

    ax1.annotate("serving configuration changes here",
                 xy=(boundary - 0.5, max(score) + 4.5), xytext=(boundary + 0.25, max(score) + 5.5),
                 fontsize=7.4, color=C_BOUND,
                 arrowprops=dict(arrowstyle="-", color=C_BOUND, lw=0.9))
    ax2.annotate("provider outage (HTTP 401),\na separate round",
                 xy=(6, min(toks) - 4), xytext=(6.9, min(toks) - 11),
                 fontsize=7.0, color=C_LINE,
                 arrowprops=dict(arrowstyle="-", color=C_INCIDENT, lw=0.9))

    for ax in (ax1, ax2):
        ax.grid(axis="y", color="#e8e8e8", lw=0.6, zorder=0)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.align_ylabels([ax1, ax2])
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def fig_dumbbell(a: dict, out: Path) -> Path:
    """Per-task pass rate before and after, one line each.

    Every line pointing the same way is the argument; a p-value is the summary
    of it. The picture is what a reader believes.
    """
    rc = a["regime_comparison"]
    moved = sorted(rc["tasks_that_moved"], key=lambda m: m["delta_pp"])
    ys = range(len(moved))
    fig, ax = plt.subplots(figsize=(6.4, max(2.0, 0.26 * len(moved) + 0.55)))
    for y, m in zip(ys, moved):
        b, af = 100 * m["before"], 100 * m["after"]
        ax.annotate("", xy=(af, y), xytext=(b, y),
                    arrowprops=dict(arrowstyle="-|>", color=C_BOUND, lw=1.5,
                                    shrinkA=0, shrinkB=0, mutation_scale=11))
        ax.plot([b], [y], "o", color=C_GREY, ms=5.5, zorder=3)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([m["task_id"] for m in moved], fontsize=7)
    ax.set_xlabel("pass rate before → after the configuration change (%)", fontsize=9)
    ax.set_xlim(-6, 106)
    # Headroom so the summary line below cannot sit on top of the lowest arrow.
    ax.set_ylim(-0.7, len(moved) - 0.4)
    ax.grid(axis="x", color="#e8e8e8", lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    # The summary sentence lives in the LaTeX caption, not inside the axes:
    # at this height it collided with the tick labels, and a caption is where a
    # reader looks for it anyway.
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", default=str(ROOT / "data" / "analysis_all.json"))
    ap.add_argument("--traj", default=str(ROOT / "data" / "traj_stats.json"))
    ap.add_argument("--outdir", default=str(ROOT / "paper" / "figures"))
    ap.add_argument("--arm", default=None)
    args = ap.parse_args()

    analysis = json.loads(Path(args.analysis).read_text())
    traj = json.loads(Path(args.traj).read_text())
    name = args.arm or sorted(analysis["arms"])[0]
    a = analysis["arms"][name]
    if not a.get("regime_comparison", {}).get("applicable"):
        print("this analysis has no regime comparison; rerun with --regime-split")
        return 2
    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    for p in (fig_regime(a, traj, out / "fig_regime.pdf"),
              fig_dumbbell(a, out / "fig_dumbbell.pdf")):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
