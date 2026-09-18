# Research PM phase 1 implementation record

Goal: add a separately runnable, auditable research PM to the existing fork.
Base: a53b13c7022bb1352dc1ca994d07ade3cd3bd62e.
Spec: docs/superpowers/specs/2026-09-18-research-pm-design.md.

- [x] pm_types.py: validated settings, JSON handling, URL identity, evidence gates.
- [x] pm_store.py: SQLite state, sources, claims, conflict queries, lexical retrieval, exports and worker lock.
- [x] pm_engine.py: five-role sequential orchestration, fixed task criteria, persisted budgets, retry and resume.
- [x] pm_io.py / pm_search_worker.py: bounded Ollama streaming and killable upstream search adapters.
- [x] pm_gui.py / Windows launchers: Korean control panel and isolated .pm-venv installation.
- [x] tests: reproduce failures before fixes; verify quotes, conflicts, budgets, recovery and transport.
- [x] Compile checks, Python 3.11 grammar checks and Linux/Xvfb GUI inspection.
- [ ] Actual Windows + installed Qwen + live search validation by the user.
- [ ] Field accuracy and throughput measurement; extended-duration operational testing.

Delivery must preserve upstream files and main. A feature branch is for evaluation,
not automatic approval to merge or a guarantee of research quality.
