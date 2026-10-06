"""Read-only frozen application binding for reference-controller validation.

The controller and ownership wrappers are external TEST tooling. This gate
establishes application build-input equality, not a manual README rehearsal or
bit-identical wrapper images. Git HEAD must contain the parent's checkpoint;
uncommitted source copies do not satisfy the gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

LEAN_SOURCE_REF = "c05128bf581ff8c57f5dff9e216c00639e430416"
BASELINE_SOURCE_REF = "6f682ee17c8affa988deffa44c56f2e39e28e462"
SOURCE_ENV = "FINKI_HUB_LEAN_SOURCE_REF"
APPLICATION_INPUTS = (
    "hub",
    "lab",
    "web",
    "package.json",
    "package-lock.json",
    ".dockerignore",
    "compose.yaml",
    "scripts/update.sh",
    ".env.example",
    "README.md",
)


class SourceBindingFailure(RuntimeError):
    """Fixed-vocabulary errors, never Git output or model/credential values."""


def git(workspace: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SourceBindingFailure("source-git-operation-failed") from None
    if result.returncode:
        raise SourceBindingFailure("source-git-operation-failed")
    return result.stdout


def exact_commit(workspace: Path, reference: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", reference):
        raise SourceBindingFailure("source-reference-not-full-commit")
    if (
        git(workspace, "rev-parse", "--verify", reference + "^{commit}")
        .decode()
        .strip()
        != reference
    ):
        raise SourceBindingFailure("source-reference-not-exact-commit")
    return reference


def input_objects(workspace: Path, reference: str) -> dict[str, str]:
    # Tree object IDs cover EVERY nested file, mode, and sub-tree, not a selection
    # of Dockerfile COPY paths. Root blobs include an existing .dockerignore.
    result = {}
    for entry in git(
        workspace, "ls-tree", "-z", reference, "--", *APPLICATION_INPUTS
    ).split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            result[name.decode("utf-8")] = metadata.decode("ascii")
    return result


def bind_source(workspace: Path, lean_ref: str | None) -> dict[str, Any]:
    if lean_ref != LEAN_SOURCE_REF:
        raise SourceBindingFailure("approved-lean-source-reference-required")
    lean = exact_commit(workspace, lean_ref)
    baseline = exact_commit(workspace, BASELINE_SOURCE_REF)
    validation = exact_commit(
        workspace, git(workspace, "rev-parse", "HEAD").decode().strip()
    )
    lean_objects = input_objects(workspace, lean)
    validation_objects = input_objects(workspace, validation)
    if set(lean_objects) != set(APPLICATION_INPUTS):
        raise SourceBindingFailure("required-lean-source-input-missing")
    if validation_objects != lean_objects:
        raise SourceBindingFailure("application-source-binding-mismatch")
    if git(workspace, "diff", "--name-only", "HEAD", "--", *APPLICATION_INPUTS).strip():
        raise SourceBindingFailure("application-worktree-not-frozen")
    if git(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        *APPLICATION_INPUTS,
    ).strip():
        raise SourceBindingFailure("untracked-application-input")
    baseline_objects = input_objects(workspace, baseline)
    return {
        "method": "preserved-reference-controller-two-cycle",
        "manual_readme_procedure": "not-rehearsed",
        "validation": validation,
        "lean": lean,
        "baseline": baseline,
        "application_input_objects": lean_objects,
        "baseline_input_objects": baseline_objects,
        "baseline_root_dockerignore": baseline_objects.get(
            ".dockerignore", "identity-absence"
        ),
        "build_context_inputs": {
            "hub": lean_objects["hub"],
            "lab": lean_objects["lab"],
            "web_root_copy_inputs": {
                name: lean_objects[name]
                for name in (
                    "web",
                    "package.json",
                    "package-lock.json",
                    ".dockerignore",
                )
            },
        },
        "instrumentation": [
            "Hub ownership wrapper adds run labels; not bit-identical shipping image",
            "baseline Hub/Lab version labels added only after installed-package proof",
            "image-only Lab profile network attachment for reference-controller config",
            "reference-controller private config/culler fixture; not README manual overlays",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--lean-ref", default=os.environ.get(SOURCE_ENV))
    args = parser.parse_args()
    try:
        evidence = bind_source(args.workspace, args.lean_ref)
    except SourceBindingFailure as exc:
        print(
            json.dumps(
                {
                    "stage": "source-binding",
                    "classification": str(exc),
                    "status": "fail",
                    "runtime": "not-run",
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "stage": "source-binding",
                "status": "pass",
                "runtime": "not-run",
                **evidence,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
