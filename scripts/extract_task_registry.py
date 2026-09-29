#!/usr/bin/env python3
"""Extract the MobileWorld task inventory statically, without importing anything.

Upstream builds its registry by importing every file under
`tasks/definitions/` and instantiating each BaseTask subclass
(`tasks/registry.py::_register_tasks_from_module`), which needs the whole
runtime. We only need names, tags and a few structural facts, so we parse the
ASTs instead. That works on any machine.

Task name == class name (registry.py:82).

Output: data/tasks.json
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

# Tag semantics from runtime/client.py::get_suite_task_list — a task with
# neither of these tags is a "GUI-only" task, which is the default suite.
MCP_TAG = "agent-mcp"
UI_TAG = "agent-user-interaction"

RANDOM_CALLS = {"choice", "sample", "randint", "shuffle", "uniform", "random", "randrange"}


def literal(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def class_attr(cls: ast.ClassDef, name: str):
    for stmt in cls.body:
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                return literal(value)
    return None


def uses_unseeded_random(cls: ast.ClassDef) -> list[str]:
    """Return the random.* calls made anywhere inside this class body.

    Nothing in the codebase ever calls random.seed(), so any of these means the
    task's initial state differs from run to run by construction.
    """
    found = []
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "random"
            and node.func.attr in RANDOM_CALLS
        ):
            found.append(f"random.{node.func.attr}")
    return sorted(set(found))


def base_names(cls: ast.ClassDef) -> list[str]:
    out = []
    for b in cls.bases:
        if isinstance(b, ast.Name):
            out.append(b.id)
        elif isinstance(b, ast.Attribute):
            out.append(b.attr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--defs", default="vendor/MobileWorld/src/mobile_world/tasks/definitions")
    ap.add_argument("--out", default="data/tasks.json")
    args = ap.parse_args()

    defs = Path(args.defs)
    tasks: dict[str, dict] = {}

    # First pass: every class in the tree, so we can resolve one level of
    # subclassing between task files (a few tasks subclass a sibling task).
    all_classes: dict[str, ast.ClassDef] = {}
    files: dict[str, Path] = {}
    for py in sorted(defs.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                all_classes[node.name] = node
                files[node.name] = py

    def is_task(name: str, depth: int = 0) -> bool:
        if depth > 6 or name not in all_classes:
            return name == "BaseTask"
        for b in base_names(all_classes[name]):
            if b == "BaseTask" or is_task(b, depth + 1):
                return True
        return False

    for name, cls in all_classes.items():
        if name == "BaseTask" or not is_task(name):
            continue
        py = files[name]
        tags = class_attr(cls, "task_tags")
        tags = sorted(tags) if isinstance(tags, (set, list, tuple)) else None
        goal = class_attr(cls, "goal")
        apps = class_attr(cls, "app_names")
        rnd = uses_unseeded_random(cls)

        suite = None
        if tags is not None:
            if MCP_TAG in tags:
                suite = "mcp"
            elif UI_TAG in tags:
                suite = "user_interaction"
            else:
                suite = "gui_only"

        tasks[name] = {
            "module": str(py.relative_to(defs.parent.parent.parent.parent)),
            "group": py.parent.name,
            "bases": base_names(cls),
            "task_tags": tags,
            "suite": suite,
            "apps": sorted(apps) if isinstance(apps, (set, list, tuple)) else apps,
            "goal": goal if isinstance(goal, str) else None,
            "unseeded_random_calls": rnd,
            "randomized_setup": bool(rnd),
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tasks, ensure_ascii=False, indent=2))

    from collections import Counter
    print(f"tasks parsed: {len(tasks)}   -> {out}")
    print("by suite:", dict(Counter(t["suite"] for t in tasks.values())))
    print("by group:", dict(Counter(t["group"] for t in tasks.values())))
    rand = [k for k, v in tasks.items() if v["randomized_setup"]]
    print(f"tasks whose setup calls unseeded random.*: {len(rand)}")
    for k in sorted(rand):
        print("   ", k, tasks[k]["unseeded_random_calls"])


if __name__ == "__main__":
    main()
