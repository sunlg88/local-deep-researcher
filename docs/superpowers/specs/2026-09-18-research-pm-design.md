# Research PM phase 1 design

User-approved direction: extend the fork with a source-grounded PM, task queue,
evidence storage and criticism, preserving main. Existing GUI requirement applies.
Five-role orchestration is an implementation goal, not five-person performance proof.

The independent desktop PM lives in the existing package and reuses upstream search
adapters. SQLite is the source of truth. A single worker advances plan, selection,
research, extraction, criticism and per-task writing stages. A separate reporting
thread produces snapshots. The original graph remains available unchanged.

Acceptance gates are fixed after planning: every criterion needs a critic check,
real known evidence, permitted source groups and distinct document bodies. Exact
quotes are mechanically checked; interpretation remains fallible. Conflicts retain
both values. Budgets and retries are persisted, and blocked work is not called done.

Configuration and transport boundaries are validated. The model never runs shell
commands. HTML source reading rejects local destinations and unsupported binary
formats. The GUI supports start, pause, stop, project selection, resume and export.

This phase excludes PDF/OCR, XLSX, vector RAG, unattended OS services and remote access.
See docs/PHASE1_VALIDATION.md for tests, deployment checks and remaining limitations.
