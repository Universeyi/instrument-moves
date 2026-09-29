#!/usr/bin/env python3
"""Fill report/REPORT.template.md from data/analysis.json -> report/REPORT.md.

The point is mechanical, not cosmetic. The repository's one hard rule is that
no number in report/ may be typed by hand, and the reliable way to enforce that
is to make it impossible: the template holds prose and placeholders only, every
placeholder is resolved out of analysis.json, and an unresolved one is a hard
error rather than a blank. REPORT.md therefore cannot exist before the data
does, and cannot drift after the data changes.

Placeholders:

    {{ arm.detectable_difference.mdd95_pp }}          value from the chosen arm
    {{ arm.rerun.posterior.ci95_pct.0 | .1f }}        with a format spec
    {{ analysis.settings.seed }}                      top level of analysis.json
    {{% flipped_tasks_table %}}                       a generated block

Usage:
    python3 scripts/render_report.py
    python3 scripts/render_report.py --arm A_temp0 --check
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCALAR = re.compile(r"\{\{\s*([A-Za-z0-9_.\[\]]+)\s*(?:\|\s*([^}]+?)\s*)?\}\}")
BLOCK = re.compile(r"\{\{%\s*([A-Za-z0-9_]+)\s*%\}\}")
# Whole sections that only apply to some arms -- a regime comparison exists
# only where something identifiable happened. Without this the template would
# hard-require every optional finding and no ordinary arm could render.
# Innermost-first: the body may not itself contain a {{#if}}, so repeated
# application resolves nested sections from the inside out. A single
# non-greedy pass would let an inner {{/if}} close an outer block.
COND = re.compile(
    r"\{\{#if\s+([A-Za-z0-9_.\[\]]+)\s*\}\}((?:(?!\{\{#if).)*?)\{\{/if\}\}",
    re.DOTALL)


class Missing(Exception):
    pass


def resolve(ctx: dict, dotted: str):
    cur = ctx
    for part in dotted.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise Missing(dotted)
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise Missing(dotted)
        else:
            raise Missing(dotted)
    if cur is None:
        raise Missing(f"{dotted} (is null)")
    return cur


def _plural(noun: str, n) -> str:
    """Agree a noun with a templated count.

    Every count in this report comes from the data, so any of them can turn out
    to be 1 on the next run and leave "1 tasks" in a published document.
    Restructuring each sentence to dodge the problem is fragile; agreeing the
    noun is not. `#task` gives task/tasks, `#entry/entries` for irregulars.
    """
    single, _, plural = noun.partition("/")
    if not plural:
        plural = single + ("es" if single.endswith(("s", "x", "ch", "sh"))
                           else "s")
    try:
        one = float(n) == 1
    except (TypeError, ValueError):
        one = False
    return single if one else plural


def fmt(value, spec: str | None) -> str:
    # `?singular|plural` picks a whole phrase, for the cases a noun-agreement
    # filter cannot reach: verbs and pronouns ("1 of them ... they measure").
    if spec and spec.startswith("?"):
        forms = spec[1:].split("|")
        if len(forms) != 2:
            raise ValueError(f"plural spec needs 'singular|plural', got {spec!r}")
        try:
            n = abs(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"plural spec applied to non-numeric {value!r}")
        return forms[0] if n == 1 else forms[1]
    if spec and spec.startswith("#"):
        # "<count> <noun>", with the noun agreed to the count.
        return f"{value:g} {_plural(spec[1:], value)}" if isinstance(
            value, float) else f"{value} {_plural(spec[1:], value)}"
    if spec:
        return format(value, spec)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# ------------------------------------------------------------------- blocks

def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_none_"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def blk_per_trial_table(arm: dict, analysis: dict) -> str:
    rows = [[p["trial"], p["n_tasks_scored"], p["n_passed"], f"{p['score_pct']:.1f}"]
            for p in arm["per_trial_suite_score"]["per_trial"]]
    return _table(["trial", "tasks scored", "passed", "suite score (%)"], rows)


def blk_flipped_tasks_table(arm: dict, analysis: dict) -> str:
    rows = []
    for t in arm["flips"]["ranked_by_instability"]:
        if not t["flip_rate"]:
            continue
        lo, hi = t["wilson95"]
        rows.append([t["task_id"], t["bucket"], f"{t['passes']}/{t['n_scored']}",
                     f"{100 * t['pass_rate']:.0f}%",
                     f"{100 * lo:.0f}–{100 * hi:.0f}%",
                     f"{t['flip_rate']:.3f}"])
    return _table(["task", "bucket", "passed", "pass rate",
                   "Wilson 95%", "flip rate"], rows)


def blk_stable_tasks_table(arm: dict, analysis: dict) -> str:
    rows = []
    for t in arm["tasks"]:
        if t["n_scored"] and not t["flipped"]:
            rows.append([t["task_id"], t["bucket"],
                         f"{t['passes']}/{t['n_scored']}",
                         "always passed" if t["pass_rate"] == 1 else "always failed"])
    return _table(["task", "bucket", "passed", "outcome"], rows)


def blk_bucket_table(arm: dict, analysis: dict) -> str:
    rows = []
    for bucket, b in arm["subgroups"]["by_bucket"].items():
        rows.append([bucket, b["n_tasks"], f"{100 * b['mean_pass_rate']:.1f}%",
                     b["n_flipped"],
                     f"{b['mean_flip_rate']:.3f}" if b["mean_flip_rate"] is not None else "—"])
    return _table(["bucket (chosen from published runs)", "tasks",
                   "mean pass rate", "flipped", "mean flip rate"], rows)


def blk_outcomes_table(arm: dict, analysis: dict) -> str:
    total = sum(arm["outcomes"].values())
    rows = [[k, v, f"{100 * v / total:.2f}%"]
            for k, v in sorted(arm["outcomes"].items(), key=lambda kv: -kv[1])]
    return _table(["outcome", "runs", "share"], rows)


def blk_leaderboard_gap_table(arm: dict, analysis: dict) -> str:
    lb = arm.get("leaderboard_gaps")
    if not lb:
        return "_data/public_matrix.json was not available._"
    rows = []
    for p in lb["adjacent_pairs"]:
        rows.append([p["higher"], p["lower"], f"{p['gap_pp']:.1f}",
                     "warrants a second run before citation"
                     if p["within_noise"] else "resolvable by single runs"])
    return _table(["entry", "next entry below", "gap (pp)", ""], rows)


def blk_regime_table(arm: dict, analysis: dict) -> str:
    r = arm.get("regime_comparison")
    if not r or not r.get("applicable"):
        return "_No regime split was applied to this arm._"
    rows = [
        ["repetition rounds",
         ", ".join(str(t) for t in r["before"]["trials"]),
         ", ".join(str(t) for t in r["after"]["trials"])],
        ["suite score each round (%)",
         ", ".join(f"{x:.1f}" for x in r["before"]["suite_scores_pct"]),
         ", ".join(f"{x:.1f}" for x in r["after"]["suite_scores_pct"])],
    ]
    rt = r.get("reasoning_tokens_per_action")
    if rt:
        rows.append(["reasoning tokens per action (median)",
                     f"{rt['before_median_of_medians']:.1f}",
                     f"{rt['after_median_of_medians']:.1f}  "
                     f"({rt['change_pct']:+.1f}%)"])
    return _table(["", "before", "after"], rows)


def blk_regime_moved_tasks_table(arm: dict, analysis: dict) -> str:
    r = arm.get("regime_comparison")
    if not r or not r.get("applicable") or not r.get("tasks_that_moved"):
        return "_No task changed._"
    rows = [[m["task_id"], m["bucket"], f"{100*m['before']:.0f}%",
             f"{100*m['after']:.0f}%", f"{m['delta_pp']:+.0f}"]
            for m in r["tasks_that_moved"]]
    return _table(["task", "bucket", "before", "after", "change (pp)"], rows)


def blk_leave_one_out_table(arm: dict, analysis: dict) -> str:
    l = arm.get("leave_one_out")
    if not l or not l.get("variants"):
        return "_Not computed for this arm._"
    rows = [[v["dropped_trial"], v["n_trials_left"],
             f"{v['observed_range_pp']:.1f}", f"{v['mdd95_pp']:.2f}",
             v["n_flipped"],
             f"{v['leaderboard_adjacent_within_noise']} of 17"]
            for v in l["variants"]]
    return _table(["round dropped", "rounds left", "observed range (pp)",
                   "detectable difference (pp)", "tasks that flipped",
                   "adjacent gaps needing a second run"], rows)


def blk_provenance_table(arm: dict, analysis: dict) -> str:
    v = arm["provenance"]["values"]
    params = arm["provenance"].get("model_params") or {}
    host = arm["provenance"].get("host") or {}
    rows = [
        ["model", v.get("model")],
        ["serving", f"{params.get('or_provider')} / {params.get('or_quant')}"],
        ["sampling", f"temperature={params.get('temperature')} seed={params.get('seed')}"],
        ["agent", v.get("agent_type")],
        ["image", f"`{v.get('image')}`"],
        ["image digest", f"`sha256:{v.get('image_digest')}`"],
        ["task-set commit", f"`{v.get('taskset_commit')}`"],
        ["max rounds / step wait", f"{v.get('max_round')} / {v.get('step_wait_time')}s"],
        ["harness auto-retry", v.get("auto_retry")],
        ["container recycling", v.get("recycle_policy")],
        ["host kernel", host.get("kernel")],
        ["runs.jsonl sha256", f"`{analysis['inputs']['runs_sha256']}`"],
    ]
    return _table(["", ""], [[a, b] for a, b in rows])


def blk_randomized_setup_table(arm: dict, analysis: dict) -> str:
    r = arm["subgroups"]["randomized_setup"]
    if not r["task_ids"]:
        return "_No task in the pilot randomises its own initial state._"
    rows = []
    for t in arm["tasks"]:
        if t["task_id"] in r["task_ids"]:
            rows.append([t["task_id"], f"{t['passes']}/{t['n_scored']}",
                         f"{t['flip_rate']:.3f}" if t["flip_rate"] is not None else "—"])
    return _table(["task", "passed", "flip rate"], rows)


def blk_absorbed_retries_by_trial_table(arm: dict, analysis: dict) -> str:
    by = arm["absorbed_retries"].get("by_trial") or {}
    if not by:
        return "_The harness did not silently restart any run._"
    worst = max(by.values())
    rows = []
    for trial, n in sorted(by.items(), key=lambda kv: int(kv[0])):
        rows.append([trial, n, "**incident**" if n == worst and n > 3 * (
            sum(by.values()) - worst) / max(1, len(by) - 1) else ""])
    return _table(["repetition round", "silent restarts", ""], rows)


def blk_outcomes_by_trial_table(arm: dict, analysis: dict) -> str:
    by = arm.get("outcomes_by_trial") or {}
    if not by:
        return "_none_"
    kinds = sorted({k for v in by.values() for k in v})
    if kinds == ["scored"]:
        return "_Every run in every round produced a score._"
    rows = [[t] + [by[t].get(k, 0) for k in kinds]
            for t in sorted(by, key=int)]
    return _table(["repetition round"] + kinds, rows)


def blk_absorbed_retries_table(arm: dict, analysis: dict) -> str:
    by = arm["absorbed_retries"]["by_task"]
    if not by:
        return "_The harness did not silently restart any run._"
    rows = [[k, v] for k, v in sorted(by.items(), key=lambda kv: -kv[1])]
    return _table(["task", "silent restarts"], rows)


BLOCKS = {name[4:]: fn for name, fn in list(globals().items())
          if name.startswith("blk_")}


# --------------------------------------------------------------------- reflow

FENCE = re.compile(r"^\s*```")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
# Blocks whose line breaks carry meaning, or whose lines are already atomic.
NOWRAP_PREFIX = ("#", "|", ">", "!", "<", "    ", "\t")


def _wrap_block(lines: list[str], width: int) -> list[str]:
    if any(ln.lstrip().startswith(NOWRAP_PREFIX) or ln.startswith(("    ", "\t"))
           for ln in lines):
        return lines

    # Long option strings and hyphenated flags must not be split.
    def wrap(text: str, first: str, rest: str) -> list[str]:
        return textwrap.wrap(text, width=width, initial_indent=first,
                             subsequent_indent=rest, break_long_words=False,
                             break_on_hyphens=False) or [first.rstrip()]

    if LIST_ITEM.match(lines[0]):
        out: list[str] = []
        item: list[str] = []
        indent = marker = ""

        def flush():
            if item:
                out.extend(wrap(" ".join(x.strip() for x in item),
                                f"{indent}{marker} ",
                                indent + " " * (len(marker) + 1)))
        for ln in lines:
            m = LIST_ITEM.match(ln)
            if m:
                flush()
                indent, marker = m.group(1), m.group(2)
                item = [m.group(3)]
            else:
                item.append(ln)
        flush()
        return out

    return wrap(" ".join(ln.strip() for ln in lines), "", "")


def reflow(text: str, width: int = 79) -> str:
    """Re-wrap prose after substitution.

    A placeholder is almost never the same length as the value it resolves to,
    so a hand-wrapped template renders into ragged source. Markdown does not
    care, but the generated file is meant to be read as a file too. Code
    fences, tables, headings and images are left exactly as written.
    """
    out: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush():
        if buf:
            out.extend(_wrap_block(buf, width))
            buf.clear()

    for line in text.split("\n"):
        if FENCE.match(line):
            flush()
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        if not line.strip():
            flush()
            out.append("")
            continue
        buf.append(line)
    flush()
    return "\n".join(out)


# --------------------------------------------------------------------- main

def render(template: str, arm: dict, analysis: dict) -> tuple[str, list[str]]:
    problems: list[str] = []

    def sub_block(m: re.Match) -> str:
        name = m.group(1)
        if name not in BLOCKS:
            problems.append(f"unknown block {{{{% {name} %}}}}")
            return m.group(0)
        try:
            return BLOCKS[name](arm, analysis)
        except Exception as e:                       # noqa: BLE001
            problems.append(f"block {name} failed: {e!r}")
            return m.group(0)

    def sub_scalar(m: re.Match) -> str:
        dotted, spec = m.group(1), m.group(2)
        try:
            return fmt(resolve({"arm": arm, "analysis": analysis}, dotted), spec)
        except Missing as e:
            problems.append(f"missing value: {e}")
            return m.group(0)
        except (ValueError, TypeError) as e:
            problems.append(f"bad format for {dotted}: {e}")
            return m.group(0)

    def sub_cond(m: re.Match) -> str:
        path, body = m.group(1), m.group(2)
        try:
            value = resolve({"arm": arm, "analysis": analysis}, path)
        except Missing:
            return ""
        # The markers sit on their own lines; keeping their surrounding
        # newlines would leave a blank gap wherever a section is dropped and a
        # double gap wherever one is kept.
        return ("\n" + body.strip("\n") + "\n") if value else ""

    # Conditionals first: a dropped section must not have its placeholders
    # resolved, or a missing value inside it would be reported as an error.
    out = template
    for _ in range(10):                       # nesting depth guard
        out, n = COND.subn(sub_cond, out)
        if not n:
            break
    if "{{#if" in out or "{{/if}}" in out:
        problems.append("unbalanced {{#if}} / {{/if}} in the template")
    out = BLOCK.sub(sub_block, out)
    out = SCALAR.sub(sub_scalar, out)
    return out, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", default=str(ROOT / "data" / "analysis.json"))
    ap.add_argument("--template", default=str(ROOT / "report" / "REPORT.template.md"))
    ap.add_argument("--out", default=str(ROOT / "report" / "REPORT.md"))
    ap.add_argument("--arm", default=None)
    ap.add_argument("--width", type=int, default=79,
                    help="Re-wrap prose to this width after substitution; "
                         "0 leaves the template's own line breaks alone.")
    ap.add_argument("--check", action="store_true",
                    help="Resolve everything but write nothing. Exits non-zero "
                         "if any placeholder is unresolved.")
    args = ap.parse_args()

    apath, tpath = Path(args.analysis), Path(args.template)
    if not tpath.exists():
        print(f"{tpath} does not exist")
        return 2
    if not apath.exists():
        print(f"{apath} does not exist -- run scripts/analyze.py first. "
              "REPORT.md is deliberately not writable without it.")
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

    out, problems = render(tpath.read_text(), arms[name], analysis)

    if problems:
        unique = sorted(set(problems))
        print(f"{len(unique)} unresolved placeholder(s) "
              f"({len(problems)} occurrence(s)):")
        for p in unique:
            print(f"  - {p}")
        print("\nNothing was written. Every number in the report has to come "
              "out of analysis.json; a placeholder that cannot be resolved is "
              "a bug in the template or a gap in the data, not something to "
              "fill in by hand.")
        return 1

    if args.check:
        print(f"template resolves cleanly against arm {name}")
        return 0

    if args.width:
        out = reflow(out, args.width)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(out)
    print(f"wrote {args.out} from arm {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
