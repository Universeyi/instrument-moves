#!/usr/bin/env python3
"""Make data/runs.jsonl publishable without changing what it says.

run_matrix.py records absolute paths (it has to: `mw eval` runs with a
different cwd) and the host's name. Neither is a result, both identify the
machine somebody ran this on, and this repository is meant to be published as
it stands. This rewrites those two things and touches nothing else.

The original is never modified. Diff the two files before publishing -- the
point is that the difference is boring.

    python3 scripts/scrub_runs.py
    python3 scripts/scrub_runs.py --in data/runs.jsonl --out data/runs.public.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATH_FIELDS = ("traj_path", "agent_type")

# /home/<user> and /Users/<user>, the two shapes this project produces.
HOME_RE = re.compile(r"/(?:home|Users)/[A-Za-z_][A-Za-z0-9_.-]*")


# Directories that exist at the repository root. A recorded path is anchored on
# whichever of these it contains, rather than on this machine's layout.
_ANCHORS = ("runs", "data", "agents", "scripts", "report", "vendor")


def repo_relative(value):
    """Turn a path recorded on another machine into a repository-relative one.

    run_matrix.py records absolute paths, and the machine that runs the matrix
    is usually not the machine that analyses it -- the evaluation needs a Linux
    host with /dev/kvm, the analysis runs anywhere. So `relative_to(ROOT)`
    fails on exactly the normal workflow, and falling back to the basename
    throws away the arm and the trial, which is most of what makes the path
    useful. Anchor on the first known repository directory instead.
    """
    if not isinstance(value, str) or "/" not in value:
        return value
    parts = [p for p in value.split("/") if p]
    for i, part in enumerate(parts):
        if part in _ANCHORS:
            return "/".join(parts[i:])
    return "/".join(parts[-3:]) if len(parts) > 3 else "/".join(parts)


def relativise(value, root: Path):
    if not isinstance(value, str) or not value.startswith("/"):
        return value
    return repo_relative(value)


def scrub(row: dict, root: Path) -> tuple[dict, list[str]]:
    changed = []
    for field in PATH_FIELDS:
        if field in row:
            new = relativise(row[field], root)
            if new != row[field]:
                row[field] = new
                changed.append(field)

    host = row.get("host_spec")
    if isinstance(host, dict) and "hostname" in host:
        host.pop("hostname")
        changed.append("host_spec.hostname")

    # eval_stderr_tail is raw subprocess output. Deleting it would destroy
    # evidence, so it is kept -- but a Python traceback quotes absolute paths,
    # and on this project those contain a username derived from the operator's
    # email address. Rewriting the home prefix to `~` removes the only
    # identifying part while leaving the message, the file, and the line intact.
    tail = row.get("eval_stderr_tail")
    if isinstance(tail, str) and tail:
        new_tail = HOME_RE.sub("~", tail)
        if new_tail != tail:
            row["eval_stderr_tail"] = new_tail
            changed.append("eval_stderr_tail:home-path")

    return row, changed


# Upstream logs a masked key on the LLM error path
# (agents/implementations/general_e2e_agent.py, "Error fetching response from
# agent: {}, {}, {}"), so thread_*.log under runs/ carries sk-o...XXXX. The
# leading characters are the same for every key of that provider, so what
# actually leaks is the last four -- low risk, and still not something to put
# in a public repository.
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9._-]*")


def scan_artifacts(root: Path) -> dict[Path, int]:
    """Files under `root` containing anything that looks like key material."""
    hits: dict[Path, int] = {}
    if not root.exists():
        return hits
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix in {".png", ".jpg", ".jpeg"}:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = len(KEY_PATTERN.findall(text))
        if n:
            hits[path] = n
    return hits


def redact_artifacts(hits: dict[Path, int]) -> int:
    for path in hits:
        text = path.read_text(errors="replace")
        path.write_text(KEY_PATTERN.sub("sk-REDACTED", text))
    return len(hits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", default=str(ROOT / "data" / "runs.jsonl"))
    ap.add_argument("--out", dest="dst", default=str(ROOT / "data" / "runs.public.jsonl"))
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--artifacts", default=str(ROOT / "runs"),
                    help="Directory of raw per-trial artifacts to scan for key "
                         "material before publishing. Set to '' to skip.")
    ap.add_argument("--redact-artifacts", action="store_true",
                    help="Rewrite the matches found by the scan. Off by "
                         "default: these files are evidence, and editing them "
                         "is a decision, not a default.")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    if not src.exists():
        print(f"{src} does not exist")
        return 2
    if dst.resolve() == src.resolve():
        print("refusing to overwrite the original")
        return 2

    root = Path(args.root).resolve()
    out_lines, touched, with_stderr = [], set(), 0
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row, changed = scrub(row, root)
        touched.update(changed)
        if row.get("eval_stderr_tail"):
            with_stderr += 1
        out_lines.append(json.dumps(row, ensure_ascii=False))

    dst.write_text("\n".join(out_lines) + "\n")
    print(f"wrote {dst}  ({len(out_lines)} rows)")
    print(f"fields rewritten: {', '.join(sorted(touched)) or 'none'}")
    if args.artifacts:
        hits = scan_artifacts(Path(args.artifacts))
        if hits:
            total = sum(hits.values())
            print(f"\n!! {total} key-like string(s) in {len(hits)} file(s) under "
                  f"{args.artifacts}.")
            for path in sorted(hits)[:5]:
                print(f"   {path}")
            if len(hits) > 5:
                print(f"   ... and {len(hits) - 5} more")
            if args.redact_artifacts:
                print(f"   redacted {redact_artifacts(hits)} file(s)")
            else:
                print("   Not published as-is. Re-run with --redact-artifacts, "
                      "or exclude runs/ from what you publish.")
                return 1
        else:
            print(f"artifacts under {args.artifacts}: no key material found")

    if with_stderr:
        print(f"\n{with_stderr} row(s) carry eval_stderr_tail. Home paths in it "
              f"have been rewritten to `~`; everything else is untouched "
              f"captured subprocess output. Read it before publishing:")
        print(f"  python3 -c \"import json;[print(r.get('eval_stderr_tail','')) "
              f"for r in map(json.loads, open('{dst}'))]\" | sort -u | head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
