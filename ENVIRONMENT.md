# What the MobileWorld environment actually does

Facts about the benchmark's harness that the scripts in this repository depend
on, and where the upstream documentation and code disagree. Each item is marked
with its source: `[doc]` upstream documentation, `[code]` read from the upstream
source, `[registry]` read from the container registry, `[measured]` measured by
us, `[public data]` computed from upstream's published trajectories.

Upstream snapshot: [Tongyi-MAI/MobileWorld](https://github.com/Tongyi-MAI/MobileWorld)
at `83e7b8f` (2026-08-21). File and line references are to that commit.

## 1. Licence

MobileWorld is Apache-2.0. Nothing in it restricts publishing evaluation
results, and upstream explicitly invites community submissions of scores and
trajectories (`docs/submit.md`, `site/bundle_trajs.py`). `[code]` `[doc]`

## 2. How one evaluation runs

### 2.1 The task loop `[code]`

`src/mobile_world/core/runner.py::_execute_single_task` initialises the task,
loops agent prediction and action execution until the agent finishes, answers,
fails or hits the step cap, then asks the server for a score. Scoring happens
server-side, inside the container; the client receives a `score` and a free-text
`reason`.

### 2.2 Two layers of retry, undocumented `[code]`

1. **Inside a task.** When an exception mentions `"Device is not healthy"`,
   `_process_task_on_env` sleeps 20 s, renames the partial trajectory to
   `*_backup_<ts>`, and reruns the task in place, up to twice
   (`retry_on_device_unhealthy=2`).
2. **Whole pass.** `run_agent_with_evaluation` makes up to
   `min(1 + auto_retry, 10)` passes, each rerunning only tasks that have no
   `result.txt` yet.

The outer retry picks up tasks that crashed, not tasks that finished with a
zero (a zero still writes `result.txt`), so it is not best-of-N and does not
inflate leaderboard scores. It does mean the variance we measure is what is
left after the harness absorbed some of it. The number of `*_backup_*`
directories is the number of absorbed retries; `run_matrix.py` records it and
`analyze.py` reports it.

### 2.3 Artefacts `[code]`

Per task: `traj.json` (per-step `task_goal`, `step`, `prediction`, `action`,
`ask_user_response`, `tool_call`, plus token usage), `result.txt`
(`score: 1.0\nreason: ...`), screenshots, and a loguru thread log. Not
recorded anywhere: timestamps in `traj.json`, sampling parameters, image
digest, task-set commit, host. `run_matrix.py` adds all of these.

### 2.4 Pass threshold `[code]`

A task passes when `score > 0.99` (`core/log_viewer/utils.py:489`,
`core/subcommands/eval.py::generate_pass_k_report`). `runner.py` logs success
as `score > 0.0`, which reads more generously than the leaderboard; we use
`> 0.99`. In the 18 published bundles every score is 0.0 or 1.0, so the choice
does not change any result. `[public data]`

## 3. Sampling parameters

### 3.1 Defaults are hard-coded per agent `[code]`

`mw eval` has no `--temperature` flag. Every agent sets `temperature=0.0` in
its `runtime_conf` except `seed_agent` (0.7, top_p 0.9).

### 3.2 Claude models run at the API default temperature `[code]`

`src/mobile_world/agents/base.py:96-99` deletes `temperature` when the model
name contains `claude`. Leaderboard entries for Claude models were therefore
not run at temperature 0.

### 3.3 No agent sends a seed `[code]`

Apart from the `uiins` grounding model, no agent passes `seed`. Changing the
sampling configuration therefore needs an agent file of one's own:
`--agent-type path/to/agent.py` loads it via `load_agent_from_file`, which is
how `agents/pinned_e2e_agent.py` is used without modifying upstream.

## 4. Host requirements

- **x86_64 Linux with `/dev/kvm`** (bare metal, or a cloud instance with nested
  virtualisation). `check_kvm_available()` fails without it; the image is
  `linux/amd64` only `[registry]` and runs an x86_64 Android emulator inside a
  privileged docker-in-docker container. Apple Silicon cannot run it.
- Real-device mode (`docs/real-devices.md`) runs ad-hoc goals with no verifier
  and no score, so it cannot be used for pass/fail statistics.
- Disk: 200 GB or more (10.5 GB compressed image, container layers,
  screenshots). Memory: 32 GB or more for one container at a time.
- Use image v1.2 or later on 6.x kernels (automatic nft/legacy iptables
  detection, `docs/docker_changelog.md`).

`scripts/provision/` has the machine we used and a bootstrap script.

## 5. Seven tasks randomise their own initial state `[code]`

Nothing in the repository seeds the process RNG, and seven tasks call
`random.choice` / `randint` / `sample` during setup: `CVEmailTask`,
`CVEmailAskUserTask`, `InvoiceReceiptCopyTask`,
`InvoiceReceiptCopyAskUserTask`, `ProjectPaperEmailAskUserTask`,
`ReviewPaperEmailTask`, `DeleteUselessFilesAskUserTask`. Each run of these is a
different instance of the task, so the upstream statement that every execution
begins from identical initial conditions does not hold for them. They are
flagged `randomized_setup: true` in `data/tasks.json` and reported separately.

## 6. The static task registry matches the leaderboard `[code]`

`scripts/extract_task_registry.py` parses the task definitions without
importing them: 201 tasks, 117 GUI-only, 44 user-interaction, 40 MCP, matching
the counts upstream publishes in `site/leaderboard.json`.

## 7. Task objects are singletons `[code]`

`tasks/registry.py::_register_tasks_from_module` instantiates each task once
and reuses the object across episodes (see upstream commit `5adc031`, which
fixed one resulting state leak). `run_matrix.py` uses a fresh container for
every trial so that no state can carry over.

## 8. Serving routes are a variance source `[measured]`

On OpenRouter one model id is served by many providers at different
quantizations (on 2026-08-24, 20 providers for `moonshotai/kimi-k2.6`, 10 for
`kimi-k2.5`, across fp4, int4, fp8 and bf16). Unpinned, consecutive requests
can land on different weights. `agents/pinned_e2e_agent.py` sends

```json
{"provider": {"only": ["<slug>"], "quantizations": ["<quant>"], "allow_fallbacks": false}}
```

and reads the serving provider back from each response.

Declared endpoint metadata was wrong in both directions: `seed` is declared and
has no effect (§12); implicit caching is declared unsupported and happens
(19.1% of prompt tokens were billed as cached in Arm A).

Configuration used for the main arm: `moonshotai/kimi-k2.5` on `atlas-cloud`,
int4, with `deepinfra` fp4 as the control endpoint in the out-of-harness probes.
The upstream reference score (49.6% GUI-only) was run on Moonshot's own int4
endpoint, a different provider; it indicates the difficulty regime, not a
target.

## 9. `mw env rm --all` does nothing for pinned image tags `[code]` `[measured]`

`remove_containers` filters by the substring
`ghcr.io/tongyi-mai/mobile_world:latest` (`runtime/utils/models.py:28`), and
`mw env rm` has no `--image` option. Containers started from any other tag are
never removed, and the command exits 0 with `No containers to destroy`. Each
recycle then starts a new container on the next port while the evaluator keeps
talking to the first, so every trial shares one accumulating container, and
health checks stay green.

`run_matrix.py::sweep_env_containers` removes containers by name prefix
instead, and `check_env_topology` refuses to continue unless exactly one
container is running on the expected port. Reported upstream; see
`report/upstream_issues/`.

## 10. `extra_body` is overwritten by model name `[code]` `[measured]`

`agents/base.py:105-106` assigns `{"enable_thinking": True}` to `extra_body`
for Kimi models, replacing whatever the caller set, so provider pinning passed
through `runtime_conf` never reaches the API. Measured on one prompt: unpinned
requests were served by different providers with reasoning lengths differing
fivefold; pinned requests were served by the requested provider every time.

A consequence: `base.py:115` calls `.strip()` on `content` without a `None`
check. With thinking forced on and the stock `max_tokens=2048`, a verbose
provider can exhaust the budget on reasoning, return `content=None`, and turn
a run into a harness error. `pinned_e2e_agent.py` merges rather than replaces
`extra_body` and records the provider that actually served each response.
Both problems are written up for upstream in `report/upstream_issues/`.

## 11. The 50-step cap comes from one launch path only `[code]` `[public data]`

`--max-round` has no CLI default (`core/subcommands/eval.py:373,448`:
`args.max_round or -1`, i.e. unlimited); 50 is the default of the eval-server
path (`core/eval_server/db.py:40,81`, `routes.py:642`). In the published
bundles, 525 of 2,562 runs (20.5%) end at exactly 50 steps. We pass
`--max-round 50` explicitly to match the leaderboard.

## 12. `temperature=0` with a seed is not deterministic `[measured]`

2026-08-25: the same prompt, `temperature=0`, `seed=42`, thinking on,
`max_tokens=8192`, three requests per provider:

| provider / quantization | output tokens | identical? |
|---|---|---|
| `deepinfra` / fp4 | 2963 / 1448 / 1494 | no |
| `atlas-cloud` / int4 | 1571 / 2812 / 1427 | no |

Both declare `seed` support. (An earlier test with a 33-token reply came back
byte-identical; short outputs say nothing about determinism.)

So Arm A (temperature 0, fixed seed) measures the total variance left after
every available control, environment plus irreducible sampling, not
environment variance alone. Arm B − Arm A is what default sampling adds on top,
a lower bound on the model's share.
