# PR17 static correction publication

- PR: https://github.com/finki-hub/shell/pull/17
- Branch: `jupyterhub-6.0.1-combined`
- Published atop: `ad16a7ae4b043b38c11399bb8680eb23dbc53bba`
- Scope: bounded maintenance/integration harness corrections and contract tests;
  no workflow, runtime application, Hub, Lab, or production configuration edits.
- Runtime gate: `jupyterhub-migration-runtime` was absent before publication.
  The existing integration job remains gated by explicit dispatch approval or
  that PR label; no label, dispatch, merge, or production action was performed.

## Validation evidence

Parent-reported pre-publication static evidence: integration contracts 42
passed; root tests 34 passed with one POSIX-only skip; Linux-targeted mypy and
Ruff passed for the maintenance/integration scope; Hub Ruff format, mypy, and
rollout tests passed. These are reported parent results, not rerun here.

The existing static workflow is expected to run for the pushed commit. Its
post-push run ID and initial status are reported in the publication response;
queued/in-progress is not a passing result. The privileged Linux Docker/XFS/Hub
runtime suite remains unrun and unauthorized by this publication.

Publication commit SHA and remote head are reported in the publication
response after push. Human repository attribution is preserved; no AI/tool
attribution is added.
