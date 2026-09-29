## Summary

I ran the same agent, on the same 29 GUI-Only tasks, ten times, with everything
I could find a flag for pinned — model id, serving provider, quantization,
`temperature=0`, fixed `seed`, container image digest, task-set commit, one
fresh container per repetition round, strictly serial execution.

The suite score still moved by **8.1 points** between the best and worst
repetition, and **8 of 29 tasks did not return the same verdict every time**.

I don't think this is a defect in MobileWorld — it looks like the unavoidable
cost of driving a real OS through a real UI against real backends, which is
exactly what makes the benchmark worth having. But as far as I can tell nobody
has published a number for it, and without one it is hard for a reader to know
which leaderboard differences are meaningful. Happy to hand over the data and
the tooling if it is useful to you; details below and I can supply whatever
format is easiest.

## What I measured

**Run-to-run variance, one configuration, seven repetitions** (after excluding
the first three — see the next point):

| | |
|---|---|
| suite score per repetition | 31.0, 31.0, 39.1, 34.5, 37.9, 37.9, 34.5 % |
| observed range | **8.1 points** |
| tasks that flipped at least once | **8 of 29** — `split` bucket 7/10, `all_pass` 1/9, `all_fail` 0/10 |
| smallest difference two single runs can resolve (95%) | **8.7 points** (8.0–9.0 under leave-one-round-out) |

Carried up to the full 117-task GUI-Only suite by reweighting the sampled
groups to their real sizes, the 95% interval for a single run is about **12.8
points wide**. That step is conditional on the 88 unmeasured `split` tasks
behaving like the 10 measured ones, and I would treat it as indicative rather
than settled.

**The serving endpoint changed configuration in the middle of the run.**
Between repetition 3 and 4, with nothing changed on my side, **10 tasks got
worse and 0 got better** (sign test p = 0.002) and the model's reasoning output
per action dropped from a median of 132 tokens to 106 and stayed there. An
identical probe sent outside the harness a day apart reproduces it; a second
provider used as a control does not move. I mention it because **no leaderboard
entry records which serving endpoint or serving configuration produced it**, so
two submissions made a day apart can differ for reasons neither submitter can
see. This is not specific to MobileWorld — it applies to any leaderboard built
on hosted inference.

**`temperature=0` with a fixed seed is not determinism.** Three identical
requests returned three different completions on both endpoints I tried, both
of which advertise `seed` support. Worth flagging the trap: the same check with
a trivial prompt ("reply with OK") returns byte-identical output and looks like
proof of determinism — a thirty-token answer is identical under any sampler.
It has to be probed at the output length the real workload produces.

**The harness absorbs failures silently.** Across all 290 runs the in-task
retry fired 110 times, **68 of them inside a single repetition round** in which
the provider returned HTTP 401 for about an hour. Six runs in that round
produced no `result.txt` at all. None of the scored results carries any trace of it. I counted the
`*_backup_*` directories to see this; there is no other signal.

## Method

Nothing under `vendor/MobileWorld` was modified; the custom agent goes through
the existing `--agent-type <path.py>` hook.

| | |
|---|---|
| task-set commit | `83e7b8fc75ebb6c4a098254a42999db5f4172666` |
| image | `ghcr.io/tongyi-mai/mobile_world:v1.4`, digest `sha256:00e24a8995892af9…` |
| model | `moonshotai/kimi-k2.5` via OpenRouter, provider pinned to `atlas-cloud`, quantization `int4` |
| sampling | `temperature=0`, `seed=42`, `max_tokens=2048` (the `general_e2e` default) |
| eval | `--max-concurrency 1`, `--max-round 50`, `--auto-retry 10`, one fresh container per repetition round |
| tasks | 29 GUI-Only, chosen from your published trajectory bundles: 10 the published runs disagree on, 9 they all pass, 10 they all fail |
| pass threshold | `score > 0.99`, matching `core/log_viewer/utils.py:489` |

Robustness: every repetition round was dropped in turn and everything
recomputed; the resolvable-difference figure stays in 8.0–9.0 points across all
seven variants, so no single round (including the one hit by the outage) is
driving it.

## Recommendations

These are the things that would have made the above much easier to establish,
and I think most of them are close to free:

1. **Record the serving endpoint in a leaderboard entry** — provider and
   quantization, not just the model id. This is the single highest-value change
   here. Two entries with the same model id today can be different weights on
   different hardware, and there is currently no way for a reader to tell.
2. **Record the image digest and task-set commit per entry.** Entries from
   different dates were scored by different verifier versions — your own commit
   history has several `fix(tasks)` corrections that postdate existing entries
   — and there is no field that says which version an entry used.
3. **Report the number of runs prominently, and a range where there is more
   than one.** `runs: 1` is already in `leaderboard.json`; surfacing it in the
   rendered table, and encouraging 3 runs with a min–max, would let readers
   calibrate small gaps themselves.
4. **Consider stating a minimum meaningful gap** in the leaderboard docs. If
   the number above is roughly right, differences under ~8 points between
   single runs are not resolvable, and saying so protects submitters as much as
   readers.
5. **Log the completion-token count per step.** `total_tokens` is ~98% prompt
   and barely moved when the endpoint changed under me; reasoning tokens per
   action moved 20% in one step. It was the only signal that showed it, and I
   had to recover it from `traj.json` afterwards.

## Two code-level bugs found along the way

Both in `src/mobile_world/agents/base.py` at `83e7b8f`. Small, independent of
everything above, and I have minimal reproductions for each — happy to open
them as separate issues if that is easier to track:

- **`:115`** — `response.choices[0].message.content.strip()` raises when a
  reasoning model returns `finish_reason: "length"` with `content: None`. It
  surfaces as `Agent LLM failed` and an output-parsing error, which points away
  from the cause. Triggers readily because `:105` forces
  `enable_thinking: True` for `kimi-k*` while `max_tokens` defaults to 2048.
- **`:105-106`** — `kwargs["extra_body"] = {"enable_thinking": True}` is an
  assignment, so any `extra_body` the caller supplied is discarded. On
  OpenRouter that silently removes provider routing, which is how I discovered
  it: requests I believed were pinned were being served by three different
  providers. A `setdefault` merge fixes it and also lets a caller turn thinking
  off.

## Data

I have the raw per-run records for all 290 runs (score, steps, timings, token
usage, image digest, task-set commit, silent-retry counts), the per-task
trajectories, and the analysis scripts. **None of it is published yet — I
wanted you to see it first.** Tell me what would be useful and in what form:
a PR, an attached archive here, or a repository link once it is up.
