<!--
  Source template. Do not edit report/REPORT.md -- it is generated:

      python3 scripts/analyze.py
      python3 scripts/plots.py
      python3 scripts/render_report.py

  Every number below is a placeholder resolved out of data/analysis.json.
  Typing one in by hand defeats the only check this project has.
-->

# How much does a MobileWorld score move when nothing changes?

A measured noise floor for the [MobileWorld](https://github.com/Tongyi-MAI/MobileWorld)
mobile-agent benchmark: the same agent, the same configuration, the same tasks,
run {{ arm.trials_per_task.median | .0f }} times.

This is not a criticism of MobileWorld. Every benchmark that drives a real
operating system through a real UI against real network services has run-to-run
variance; that is the price of not being a static test set, and it is a price
worth paying. What has been missing is a number for it. Without one, a reader
cannot tell whether a two-point gap on the leaderboard means anything. This
report supplies the number, the data behind it, and the code to reproduce it.

---

## TL;DR

Three findings. The first is the one this project set out to measure. The
second and third were not planned, apply to any leaderboard built on hosted
inference, and are the reason this report is worth reading if you do not care
about MobileWorld at all.

### 1. A benchmark score moves when nothing changes, and the moves are as large as the gaps being cited

The same suite, re-run under an identical configuration, moved
**{{ arm.per_trial_suite_score.observed_range_pp | .1f }} points** between its
best and worst repetition — {{ arm.per_trial_suite_score.summary.min | .1f }}%
to {{ arm.per_trial_suite_score.summary.max | .1f }}%, across
{{ arm.per_trial_suite_score.n_trials_complete | #repetition }} with the model,
serving provider, quantization, image digest, task-set commit, temperature and
seed all pinned. {{ arm.flips.n_flipped }} of
{{ arm.flips.n_tasks_with_2plus_scored_trials | #task }} did not return the
same verdict every time.

Two entries need to differ by
**{{ arm.detectable_difference.mdd95_pp | .1f }} points** before one run each
can tell them apart. **{{ arm.leaderboard_gaps.n_adjacent_within_noise }} of
the {{ arm.leaderboard_gaps.n_adjacent_pairs }} adjacent gaps** on the
published GUI-Only board are smaller than that, as are
{{ arm.leaderboard_gaps.n_all_pairs_within_noise }} of its
{{ arm.leaderboard_gaps.n_all_pairs }} pairs overall.
{{ arm.leaderboard_gaps.n_single_run_entries }} of the
{{ arm.leaderboard_gaps.n_entries_on_board }} entries are single runs and none
reports an interval.

### 2. The serving endpoint changed mid-experiment. Nothing pins it, nothing records it, and nobody would see it
{{#if arm.regime_comparison.applicable}}

Partway through a thirty-hour run, with every knob still pinned, the provider
changed its serving configuration. **{{ arm.regime_comparison.tasks_worse | #task }} got worse and
{{ arm.regime_comparison.tasks_better }} got better**
(p = {{ arm.regime_comparison.sign_test_p | .3f }}).{{#if arm.regime_comparison.reasoning_tokens_per_action}} The model's reasoning per
action dropped
**{{ arm.regime_comparison.reasoning_tokens_per_action.change_pct_abs | .1f }}%**
in one step and held at the new level.{{/if}} An identical probe sent outside
the harness a day apart reproduces it; a second provider used as a control
does not move.

> Two vendors running this benchmark a day apart — same model id, same
> quantization, same provider — are compared against each other across a gap
> neither of them can see.

There is no flag that pins a serving configuration, and no leaderboard entry
records which one produced it. This is not a MobileWorld problem. It applies to
every leaderboard built on hosted inference.
{{/if}}

### 3. `temperature=0` is not determinism — and the obvious way to check gives the wrong answer

Three identical requests, same prompt, same fixed seed, temperature 0, returned
three different completions on **both** serving endpoints tested. Both advertise
`seed` support.

The methodological trap matters more than the result. An earlier check of the
same endpoint used a trivial prompt — "reply with OK" — and returned
byte-identical output twice, which reads as proof of determinism. A thirty-token
answer is identical under any sampler. **Determinism has to be probed at the
output length the real workload produces**, or the check silently confirms
whatever you hoped.

![the noise floor against the published entries](figures/fig2_noise_vs_leaderboard_{{ arm.arm }}.png)

---

## What was measured, and what was not

**Measured.** The distribution of the benchmark's own output when its input is
held fixed. One trial = one task, executed once, in a container created fresh
for that repetition round.

**Not measured.** Whether any model is better than any other. This report says
nothing about capability. It says how finely the instrument can distinguish
capabilities at all.

Two variance sources were separated:

| source | held down by |
|---|---|
| model sampling | `temperature=0`, fixed `seed`, provider and quantization pinned |
| environment: container state, timing, backend services, network | one fresh container per repetition round, strictly serial execution, pinned image digest, pinned task-set commit |

Arm `A_temp0` is the headline. It was designed on the assumption that what
survives `temperature=0` is mostly environment. **That assumption was tested
and does not hold.** Three identical requests to the pinned endpoint — same
prompt, same seed, temperature 0 — produced three different completions,
varying about twofold in length, on both serving providers examined. Both
advertise `seed` support in OpenRouter's capability list.

So this report does not claim to separate environment from model. Arm A
measures **the total run-to-run variance that survives every control available
to a careful experimenter**: pinned image digest, pinned task-set commit, fresh
container per round, serial execution, pinned provider and quantization,
temperature 0, fixed seed. That is the number a reader comparing two
leaderboard entries needs, because it is the noise they are up against
regardless of its origin.

### The task set

{{ arm.n_tasks }} GUI-Only tasks, chosen from public data before any compute was
spent, in three groups:

{{% bucket_table %}}

The groups come from the 18 trajectory bundles MobileWorld publishes: tasks that
every published run passed, tasks that every published run failed, and tasks
they disagreed on. Cross-model disagreement is **not** the same thing as
run-to-run variance — it is a prior about where to look. The two control groups
exist so that instability found in the third group can be compared against
something.

GUI-Only was chosen because it needs neither a second LLM playing the user nor
MCP credentials, so the configuration has fewer moving parts and is cheaper for
someone else to reproduce.

---

## Method

{{% provenance_table %}}

- **Serial.** `--max-concurrency 1`, no task shuffling. Concurrency and ordering
  are themselves timing variables.
- **Fresh container per repetition round.** Task objects are singletons reused
  across episodes (`tasks/registry.py:82`) and upstream has already had to fix
  cross-episode state leakage once (commit `5adc031`), so state is destroyed
  between rounds rather than trusted.
- **Provider pinned.** On OpenRouter a single model id fans out to many
  providers at different quantizations, which are not the same weights.
  Left unpinned, that routing variance would land in these results labelled as
  environment noise. `agents/pinned_e2e_agent.py` sends
  `{"provider": {"only": [...], "quantizations": [...], "allow_fallbacks": false}}`,
  so a request either runs on the pinned endpoint or fails loudly — and it
  merges that into whatever the harness put there, because the harness
  overwrites the field for this model family (above). Every request's actual
  serving provider is read back off the response and written into the trial's
  own log, so the pinning is evidenced rather than assumed.
- **Pass threshold `score > {{ analysis.settings.pass_threshold }}`**, which is
  upstream's own (`core/log_viewer/utils.py:489`). Note that `runner.py` logs
  success as `score > 0.0`; the two disagree, and the stricter one is used here.
- **Nothing under `vendor/` was modified.** The custom agent is supplied through
  the harness's own `--agent-type <path.py>` hook, so reproducing this does not
  require forking the benchmark.

Runs that produced no score are reported separately rather than counted as
failures:

{{% outcomes_table %}}

---

## Results

### The direct observation

Each repetition is a complete pass over the task set, so each one has a suite
score. These are raw counts — no model, no resampling:

{{% per_trial_table %}}

Best minus worst: **{{ arm.per_trial_suite_score.observed_range_pp | .1f }}
points**. The mean absolute difference between any two repetitions is
{{ arm.per_trial_suite_score.pairwise_abs_difference_pp.mean | .1f }} points.

### Per task

![per-task pass rates](figures/fig1_per_task_pass_rate_{{ arm.arm }}.png)

Tasks that did not give the same answer every time, most unstable first:

{{% flipped_tasks_table %}}

`flip rate` is the share of trial *pairs* that disagree — `k(n−k) / C(n,2)` for
`k` passes out of `n` trials. Its maximum is not 1 and depends on `n`: at the
`n = {{ arm.flips.n_trials_typical }}` of this arm, an even split scores
{{ arm.flips.flip_rate_max_typical | .3f }}. Mean across all tasks:
{{ arm.flips.mean_flip_rate | .3f }}.

![flip rate distribution](figures/fig3_flip_rate_distribution_{{ arm.arm }}.png)

Tasks that were perfectly consistent — useful as a check that the setup is not
simply noisy everywhere:

{{% stable_tasks_table %}}

### Tasks that randomise their own initial state

Seven tasks in the benchmark call `random.choice` / `randint` / `sample` in
their own setup, and nothing anywhere in the codebase calls `random.seed()`.
The README's claim that each execution "begins from identical initial
conditions" does not hold for them: they are a different problem each run.
This pilot contains {{ arm.subgroups.randomized_setup.n_tasks }} of them, never pooled with the rest, because {{ arm.subgroups.randomized_setup.n_tasks | ?it measures|they measure }} something else.

{{% randomized_setup_table %}}

### The total score

Three numbers that are easy to confuse, so all three are reported:

| question | 95% interval | σ |
|---|---|---|
| If this same {{ arm.n_tasks }}-task set were run again, where would the total land? **Headline.** | {{ arm.rerun.posterior.ci95_pct.0 | .1f }}–{{ arm.rerun.posterior.ci95_pct.1 | .1f }}% | {{ arm.rerun.posterior.sd_pp | .2f }} pp |
| Same, pretending {{ arm.trials_per_task.median | .0f }} trials pinned each rate down exactly — a lower bound | {{ arm.rerun.plugin.ci95_pct.0 | .1f }}–{{ arm.rerun.plugin.ci95_pct.1 | .1f }}% | {{ arm.rerun.plugin.sd_pp | .2f }} pp |
| How precise is *this experiment's own* number? | {{ arm.estimate.ci95_pct.0 | .1f }}–{{ arm.estimate.ci95_pct.1 | .1f }}% | {{ arm.estimate.sd_pp | .2f }} pp |

A total over {{ arm.n_tasks }} tasks moves in discrete steps — each task either
passes or it does not — so the first two rows can quote the same interval while
differing in σ: their percentiles land on the same step. At this size σ is the
comparison that carries information.

Carried up to the real GUI-Only suite — the pilot's three groups reweighted to
their true sizes ({{ arm.gui_only_extrapolation.strata_sizes.split }} split,
{{ arm.gui_only_extrapolation.strata_sizes.all_pass }} always-passed,
{{ arm.gui_only_extrapolation.strata_sizes.all_fail }} always-failed, out of
{{ arm.gui_only_extrapolation.total_slots }}).

**This is the report's largest inferential step and it is conditional**: *if*
the {{ arm.gui_only_extrapolation.strata_unmeasured.split }} split-group tasks
that were not run behave like the
{{ arm.gui_only_extrapolation.strata_measured.split }} that were, then —

| | 95% interval | width | σ |
|---|---|---|---|
| fixed task set, noise only | {{ arm.gui_only_extrapolation.fixed_taskset.ci95_pct.0 | .1f }}–{{ arm.gui_only_extrapolation.fixed_taskset.ci95_pct.1 | .1f }}% | **{{ arm.gui_only_extrapolation.fixed_taskset.ci95_width_pp | .1f }} pp** | {{ arm.gui_only_extrapolation.fixed_taskset.sd_pp | .2f }} pp |
| also resampling which tasks are in the suite | {{ arm.gui_only_extrapolation.resampled_tasks.ci95_pct.0 | .1f }}–{{ arm.gui_only_extrapolation.resampled_tasks.ci95_pct.1 | .1f }}% | {{ arm.gui_only_extrapolation.resampled_tasks.ci95_width_pp | .1f }} pp | {{ arm.gui_only_extrapolation.resampled_tasks.sd_pp | .2f }} pp |

The first row is the one to compare with a leaderboard gap: it asks how much a
score moves with the task set held fixed, which is what happens when two
vendors run the same benchmark. The second row also asks whether the other
{{ arm.gui_only_extrapolation.strata_unmeasured.split }} disagreed-on tasks
behave like the {{ arm.gui_only_extrapolation.strata_measured.split }} measured
here, which is a question about
generalisation, not about noise.

All resampling is seeded (`{{ analysis.settings.seed }}`,
{{ analysis.settings.bootstrap_resamples }} resamples), so the same
`runs.jsonl` always produces the same `analysis.json`.

{{#if arm.leave_one_out.variants}}
### Does any single round drive this?

The obvious challenge to a spread measured over a handful of repetitions is
that one unusual round produced it — and one round here *was* unusual: a
provider outage cost it six scores and most of the run's silent retries.
Singling that round out for a robustness check would invite the suspicion that
it was chosen after seeing the answer, so every round is dropped in turn and
everything recomputed from scratch on what is left:

{{% leave_one_out_table %}}

The detectable difference stays between
**{{ arm.leave_one_out.mdd95_pp_min | .2f }} and
{{ arm.leave_one_out.mdd95_pp_max | .2f }} points** whichever round is removed,
against {{ arm.detectable_difference.mdd95_pp | .2f }} for the full set — the
headline sits inside its own leave-one-out range rather than at an edge of it.
The count of adjacent leaderboard gaps too small to resolve is
{{ arm.leave_one_out.leaderboard_adjacent_within_noise_min }}–{{ arm.leave_one_out.leaderboard_adjacent_within_noise_max }}
of {{ arm.leaderboard_gaps.n_adjacent_pairs }} across every variant.

Dropping the outage round specifically does not weaken the result; it makes the
interval slightly *narrower*, and at least one other round narrows it more.

---
{{/if}}

### The harness absorbed some of this before it could be measured

Inside a task, an exception containing `"Device is not healthy"` makes the
harness sleep, rename the partial trajectory to `*_backup_<ts>`, and rerun in
place, up to twice. Across rounds, `--auto-retry` (default 10) re-runs tasks
that left no `result.txt`. It does **not** re-run tasks that scored 0 — so this
is not best-of-N and the leaderboard is not inflated by it. But it does mean the
variance reported here is what survives two layers of silent retry.

Silent restarts observed in this run: **{{ arm.absorbed_retries.total }}**.

They are not spread evenly. Broken down by repetition round:

{{% absorbed_retries_by_trial_table %}}

The single worst round accounts for
**{{ arm.absorbed_retries.max_trial_share | .0%}}** of them. That concentration
is the signature of an infrastructure incident rather than of task difficulty —
a hard task is hard in every round, an outage happens in one. During this pilot
the serving provider returned HTTP 401 for about an hour; the harness silently
retried through it, some runs survived and some were lost, and **not one of the
scored results carries any trace of it**. Without this column, an hour of total
provider unavailability would be invisible in the data.

Unscored runs, by round:

{{% outcomes_by_trial_table %}}

By task:

{{% absorbed_retries_table %}}

Every one of those is instability that never reached a score. **The measured
noise floor is a floor in both senses.**

---

{{#if arm.regime_comparison.applicable}}
## The serving endpoint changed underneath the experiment

Everything a careful experimenter can pin was pinned: container image digest,
task-set commit, model id, serving provider, quantization, temperature, seed,
one fresh container per round, strictly serial execution. Partway through, the
results changed character anyway.

{{% regime_table %}}

**{{ arm.regime_comparison.tasks_worse | #task }} got worse and
{{ arm.regime_comparison.tasks_better }} got better** across the boundary
({{ arm.regime_comparison.tasks_unchanged }} unchanged). A split that one-sided
happens by chance with probability
**{{ arm.regime_comparison.sign_test_p | .3f }}**. Noise moves tasks in both
directions; this did not.

{{% regime_moved_tasks_table %}}

The cause is visible in how much the model reasoned per action, which fell by
about a fifth and then held steady at the new level — a step, not a drift. The
endpoint began generating less reasoning per call, so each step got *faster*
while decisions got worse, more steps were needed, more runs hit the round cap,
and the score fell. Degrading hardware or a slow network would have made things
slower, not quicker.

An identical probe sent outside the harness 24 hours apart reproduces it, and a
second provider used as a control does not move. The full evidence is in the
paper (Section 4) and `ENVIRONMENT.md` §8 and §12.

**This is the finding, not a caveat to it.** There is no flag that pins a
serving configuration, and no leaderboard entry records which one produced it.
Two vendors running this benchmark a day apart, with the same model id at the
same quantization from the same provider, can be compared against each other
across a gap neither of them can see.

It also means the pooled spread across all rounds is **not** a pure noise
floor: it mixes run-to-run noise with a one-off configuration change. Where a
single number is quoted below, it comes from within one regime.

---
{{/if}}

## What this means for reading the leaderboard

Two single runs must differ by **{{ arm.detectable_difference.mdd95_pp | .1f }}
points** before the difference is outside what noise produces on its own; to
detect a real difference reliably (80% of the time) it needs to be
{{ arm.detectable_difference.mdd95_power80_pp | .1f }} points.

{{% leaderboard_gap_table %}}

This is an indication of scale, not a re-scoring. The noise floor was measured
for one model, on {{ arm.n_tasks }} tasks, and is applied above to entries
produced by other agents and other models. It says which gaps *deserve* a
second run before being cited, not which ones are wrong.

Three things follow, none of which requires agreeing with the exact number:

1. **A single run is not a measurement.** Reporting `runs: 1` with no interval
   invites readers to compare numbers that are not comparable. Three runs and a
   range would cost 3× the compute and remove most of the ambiguity.
2. **Rank order near the top is unstable.** Adjacent entries separated by less
   than the noise floor can swap places on a re-run without anything changing.
3. **"State of the art by N points" needs N stated against a noise floor.**

### Things already true without running anything

These come from the public repository alone and are independent of everything
above:

- **{{ arm.leaderboard_gaps.n_single_run_entries }} of the
  {{ arm.leaderboard_gaps.n_entries_on_board }} leaderboard entries are single
  runs** (`runs: 1`); the remaining
  {{ arm.leaderboard_gaps.n_repeated_entries }} report `runs: 3`. No entry
  reports variance or an interval.
- **Claude's temperature is deleted before the request is sent.**
  `agents/base.py:96-99`: if `"claude" in model`, `del kwargs["temperature"]`.
  Every Claude entry therefore ran at the API default, not at 0 — so entries on
  the same board carry structurally different sampling noise.
- **`-no-snapshot` is passed to the emulator** (`docker/start_emulator.sh`), so
  the README's "AVD snapshots ensure identical initial state" is not what
  happens; initial state is whatever each task's `initialize_task` establishes.
- **One published run in five ended by running out of steps, not by finishing.**
  Across the 2,562 (model, task) runs in the trajectory bundles upstream
  publishes, **525 — 20.5% — have a step count of exactly 50**, against a
  maximum observed anywhere of 54. That spike is the round cap, and a run that
  hits it is scored 0. Those zeros are not the agent getting the task wrong;
  they are the agent being stopped. `mw eval` itself defaults to no cap at all
  (`core/subcommands/eval.py:373`, `max_step=args.max_round or -1`) — the 50
  comes from the evaluation server's own defaults
  (`core/eval_server/db.py:40`, `routes.py:642`), so which number applies
  depends on how the run was launched, and nothing in a published entry records
  which. This study passes `--max-round 50` explicitly, to match the published
  runs rather than to be generous.
- **Caller-supplied request options are discarded for some models, by name.**
  `agents/base.py:105-106` replaces `extra_body` wholesale — not merges —
  whenever the model id contains `kimi-k`, so any routing, provider or
  serving option the caller set is dropped at the moment of the call. Two lines
  above, the same function deletes `temperature` for Claude. An experiment that
  pins its serving endpoint therefore has to verify where the request was
  actually served, because the configuration it set is not the configuration
  that was sent. On OpenRouter the difference is not cosmetic: identical
  unpinned requests in this study were served by three different providers, and
  the same trivial prompt drew 87, 476 and 228 reasoning tokens depending on
  which. Combined with `base.py:115`, which calls `.strip()` on a message
  content that reasoning models may return as `None`, a verbose provider and
  the stock `max_tokens` of 2048 can truncate the answer away entirely and fail
  the task through every retry. (Checked against the published bundles: this
  does *not* appear to have affected the leaderboard's own reasoning-model
  entries, which miss a score on well under 1% of tasks.)
- **The harness's own container cleanup silently does nothing unless the image
  tag is `latest`.** `mw env rm --all` selects containers by image substring
  against `DEFAULT_IMAGE = "ghcr.io/tongyi-mai/mobile_world:latest"`
  (`runtime/utils/models.py:28`, `core/api/env.py:507`,
  `runtime/utils/docker.py:102`), and exposes no way to override it. Pin the
  image to any released version and the command prints `No containers to
  destroy` and exits 0 while the containers keep running; the next launch takes
  the next free port, and an evaluation pointed at the original port keeps
  talking to the first container ever created. Every episode then shares one
  container's accumulated state — which is what upstream's own commit
  `5adc031`, "reset per-episode state on reused singleton task instances", was
  written to prevent. Nothing reports it: the stale container is healthy, so
  the health check passes.
- **Entries from different dates were scored by different verifier versions.**
  Upstream's history contains a run of verifier fixes postdating existing
  entries: `8ae5064` (wrong scoring for accept/cancel meeting), `c33e340` (a
  correct MMMU-Pro answer rejected), `32ed916` (phone-format false negatives),
  `702e904` (a task depended on the external site placehold.co), plus the
  README's own note that Mattermost session expiry produced false negatives.

---

## Scoring bugs are a separate finding

Some disagreements between trials are not environment noise: the agent did the
task and was scored 0, or did not and was scored 1. Those are checker bugs, and
they are verifiable from the artifacts by anyone.

`scripts/find_candidates.py` ranks runs worth opening — minority outcomes inside
unstable tasks first, then passes that finished in fewer steps than any
published run of the same task needed, then runs cut off at `--max-round`. The
human judgement on each one is recorded in
[checker_issues.md](checker_issues.md), which is a separate document with its
own evidence; nothing from the ranking script is presented as a finding.

---

## Limitations

1. **`temperature=0` is not determinism, and this was measured rather than
   assumed.** Three identical requests at temperature 0 with a fixed seed
   returned three different completions on both serving endpoints tested, with
   completion lengths varying about twofold; both providers advertise `seed`
   support. Arm A is therefore not an environment measurement, and no
   environment/model decomposition is claimed anywhere in this report. A
   cautionary note on method: an earlier check that used a trivial prompt
   ("reply with OK") *did* return byte-identical output twice and suggested the
   endpoint was deterministic. A thirty-token answer is identical under any
   sampler. Determinism has to be probed at the output length the real workload
   produces.
2. **One agent, one model, one serving endpoint.** Instability is very likely
   model-dependent — a model that is decisive on a task will flip less than one
   that is borderline on it. Applying this noise floor to other entries is an
   extrapolation.
3. **{{ arm.n_tasks }} tasks, {{ arm.trials_per_task.median | .0f }} trials.**
   {{ arm.trials_per_task.median | .0f }} trials pin a per-task pass rate down
   only as far as the Wilson intervals in the table above — wide, in the middle
   of the range. That uncertainty is carried through the headline interval,
   which is why the Jeffreys-posterior row is the headline and the plug-in row
   is labelled a lower bound. It does not make it go away.
4. **The pilot is not a random sample.** It deliberately oversamples tasks the
   published runs disagree on. The extrapolation to
   {{ arm.gui_only_extrapolation.total_slots }} tasks reweights the groups back
   to their true sizes, but still assumes the
   {{ arm.gui_only_extrapolation.strata_measured.split }} measured split-group
   tasks represent the other
   {{ arm.gui_only_extrapolation.strata_unmeasured.split }}.
5. **GUI-Only only.** The 44 user-interaction and 40 MCP tasks were not run.
   User-interaction tasks add a second LLM to the loop and would plausibly be
   noisier, not less.
6. **The harness's own retries absorbed part of what was being measured**
   (above). Measured variance is net of them.
7. **Image `v1.4`, not what most leaderboard entries used.** `latest` and `v1`
   are the same 2025-12-24 build — the oldest one — and upstream's changelog
   says v1.2 fixed iptables-NAT failures that cause "deadlocked container
   launches and silent eval failures" on 6.x kernels. This host runs a 6.x
   kernel, so running `latest` would have injected a known, already-fixed bug
   into a noise measurement. The trade-off is that the environment is not
   byte-identical to the one most entries ran on. The digest is recorded on
   every row.
8. **The serving endpoint differs from the reference entry's.** The
   published Kimi-K2.5 GUI-Only figure ran on Moonshot's own `int4` endpoint;
   this ran on AtlasCloud's `int4`. Same nominal quantization, different
   provider and serving stack. The published figure was used
   to check that the model sits mid-range, where flips are possible at all —
   not as a target to match.
9. **Absolute level is not comparable to a leaderboard score.** Only the
   *width* transfers. The pilot's own total is a property of a deliberately
   skewed task set.

---

## Reproducing this

Offline half — needs nothing but the upstream checkout, spends nothing:

```bash
git clone --depth 50 https://github.com/Tongyi-MAI/MobileWorld.git vendor/MobileWorld
```

```bash
python3 scripts/extract_task_registry.py && python3 scripts/build_public_matrix.py && python3 scripts/select_pilot_tasks.py
```

The evaluation itself needs a Linux host with `/dev/kvm` — the image is
`linux/amd64` only and the CLI checks the host for KVM (`core/api/env.py:710`),
so it cannot run on macOS. `scripts/provision/` documents a host that works.

```bash
python3 scripts/run_matrix.py --arm {{ arm.arm }} --trials {{ arm.trials_per_task.median | .0f }} --tasks data/pilot_all.txt --agent-type agents/pinned_e2e_agent.py --model-name {{ arm.provenance.values.model }} --llm-base-url {{ arm.provenance.values.llm_base_url }} --or-provider {{ arm.provenance.model_params.or_provider }} --or-quant {{ arm.provenance.model_params.or_quant }} --temperature {{ arm.provenance.model_params.temperature }} --seed {{ arm.provenance.model_params.seed }}
```

Then:

```bash
python3 scripts/analyze.py && python3 scripts/plots.py && python3 scripts/render_report.py
```

`data/runs.jsonl` is the raw record — one line per trial, appended and fsync'd
as each trial finishes. Its sha256 is stamped into `analysis.json` and onto
every figure, so a figure can always be traced back to the data that produced
it.

---

## Provenance

Upstream: [Tongyi-MAI/MobileWorld](https://github.com/Tongyi-MAI/MobileWorld),
Apache-2.0, pinned at `{{ arm.provenance.values.taskset_commit }}`. Nothing
under `vendor/` was modified.

Generated {{ analysis.generated_at }} by `scripts/render_report.py` from
`data/analysis.json`, arm `{{ arm.arm }}`
({{ arm.n_rows }} trials, provenance-consistent:
{{ arm.provenance.consistent }}).
