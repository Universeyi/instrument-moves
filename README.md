# The Instrument Moves

Data, analysis code and figure scripts for **The Instrument Moves: Unrecorded
Serving Changes in Agent Benchmarks** (Jingyuan Yi, 2026), accepted as a poster
at the NeurIPS 2026 workshop *Who Verifies the Agents?*.
Paper: [`paper/instrument-moves.pdf`](paper/instrument-moves.pdf) · arXiv: _to be added_

We pinned every control the [MobileWorld](https://github.com/Tongyi-MAI/MobileWorld)
mobile-agent benchmark exposes (model identifier, serving provider,
quantization, `temperature=0`, seed, image digest, task-set commit, a fresh
container per repetition, serial execution) and ran the same agent on the same
29 tasks ten times. Three things followed:

1. **`temperature=0` with a fixed seed was not deterministic** on either
   endpoint we tried. Our first check said otherwise: a short prompt came back
   byte-identical twice, on an endpoint that turned out not to be deterministic.
2. **Ten and a half hours in, the pinned endpoint's behaviour changed.** Ten
   tasks got worse and none got better. Median completion tokens per action fell
   by a fifth at a single round boundary and stayed there. We observed the
   endpoint's behaviour, not its configuration: the shift was specific to that
   endpoint, absent from a control, and invisible to every field a leaderboard
   records.
3. **Two single-run scores need to differ by roughly eight points** (8.07; a
   95% interval of 5.18–8.53 when whole rounds are resampled) before the gap
   exceeds what repetition alone produces, for this agent, this endpoint and
   this reweighted task sample.

The token signal flagged the shift at the round level; the score did not. The
signal costs one integer per request to log.

## What is here

```
paper/          arXiv source (main.tex, sections/, figures/, refs.bib, main.bbl) and the PDF
data/           every measurement the paper uses; runs.jsonl is the raw record, one row per run
scripts/        the pipeline, from task extraction to the paper's tables and figures
agents/         the pinned agent used for every run
report/         the long-form report, the round-6 sensitivity analysis, forensics of the
                public bundles, scoring issues found, and upstream issue drafts
ENVIRONMENT.md  what the benchmark's harness actually does, where docs and code disagree
```

Key data files:

| File | What it is |
|---|---|
| `data/runs.jsonl` | 314 runs: 290 in the main arm (`A_temp0`, 29 tasks × 10 rounds), 20 provider calibration runs, 4 smoke runs. Host names and paths scrubbed |
| `data/analysis.json` | Rounds 4–10, with leave-one-round-out; the paper's headline regime |
| `data/analysis_headline.json` | Rounds 4–10 without round 6 |
| `data/analysis_all.json` | All ten rounds, with the before/after comparison at round 4 |
| `data/sensitivity_round6.json` | The two above side by side; see `report/SENSITIVITY.md` |
| `data/traj_stats.json` | Per-run completion tokens and steps from the raw trajectories |
| `data/probe_determinism.json` | The out-of-harness probes: determinism and the 24-hour endpoint check |
| `data/public_matrix.json` | Model × task outcomes from the 18 trajectory bundles upstream publishes |
| `data/public_matrix.lenient.json` | The same, reading the three bundles that use other result formats |
| `data/tasks.json` | All 201 task definitions, statically extracted |
| `data/camera_ready_stats.json` | Sensitivity of the threshold: round-level bootstrap, shared round effects, leave-one-task-out, 80% power |
| `data/detector_checks.json` | Tokens per step as a change signal: the round-by-round test in this run, and the endpoint sentinel's false-alarm record |
| `data/sentinel_alerts_snapshot.json` | Snapshot of the endpoint sentinel's daily drift tests (2026-09-06 to 09-29) behind `detector_checks.json` |

Each `runs.jsonl` row records the outcome (`outcome, score, pass, reason,
steps, total_tokens, duration_s, started_at, ended_at`) together with the full
configuration it ran under (`model, llm_base_url, model_params, agent_type,
agent_env, image, image_digest, taskset_commit, taskset_dirty, max_round,
auto_retry, recycle_policy, host_spec`), none of which the upstream harness
records itself. `absorbed_retries` counts the retries the
harness performed silently inside a run (`ENVIRONMENT.md` §2.2).

## Check the paper's numbers

Standard library only, any machine, a few seconds:

```bash
python3 scripts/audit_paper_numbers.py
```

It reads every number the paper states against `data/` and fails on any
mismatch (62 claims at the time of release).

## Rerun the analysis

```bash
python3 scripts/analyze.py --trials 4:10 --leave-one-out --regime-split 4 --out data/analysis.json
python3 scripts/analyze.py --trials 4:10 --exclude-trials 6 --out data/analysis_headline.json
python3 scripts/analyze.py --arm A_temp0 --regime-split 4 --out data/analysis_all.json
```

Each reproduces the committed file exactly, apart from its `generated_at`
stamp. Then:

```bash
python3 scripts/make_appendix_tables.py   # paper/sections/tables/
python3 scripts/plots_paper.py            # paper/figures/ (needs matplotlib)
python3 scripts/plots.py                  # report/figures/ (needs matplotlib)
python3 scripts/render_report.py          # report/REPORT.md from its template
python3 scripts/refresh_sensitivity.py     # threshold fields of data/sensitivity_round6.json
python3 scripts/camera_ready_stats.py      # data/camera_ready_stats.json
python3 scripts/detector_checks.py         # data/detector_checks.json
```

`report/REPORT.md` is generated from `report/REPORT.template.md`, which holds
prose and placeholders and no numbers of its own; an unresolvable placeholder
stops the render. `python3 scripts/selftest.py` runs the whole chain over
synthetic data in a temporary directory and checks it is internally
consistent.

The public-data side needs an upstream checkout:

```bash
git clone https://github.com/Tongyi-MAI/MobileWorld.git vendor/MobileWorld
git -C vendor/MobileWorld checkout 83e7b8f
python3 scripts/extract_task_registry.py
python3 scripts/build_public_matrix.py            # add --lenient for the lenient matrix
python3 scripts/select_pilot_tasks.py
python3 scripts/forensic_signals.py
```

## Run it again

New runs need an x86_64 Linux host with `/dev/kvm` (`ENVIRONMENT.md` §4);
`scripts/provision/` describes the machine used and bootstraps one. Then:

```bash
python3 scripts/run_matrix.py --arm A_temp0 --trials 10 --tasks data/pilot_all.txt \
    --agent-type agents/pinned_e2e_agent.py \
    --model-name moonshotai/kimi-k2.5 --llm-base-url https://openrouter.ai/api/v1 \
    --or-provider atlas-cloud --or-quant int4 --temperature 0.0 --seed 42
```

This is the main arm's configuration as recorded on every row.

`--dry-run` prints the plan on any machine. Runs are resumable, and every row
is written and fsync'd as it completes. The endpoint you reach may not behave like
the one measured here. The raw trajectories and
screenshots of the original runs are not published.

## Licence

Code (`scripts/`, `agents/`): MIT, see [`LICENSE`](LICENSE). Data, report and
paper text: CC BY 4.0. Task content and the public-bundle matrices are derived
from MobileWorld (Apache-2.0); see [`NOTICE`](NOTICE). `paper/neurips_2026.sty`
is the NeurIPS style file, distributed under its own terms.

## Citation

```bibtex
@misc{yi2026instrument,
  title  = {The Instrument Moves: Unrecorded Serving Changes in Agent Benchmarks},
  author = {Yi, Jingyuan},
  year   = {2026},
  url    = {https://github.com/Universeyi/instrument-moves}
}
```
