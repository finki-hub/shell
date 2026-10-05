VERDICT: Publishing the static-only PR17 security correction from base `ff31cf803e6f3099d245f46626c1b5cd82d076a6`; runtime label absent, and CodeQL/Linux CI results are pending—not passing.

# PR17 security correction publication

- PR: https://github.com/finki-hub/shell/pull/17
- Branch: `jupyterhub-6.0.1-combined`
- Base before publication: `ff31cf803e6f3099d245f46626c1b5cd82d076a6`
- Scope: fixture-only proxy-token containment, inspect-absence reconciliation,
  scoped contracts and explanation, plus the pinned `setup-uv` action reference.
- Pre-push PR state: open; `jupyterhub-migration-runtime` label absent.
- Existing runtime gate remains unchanged: integration requires `checks` and
  either explicit manual approval input `APPROVED` or the runtime PR label.

Parent-provided source validation: 49 integration contracts passed; locked Hub
environment Linux-targeted mypy, CI-scoped Ruff, formatting, compilation and
diff checks passed. This publication did not rerun those checks.

The push is expected to queue static PR checks and CodeQL. Run IDs and initial
status are captured in the publication response. A queued or in-progress run is
not a passing result. Parent-owned latest CI/SAST review and runtime-gate decision
remain pending. No privileged runtime suite, label, dispatch, merge, production
access, SSH, or review/comment action was performed. No Docker proof is claimed.

Only explicitly intended source files and this publication artifact are staged.
Local `.gitignore`, `.ignore`, integration remediation memo and all other
ignored/local metadata are excluded and preserved.

## Bounded loop diagnostics follow-up

- Diagnostic source commit: `5088918cb2644e1aa310e57ae91b1c702c71aefc`
  (`test: add sanitized XFS setup failure diagnostics`), pushed to the same PR17
  branch without changing the workflow gate.
- On that exact source SHA, migration `checks` passed in
  [run 37381970039](https://github.com/finki-hub/shell/actions/runs/37381970039).
  CodeQL and all three Analyze jobs passed; branch build, Hub test, lint, and
  typecheck checks also passed. The migration integration job was skipped while
  the runtime label was absent.
- After those gates passed, the explicitly authorized disposable runtime label
  was applied and triggered [attempt 2](https://github.com/finki-hub/shell/actions/runs/37382187949).
  It failed at `xfs-quota-verification` before acceptance cases; the structured
  result had no `failure_context`, so the cause remains unknown. Cleanup was
  reported verified. See `runtime-attempt2.md` for the sanitized evidence.
- The runtime label was removed after the terminal failure. No runtime approval,
  migration success, or production evidence is claimed.
