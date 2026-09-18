# Isolated Collector validation - 2026-09-18

## Executed in the development container

- Full suite: `xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v`.
- Result: **107 tests passed**, no skipped tests in the Xvfb run (Python 3.13, Linux).
- Syntax compilation: `python -m compileall -q src/ollama_deep_researcher` succeeded.
- Tests use synthetic local sources, temporary SQLite databases, a fixture Ollama HTTP server,
  real killable search/PDF child processes, and a real Tk window under Xvfb.
- Baseline before changes: 56 passing tests. New behavior tests were run red before implementation.
- Existing gate tests are retained as opt-in final-filter tests. Collector tests intentionally
  replace the old global-error/discard behavior with retention, review labels and local recovery.

## Covered invariants

1. Separate database and document directories; no default or transitive cross-project reuse.
2. Explicit reference selection, provenance, raw byte preservation and read-only source access.
3. Legacy online SQLite backup, old export archival, ambiguous-source quarantine, ID remapping.
4. Full original preservation even with no extracted evidence, immutable original/text versions.
5. Exact quotes, character/page locations, optional unknown fields and comparison-condition separation.
6. Long-source tail coverage, no repeated same-range extraction, replay after interrupted checkpoints,
   split-child recovery, pause/resume and independent fair task turns.
7. Single-source retention; critic/extractor/writer failure does not erase sources or globally abort.
8. Immutable original topic/instructions in every role; AI criteria do not become storage gates.
9. Source URL/query/error ledgers, bounded retry/cooldown, Unicode URLs and encoding fallback.
10. Text PDF extraction, blank/failed PDF flags, original-byte retention and parser cancellation.
11. Ollama schema transport, incomplete output rejection, private-address checks and cancellation.
12. Self-contained handoff with file hashes, raw originals, failed ranges, source labels and safe CSV cells.
13. Korean GUI uses project-bound Store instances and exposes explicit references, time and advanced controls.

## Not demonstrated by these tests

- Scientific/factual accuracy, relevance precision or recall of any actual local model.
- Live Ollama/Qwen3.5 schema behavior and throughput on an RTX 4060 laptop.
- Windows 10/11 end-to-end installation and OS-specific shutdown/memory behavior.
- Live search-provider reliability, all website encodings/layouts, or independent source provenance.
- Two-hour, eight-hour or twenty-four-hour soak tests on the user's computer.
- OCR, JavaScript page rendering, paywall bypass, complex-table or diagram understanding.
- A full security audit or independent reviewer approval. This run used regression tests and self-review.

## Manual rollout checks

- [ ] Back up the whole existing `pm_data` directory before installing.
- [ ] Close the old PM, install dependencies, launch the new GUI, verify installed Ollama model.
- [ ] Confirm migrated projects are paused and original DB/exports remain in backups.
- [ ] Run two unrelated short projects; verify no unsolicited source reuse.
- [ ] Run a selected-reference project and inspect origin IDs and re-extraction.
- [ ] Stop/restart during source extraction and inspect preserved range history.
- [ ] Review handoff originals, page/character locations, failure labels and manifest hashes.
- [ ] Progress from a short real run to 2h, 8h and 24h only after earlier runs are inspected.

GitHub Actions runs the regression suite on Python 3.11 and retains source/test-log artifacts.
Its result must be checked on the published commit; the local result above is not a claim about CI.
