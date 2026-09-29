#!/usr/bin/env python3
"""Orchestrate repeated MobileWorld runs for the stability audit.

One trial = one task executed once in a freshly created container.
Runs strictly serially: concurrency is itself a timing variable we are trying
to hold fixed.

Everything needed to reproduce a row is written to data/runs.jsonl as soon as
the trial finishes; nothing is accumulated in memory.

Requires a Linux host with /dev/kvm. See ENVIRONMENT.md §4 — this cannot run on
macOS. `--dry-run` prints the exact command sequence anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENDOR = ROOT / "vendor" / "MobileWorld"
RUNS_JSONL = ROOT / "data" / "runs.jsonl"

SCORE_RE = re.compile(r"score:\s*([-\d.eE]+)")
PASS_THRESHOLD = 0.99  # upstream's own threshold, see ENVIRONMENT.md §2.4


# --------------------------------------------------------------------------- utils

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(cmd: list[str], *, cwd: Path, dry: bool, timeout: int | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    printable = " ".join(cmd)
    print(f"  $ {printable}", flush=True)
    if dry:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=check
    )


def capture(cmd: list[str], cwd: Path | None = None) -> str | None:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return None


# ------------------------------------------------------------------- provenance

def host_spec() -> dict:
    spec = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "hostname": platform.node(),
    }
    try:
        spec["mem_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        spec["mem_bytes"] = None
    spec["kernel"] = capture(["uname", "-r"])
    spec["kvm_present"] = Path("/dev/kvm").exists()
    return spec


def image_digest(image: str) -> str | None:
    """RepoDigest of the local image — the only unambiguous identifier of the env."""
    out = capture(["docker", "image", "inspect", image, "--format",
                   "{{index .RepoDigests 0}}"])
    if out and "@sha256:" in out:
        return out.split("@sha256:")[-1]
    # Fall back to the local config id if the image was built rather than pulled.
    return capture(["docker", "image", "inspect", image, "--format", "{{.Id}}"])


def taskset_commit(vendor: Path) -> dict:
    return {
        "commit": capture(["git", "rev-parse", "HEAD"], cwd=vendor),
        "dirty": bool(capture(["git", "status", "--porcelain"], cwd=vendor)),
    }


# ----------------------------------------------------------------- result parsing

def parse_result_txt(path: Path) -> tuple[float | None, str | None]:
    """Mirror mobile_world.runtime.client.parse_result_file."""
    if not path.exists():
        return None, None
    lines = path.read_text(errors="replace").splitlines()
    score = None
    if lines:
        m = SCORE_RE.search(lines[0])
        if m:
            try:
                score = float(m.group(1))
            except ValueError:
                score = None
    reason = lines[1].strip() if len(lines) > 1 else None
    if reason and reason.startswith("reason:"):
        reason = reason[len("reason:"):].strip()
    return score, reason


def summarize_task_dir(task_dir: Path) -> dict:
    """Pull everything the upstream logger left behind for one task run."""
    out = {
        "steps": None,
        "n_screenshots": None,
        "total_tokens": None,
        # Upstream retries silently and renames the old attempt to *_backup_*.
        # Counting these is the only way to see absorbed environment failures.
        "absorbed_retries": 0,
    }
    traj = task_dir / "traj.json"
    if traj.exists():
        try:
            data = json.loads(traj.read_text())
            episode = data.get("0") or {}
            out["steps"] = len(episode.get("traj") or [])
            out["total_tokens"] = (episode.get("token_usage") or {}).get("total_tokens")
        except (json.JSONDecodeError, AttributeError):
            pass
    shots = task_dir / "screenshots"
    if shots.is_dir():
        out["n_screenshots"] = len(list(shots.glob("*.png")))
    # Two different retries leave two different traces, at two different
    # levels, and getting the level wrong makes this silently always zero:
    #
    #   outer  --auto-retry reruns a task that left no result.txt, renaming the
    #          whole previous attempt to <TaskName>_backup_<ts>/ -- a *sibling*
    #          of task_dir, inside the trial directory.
    #   inner  reset_traj() on "Device is not healthy" renames the trajectory
    #          file in place, inside task_dir.
    #
    # Verified against a real run: a trial with 9 outer retries reported 0
    # until this was fixed. ENVIRONMENT.md §2.2 and §9.
    outer = [d for d in task_dir.parent.glob(f"{task_dir.name}_backup_*")
             if d.is_dir()]
    inner = list(task_dir.glob("traj_backup_*.json"))
    out["absorbed_retries"] = len(outer) + len(inner)
    out["absorbed_retries_outer"] = len(outer)
    out["absorbed_retries_inner"] = len(inner)
    return out


# ------------------------------------------------------------------ env lifecycle

def wait_for_backend(port: int, timeout_s: int, dry: bool) -> bool:
    if dry:
        return True
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(5)
    return False


ENV_CONTAINER_PREFIX = "mobile_world_env_"


def sweep_env_containers(dry: bool) -> list[str]:
    """Remove every env container by name prefix, whatever image tag it uses.

    Deliberately goes to docker directly rather than through `mw`: the point is
    to be independent of the image filter that makes `mw env rm --all` a no-op
    here. Returns the names it removed.
    """
    if dry:
        return []
    out = capture(["docker", "ps", "-aq", "--filter",
                   f"name={ENV_CONTAINER_PREFIX}"]) or ""
    ids = [i for i in out.split() if i]
    if not ids:
        return []
    names = (capture(["docker", "inspect", "--format", "{{.Name}}"] + ids) or "")
    removed = [n.lstrip("/") for n in names.split() if n]
    # -v as well: each container carries an emulator disk image, and leaking
    # those fills the host long before the container count does.
    capture(["docker", "rm", "-f", "-v"] + ids)
    return removed


def env_topology(port: int) -> tuple[list[str], list[str]]:
    """(all env containers running, those publishing `port` on the host)."""
    out = capture(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"]) or ""
    running, on_port = [], []
    for line in out.splitlines():
        name, _, ports = line.partition("\t")
        if not name.startswith(ENV_CONTAINER_PREFIX):
            continue
        running.append(name)
        if f":{port}->" in ports:
            on_port.append(name)
    return running, on_port


def check_env_topology(cfg: argparse.Namespace) -> str | None:
    """Confirm the container we are about to talk to is the one just created.

    `mw env rm --all` can fail without saying so, and `mw env run --count 1`
    then allocates the *next* free index and host port rather than reusing the
    one we point --aw-host at. The result is an evaluation running against a
    surviving container from an earlier round, which is exactly the
    cross-episode state the protocol pays for a fresh container to avoid, and
    nothing anywhere reports it: the health check passes, because the stale
    container is perfectly healthy.

    Returns an error message, or None if the topology is what it should be.
    """
    running, on_port = env_topology(cfg.backend_port)
    if not running:
        return "no mobile_world_env_* container is running after `mw env run`"
    if len(running) > 1:
        return (f"{len(running)} env containers are running ({', '.join(sorted(running))}); "
                f"`mw env rm --all` did not remove the previous one. The eval "
                f"talks to whichever holds host port {cfg.backend_port}, which "
                f"is not necessarily the one just created.")
    if not on_port:
        return (f"{running[0]} is running but does not publish host port "
                f"{cfg.backend_port}, which is where --aw-host points")
    return None


def recycle_env(cfg: argparse.Namespace, vendor: Path) -> bool:
    """Destroy every container and bring up exactly one fresh one.

    Recreating rather than reusing is deliberate: the self-hosted Mattermost /
    Mastodon backends accumulate state, and upstream has already had to fix
    cross-episode state leakage once (commit 5adc031). How often this happens is
    controlled by --recycle; see its help text for the wall-clock trade-off.
    """
    run(["sudo", uv_bin(), "run", "mw", "env", "rm", "--all"],
        cwd=vendor, dry=cfg.dry_run, check=False, timeout=600)
    # `mw env rm --all` filters containers by image *substring*, defaulting to
    # DEFAULT_IMAGE = "ghcr.io/tongyi-mai/mobile_world:latest"
    # (runtime/utils/models.py:28, api/env.py:507), and `mw env rm` exposes no
    # flag to override it. A container started from any other tag therefore
    # never matches: the command reports "No containers to destroy" and exits 0
    # while the containers keep running. Pinning the image to v1.4 -- which this
    # audit does deliberately -- silently disables upstream's own cleanup.
    #
    # Without this sweep every round leaves its container behind, `mw env run`
    # allocates the next free port, and the evaluation keeps talking to the
    # *first* container ever created: one container shared by every trial,
    # accumulating exactly the cross-episode state the fresh-container protocol
    # exists to prevent. Nothing reports it; the stale container is healthy.
    swept = sweep_env_containers(cfg.dry_run)
    if swept:
        print(f"  swept {len(swept)} container(s) upstream's `env rm` left "
              f"behind: {', '.join(sorted(swept))}")
    run(["sudo", uv_bin(), "run", "mw", "env", "run",
         "--count", "1",
         "--image", cfg.image,
         "--backend-start-port", str(cfg.backend_port),
         "--launch-interval", str(cfg.launch_interval)],
        cwd=vendor, dry=cfg.dry_run, check=False, timeout=1800)
    ok = wait_for_backend(cfg.backend_port, cfg.boot_timeout, cfg.dry_run)
    if not ok:
        print(f"  !! backend on :{cfg.backend_port} never became healthy", file=sys.stderr)
        return False
    if not cfg.dry_run:
        problem = check_env_topology(cfg)
        if problem:
            print(f"\n  !! environment is not in the state the protocol "
                  f"requires:\n     {problem}\n"
                  f"     Refusing to continue -- results from here would "
                  f"silently violate\n     the one-fresh-container-per-round "
                  f"invariant. Clean up with:\n"
                  f"       docker rm -f $(docker ps -aq "
                  f"--filter name={ENV_CONTAINER_PREFIX})\n"
                  f"     then rerun; completed trials are skipped on resume.",
                  file=sys.stderr)
            raise SystemExit(4)
    return True


# ------------------------------------------------------------------------ trial

_UV_OVERRIDE = None


def uv_bin() -> str:
    """Absolute path to uv.

    sudo replaces PATH with secure_path, which does not include ~/.local/bin
    where the uv installer puts it, so a bare `sudo uv` is not found.
    """
    if _UV_OVERRIDE:
        return _UV_OVERRIDE
    found = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    if not os.path.exists(found):
        raise FileNotFoundError(
            "uv not found; run scripts/provision/bootstrap.sh first")
    return found


def eval_cmd(cfg: argparse.Namespace, task: str, log_root: Path,
             agent_env: dict[str, str]) -> list[str]:
    # `sudo` resets the environment, so the MW_* settings would never reach the
    # agent. Going through `env` works whatever the sudoers env policy is,
    # unlike `sudo -E`.
    cmd = ["sudo"]
    if agent_env:
        cmd += ["env"] + [f"{k}={v}" for k, v in sorted(agent_env.items())]
    cmd += [
        uv_bin(), "run", "mw", "eval",
        "--task", task,
        "--agent-type", cfg.agent_type,
        "--log-file-root", str(log_root),
        "--max-round", str(cfg.max_round),
        "--step-wait-time", str(cfg.step_wait_time),
        "--max-concurrency", "1",
        "--auto-retry", str(cfg.auto_retry),
        "--aw-host", f"http://127.0.0.1:{cfg.backend_port}",
    ]
    if cfg.model_name:
        cmd += ["--model-name", cfg.model_name]
    if cfg.llm_base_url:
        cmd += ["--llm-base-url", cfg.llm_base_url]
    if cfg.scale_factor is not None:
        cmd += ["--scale-factor", str(cfg.scale_factor)]
    if cfg.enable_user_interaction:
        cmd.append("--enable-user-interaction")
    if cfg.enable_mcp:
        cmd.append("--enable-mcp")
    return cmd


def load_done(path: Path) -> set[tuple[str, str, int]]:
    """(arm, task_id, trial_index) already recorded — used for resume."""
    done: set[tuple[str, str, int]] = set()
    if not path.exists():
        return done
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("outcome") == "orchestration_error" and not r.get("keep"):
                continue  # a crashed trial is retried on resume
            done.add((r["arm"], r["task_id"], r["trial_index"]))
    return done


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True,
                    help="File with one task name per line, or a comma-separated list")
    ap.add_argument("--trials", type=int, required=True, help="Repetitions per task")
    ap.add_argument("--arm", required=True, help="Arm label, e.g. A_temp0 / B_default")

    ap.add_argument("--agent-type", required=True,
                    help="Registered agent name, or a path to a .py file (see ENVIRONMENT.md §3.3)")
    ap.add_argument("--model-name", default=None)
    ap.add_argument("--llm-base-url", default=None)
    ap.add_argument("--max-round", type=int, default=50)
    ap.add_argument("--step-wait-time", type=float, default=3.0)
    ap.add_argument("--scale-factor", type=int, default=None)
    ap.add_argument("--enable-user-interaction", action="store_true")
    ap.add_argument("--enable-mcp", action="store_true")
    ap.add_argument("--auto-retry", type=int, default=10,
                    help="Passed straight through to mw eval. Keep at the upstream "
                         "default (10) to measure what the leaderboard setup measures; "
                         "set 0 to see the raw failure rate.")
    # Consumed by agents/pinned_e2e_agent.py through the environment, and
    # recorded on every row so a result is never ambiguous about how it was made.
    ap.add_argument("--or-provider", default=None,
                    help="OpenRouter provider slug to pin, e.g. deepinfra. Without "
                         "this the router may serve different requests from "
                         "different providers at different quantizations, and that "
                         "variance would be indistinguishable from the environment "
                         "variance we are trying to measure.")
    ap.add_argument("--or-quant", default=None, help="Quantization to pin, e.g. fp4")
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--history-n-images", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None,
                    help="Only some providers honour it; check the endpoints API.")
    ap.add_argument("--model-params", default="{}",
                    help="Any further sampling params, recorded verbatim alongside "
                         "the flags above.")

    ap.add_argument("--image", default="ghcr.io/tongyi-mai/mobile_world:v1.4",
                    help="`latest` is the 2025-12-24 v1.0 build, not the newest; "
                         "v1.2+ fixes iptables-NAT failures that upstream says cause "
                         "silent eval failures. See scripts/provision/README.md.")
    ap.add_argument("--backend-port", type=int, default=6800)
    ap.add_argument("--launch-interval", type=int, default=10)
    ap.add_argument("--boot-timeout", type=int, default=900)
    ap.add_argument("--task-timeout", type=int, default=3600)
    ap.add_argument("--recycle", choices=["trial", "task", "never"], default="trial",
                    help="When to destroy and recreate the container. "
                         "'trial' (default): once per repetition round, i.e. all tasks of "
                         "trial N share one fresh container — this is how the leaderboard "
                         "runs are produced (`--task ALL` against a container pool) and it "
                         "keeps boot overhead proportional to trials, not to trials x tasks. "
                         "'task': a fresh container for every single run — removes "
                         "cross-task state leakage entirely, but boot time then dominates "
                         "the wall clock. 'never': one container for everything; only for "
                         "deliberately measuring how much state leakage is worth.")

    ap.add_argument("--vendor", default=str(DEFAULT_VENDOR))
    ap.add_argument("--runs-jsonl", default=str(RUNS_JSONL))
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the command sequence without touching anything")
    ap.add_argument("--allow-missing-digest", action="store_true",
                    help="Proceed even if the image digest cannot be read. Only "
                         "for a deliberately unpinned environment; a run without "
                         "it is not reproducible.")
    cfg = ap.parse_args()

    if cfg.agent_type.endswith(".py"):
        # mw eval runs with cwd=vendor/MobileWorld, so a repo-relative agent
        # path would not resolve there.
        cfg.agent_type = str(Path(cfg.agent_type).resolve())

    vendor = Path(cfg.vendor).resolve()
    if not (vendor / "pyproject.toml").exists():
        print(f"vendor checkout not found at {vendor}", file=sys.stderr)
        return 2

    tp = Path(cfg.tasks)
    if tp.exists():
        tasks = [ln.strip() for ln in tp.read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")]
    else:
        tasks = [t.strip() for t in cfg.tasks.split(",") if t.strip()]
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 2

    try:
        model_params = json.loads(cfg.model_params)
    except json.JSONDecodeError as e:
        print(f"--model-params is not valid JSON: {e}", file=sys.stderr)
        return 2

    agent_env = {
        "MW_OR_PROVIDER": cfg.or_provider,
        "MW_OR_QUANT": cfg.or_quant,
        "MW_TEMPERATURE": cfg.temperature,
        "MW_TOP_P": cfg.top_p,
        "MW_MAX_TOKENS": cfg.max_tokens,
        "MW_HISTORY_N_IMAGES": cfg.history_n_images,
        "MW_SEED": cfg.seed,
    }
    agent_env = {k: str(v) for k, v in agent_env.items() if v is not None}
    for k, v in agent_env.items():
        os.environ[k] = v
    model_params = {**{k.lower()[3:]: v for k, v in agent_env.items()}, **model_params}
    if agent_env and not cfg.agent_type.endswith(".py"):
        print("warning: --or-provider/--temperature/... are read by "
              "agents/pinned_e2e_agent.py; a registered agent-type ignores them.",
              file=sys.stderr)

    if not cfg.dry_run and not Path("/dev/kvm").exists():
        print("/dev/kvm is missing — MobileWorld cannot run on this host. "
              "See ENVIRONMENT.md §4. Use --dry-run to inspect the plan.", file=sys.stderr)
        return 3
    if not cfg.dry_run and not shutil.which("docker"):
        print("docker not found on PATH.", file=sys.stderr)
        return 3

    runs_jsonl = Path(cfg.runs_jsonl)
    runs_dir = Path(cfg.runs_dir)
    done = load_done(runs_jsonl)

    host = host_spec()
    ts_commit = taskset_commit(vendor)
    digest = None if cfg.dry_run else image_digest(cfg.image)

    # The digest is the only unambiguous identifier of the environment, so a
    # run that cannot record it is not reproducible and every row it writes is
    # missing the field the report has to cite. Refuse rather than discover it
    # 27 hours later.
    if digest is None and not cfg.dry_run and not cfg.allow_missing_digest:
        print(
            "\ncannot read the image digest for "
            f"{cfg.image}; refusing to run.\n"
            "`docker image inspect` failed, which almost always means this "
            "process cannot reach the docker socket.\n"
            "  - check with:  docker image inspect " + cfg.image + "\n"
            "  - if that works in a plain shell but not here, you are probably "
            "inside a tmux server\n"
            "    that was started before your account was added to the docker "
            "group. New sessions\n"
            "    inherit the server's groups, not your current ones. Fix it "
            "with `tmux kill-server`\n"
            "    and start a fresh one.\n"
            "  - override with --allow-missing-digest only if you mean it.",
            file=sys.stderr)
        return 3

    total = len(tasks) * cfg.trials
    print(f"arm={cfg.arm}  tasks={len(tasks)}  trials={cfg.trials}  total={total}")
    print(f"already recorded: {len([d for d in done if d[0] == cfg.arm])}")
    print(f"image={cfg.image} digest={digest}  taskset={ts_commit}")

    n = 0
    for trial in range(1, cfg.trials + 1):
        for task in tasks:
            n += 1
            key = (cfg.arm, task, trial)
            if key in done:
                print(f"[{n}/{total}] skip {task} trial {trial} (done)")
                continue

            log_root = runs_dir / cfg.arm / task / f"trial_{trial:03d}"
            if not cfg.dry_run:
                if log_root.exists():
                    # A half-finished directory would make mw eval think the task
                    # is already complete. Move it aside rather than delete it.
                    log_root.rename(log_root.with_name(
                        f"{log_root.name}_incomplete_{int(time.time())}"))
                log_root.mkdir(parents=True, exist_ok=True)

            print(f"[{n}/{total}] {cfg.arm} :: {task} :: trial {trial}")

            row = {
                "run_id": uuid.uuid4().hex,
                "task_id": task,
                "trial_index": trial,
                "arm": cfg.arm,
                "agent_type": cfg.agent_type,
                "model": cfg.model_name,
                "model_params": model_params,
                "agent_env": agent_env,
                "llm_base_url": cfg.llm_base_url,
                "max_round": cfg.max_round,
                "step_wait_time": cfg.step_wait_time,
                "auto_retry": cfg.auto_retry,
                "enable_user_interaction": cfg.enable_user_interaction,
                "enable_mcp": cfg.enable_mcp,
                "image": cfg.image,
                "image_digest": digest,
                "taskset_commit": ts_commit["commit"],
                "taskset_dirty": ts_commit["dirty"],
                "host_spec": host,
                "recycle_policy": cfg.recycle,
                "traj_path": str(log_root / task),
            }

            first_of_trial = task == tasks[0]
            should_recycle = (
                cfg.recycle == "task"
                or (cfg.recycle == "trial" and first_of_trial)
                or n == 1
            )
            env_ok = True
            row["env_recycled"] = should_recycle
            if should_recycle:
                env_ok = recycle_env(cfg, vendor)

            row["started_at"] = now_iso()
            t0 = time.monotonic()

            if not env_ok:
                row["outcome"] = "env_unhealthy"
                row["score"] = None
                row["pass"] = None
            else:
                try:
                    proc = run(eval_cmd(cfg, task, log_root, agent_env), cwd=vendor,
                               dry=cfg.dry_run, timeout=cfg.task_timeout, check=False)
                    row["eval_returncode"] = proc.returncode
                    if proc.stderr:
                        row["eval_stderr_tail"] = proc.stderr[-2000:]
                except subprocess.TimeoutExpired:
                    row["outcome"] = "timeout"
                    row["eval_returncode"] = None

            row["ended_at"] = now_iso()
            row["duration_s"] = round(time.monotonic() - t0, 2)

            if "outcome" not in row:
                task_dir = log_root / task
                score, reason = parse_result_txt(task_dir / "result.txt")
                row.update(summarize_task_dir(task_dir))
                row["score"] = score
                row["reason"] = reason
                row["pass"] = (score is not None and score > PASS_THRESHOLD)
                row["outcome"] = "scored" if score is not None else "no_result"

            if cfg.dry_run:
                print(f"  -> would record: {row['outcome'] if 'outcome' in row else '?'}")
            else:
                append_row(runs_jsonl, row)
                print(f"  -> {row['outcome']} score={row.get('score')} "
                      f"steps={row.get('steps')} {row['duration_s']}s "
                      f"absorbed_retries={row.get('absorbed_retries')}")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
