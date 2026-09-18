# Isolated Collector Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task. Approval is
> already recorded in the recovered conversation; do not repeat the design gate.

**Goal:** Preserve useful, traceable source material in independent projects and
export it for human/ChatGPT review.

**Architecture:** A filesystem workspace routes to independent Store instances.
The store preserves originals, candidate provenance and processing history; a
bounded collector drives collection/review. The existing desktop uses the workspace.

**Tech Stack:** Python >=3.10, SQLite, Tkinter, Ollama HTTP, pypdf.

**Spec:** `docs/superpowers/specs/2026-09-18-isolated-collector-design.md`

## Global Constraints

- Python >=3.10; preserve the Windows launchers and existing source policies.
- No automatic cross-project reuse, OCR or paid API requirement.
- Original data must remain recoverable through migration and model failures.

### Task 1: Independent projects and explicit references

**Files:** create `src/ollama_deep_researcher/pm_projects.py`; extend `pm_store.py`;
create `tests/test_pm_projects.py`.

**Interfaces:** `Workspace(root)`, `create(topic, settings, instructions='',
reference_projects=()) -> str`, `open(pid) -> Store`, `projects() -> list`,
`reference_candidates(pid, query) -> list`, `import_candidate(pid, candidate) -> str`.
`Store` remains a low-level database interface for existing callers.

- [x] Write tests for separate database files, empty default references,
  read-only origins, preserved provenance, invalid IDs and migration.
  The central acceptance assertion is:
  ```python
  a = workspace.create('Project A', settings)
  b = workspace.create('Project B', settings)
  workspace.open(a).add_document('https://example.org/a', 'A', 'Known source text')
  assert workspace.open(b).counts()['documents'] == 0
  assert workspace.reference_candidates(b, 'Known source') == []
  ```
- [x] Run `PYTHONPATH=src python -m unittest discover -s tests -p test_pm_projects.py -v`;
  confirm missing isolation fails before implementation.
- [x] Implement explicit routing, bounded one-hop candidate search and online-backup
  migration. Reject traversal/symlinks and never mutate reference origins.
- [x] Run project tests and the existing suite; commit the independently usable layer.

### Task 2: Originals, generic evidence and self-contained export

**Files:** extend `pm_store.py`, `pm_types.py`, `pm_io.py`; create `pm_documents.py`,
`pm_export.py` and `tests/test_pm_collection.py`.

**Interfaces:** `FetchedDocument`, `decode_document(raw, content_type, charset)`,
`Store.add_document(..., metadata=None, raw=None)`, persistent source/processing
records and `Store.export(pid) -> Path`.

- [x] Test retained originals without evidence, verbatim quote offsets, optional
  fields, different subentities, UTF-8/legacy encodings and PDF page metadata.
  ```python
  store.add_document(url, title, text)
  folder = store.export(pid)
  assert (folder / 'documents').is_dir()
  assert json.loads((folder / 'manifest.json').read_text())['documents']
  ```
- [x] Run the new tests and observe the missing outputs/metadata.
- [x] Implement byte-preserving fetches, bounded paragraph/page chunks, metadata,
  quote validation, conflict candidates and a manifest with verifiable hashes.
- [x] Re-run fixtures, verify exports without access to the source DB, commit.

### Task 3: Resilient collection loop

**Files:** refactor `pm_engine.py`, extend `pm_io.py`/`pm_types.py`; add flow fixtures.

**Interfaces:** retain `Engine.step(pid)`/`run(pid)`; use the store's persistent
work records. New settings select collection vs strict final filtering and a time
budget; all source policies remain immutable during a run.

- [x] Test critic truncation, isolated extraction errors, repeated URLs/queries,
  fair scheduling, retained source-only results and resumable budget/control state.
  ```python
  Engine(store, critic_failure_model, fixture_web).run(pid)
  assert store.counts()['documents'] > 0
  assert store.load(pid)['status'] != 'ERROR'
  ```
- [x] Observe failures before changing the flow.
- [x] Implement bounded task-local recovery, full question propagation, candidate
  relevance validation, chunk history, round-robin work and explicit stop reasons.
- [x] Update old assertions only where the approved collector semantics deliberately
  replace fail-the-whole-run behaviour; retain citation and source-policy checks.
- [x] Run all tests and commit.

### Task 4: Desktop, documentation and delivery

**Files:** `pm_gui.py`, `RESEARCH_PM_KO.md`, dependency/CI declarations and GUI tests.

- [x] Add a display fixture that verifies workspace use, reference selection state
  and visible retained-source counts.
- [x] Run under `xvfb-run -a`; confirm the new controls are missing initially.
- [x] Add explicit references, time/report controls, advanced settings and useful
  source/evidence/error progress without a fictitious completion percentage.
- [x] Document migration, handoff contents, installation and validation limitations.
- [x] Run `xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v`
  and `python -m compileall -q src/ollama_deep_researcher`.
- [x] Publish an atomic tree/commit to the existing development branch, never force
  overwrite concurrent edits. Verify the remote tree and CI before reporting.

## Execution record

Implementation and offline tests completed in this change; see `docs/PHASE2_VALIDATION.md`.
Live Windows/Ollama and long-duration rollout checks remain explicitly unexecuted.
