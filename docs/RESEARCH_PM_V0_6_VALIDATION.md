# Research PM v0.6 implementation candidate and validation

Baseline: `7ad22a107df57d7750b782bd24217caccaa5161a` (v0.5 code).
Approved plans: `c4efb2547f7896e898a8e5083d0a0d438f65240b`.
Development branch: `feature/research-pm-v06-optimization`.

This is an implementation candidate for laptop validation, not a completed live-research performance certification. The main branch and the original v0.5 development branch are not merged or overwritten.

## Implemented path

The desktop starts new projects with `engine_version=6`. Existing v0.5 projects still select EngineV05 and its original web adapter when resumed. Explicit v0.6 continuation creates a new project with the previous project selected as a reference. It does not rewrite the previous database.

The v0.6 path implements structured search intent, deterministic query compilation, durable query batches, page-quality signals, exact-offset paragraph chunks, lexical-first progressive retrieval, reference screening, source-scoped body reuse, bounded pacing, diminishing-return stops, complete event exports and hash-checked replay inputs.

Source collection and factual verification remain different. Lexical relevance, quote matching, a critic pass and accepted evidence counts are not truth certificates. The extractor still returns at most three claims per shown range. Deferred and unread source text must be reviewed in the original; no exhaustive extraction claim is made.

## Deliberate refinements to the approved plan

1. **Balanced fetch cap is three, not the initial proposal of two.** The controlled test with three useful sources failed with two leads: a labelled ring-diameter fact was never collected. The cap was raised to preserve source coverage rather than call that omission an optimization. Efficient remains one; quality remains three with up to three query variants.
2. **Body reuse requires exact body and compatible title.** Identical text with a different title/year/entity can change scope. Title-sensitive reuse is more conservative than body-hash-only reuse. Aliases are recorded and do not count as independent factual support.
3. **Query variants preserve all supplied numeric constraints and negations.** They do not silently shorten away user conditions to get a cheaper query. Unsupported guessed site hints cause a bounded correction request, not a fabricated domain.
4. **Embedding remains OFF by default.** CPU SentenceTransformer and Ollama embedding adapters are optional, lazy and do not download models. The CPU adapter requires an existing local model directory and disables remote code. Ollama requires an installed model and requests CPU placement plus `truncate=false`. This request is not a hardware-placement guarantee; measure actual memory and load behavior on the laptop.
5. **Search failures are typed.** The new worker does not turn backend exceptions into an empty search result. Error envelopes retain class/HTTP code/Retry-After without echoing API secrets. Old v0.5 behavior stays selectable.
6. **Complete logs are exported for v0.6.** The read-only comparison tool detects an old export whose log omits earlier calls and refuses to label it a complete baseline. Missing observed token usage stays null.
7. **Pause during ranking is recoverable.** A source committed before its retrieval plan is rebuilt on resume even if an in-memory enqueued flag was persisted. Saved extractor RESULT_READY responses also recover without re-calling the model.
8. **Short numerical sections are not dropped solely for length.** A title actually obtained from a source can preserve numeric table context; a search title alone is not a fact verification.

## Controlled replay

Command from the repository root (PYTHONPATH must include src):

```text
python tests/v06_replay_fixtures.py <new-output-folder>
```

The comparison executes the production EngineV05 and EngineV06 on the same synthetic page/search fixtures with a deterministic exact-quotation response oracle. It does NOT run Qwen inference or public web search. The oracle's labels are deliberately narrow, inspectable regression targets. They are not an independent research-quality benchmark.

| Synthetic case | v0.5 extractor invocations | v0.6 extractor invocations | Labelled facts retained |
|---|---:|---:|---:|
| Sparse fact at document tail plus distractor | 8 | 1 | 1/1 |
| Dense facts with rated/actual/planned conditions | 2 | 1 | 3/3 |
| Three useful independent URLs | 3 | 3 | 3/3 |
| Mixed-script document plus distractor | 3 | 1 | 1/1 |
| No useful fact | 3 | 0 | 0/0 |
| Total over separate cases | 19 | 6 | 8/8 |

The controlled replay recorded 13 -> 0 irrelevant extraction invocations, 35 -> 22 total model-interface invocations and 39,927 -> 8,410 **pre-transport estimated extractor tokens**. All labelled facts and six relevant source documents were retained; collected original texts were preserved. These are small synthetic regression results, NOT measured Ollama token consumption or a laptop speedup. The actual-token reduction KPI remains unmeasured until a real-model comparison.

The three-useful-source case has no extractor reduction, intentionally: useful material is not removed merely to improve the ratio.

## Tests and CI evidence

The existing v0.5 190-test baseline was executed before changes. The final local Linux/Python 3.13/Xvfb suite ran 262 tests with no failures or skips. The desktop was rendered and inspected under Xvfb. Windows/Python 3.11 results must be obtained from the published CI run below, not inferred from Linux. New tests exercise the production pipeline, original hashes, alias scope and attribution, replay tampering, source offsets, optional model failures, exact schema at the real local HTTP transport boundary, typed search failures, GUI version dispatch, and pause/restart transitions.

Run:

```text
python -m unittest discover -s tests -v
python -m compileall -q src/ollama_deep_researcher
```

Use Xvfb on headless Linux. GitHub Actions runs the suite on Ubuntu and Windows with Python 3.11. The actual result is the `pm-tests-<OS>-<commit>` log artifact for the published commit; consult that artifact and the PR for the final count/status. Do not infer a pass from this document alone.

The optional embedding protocol tests use synthetic vectors and a local HTTP test server. They are not a real embedding-model quality or memory benchmark. Code review in this session is self-review plus regression tests; independent reviewer approval is still pending.

## Read-only comparison of real exports

```text
python -m ollama_deep_researcher.pm_benchmark compare <v05-handoff> <v06-handoff> --output comparison.json
python -m ollama_deep_researcher.pm_benchmark validate-replay <handoff/replay>
```

A changed search backend, model, settings, reference seed or package version makes a live comparison observational. Warm reference reuse must not be compared to a cold run as though both had identical initial data. A missing or truncated log is explicitly reported. Calls, estimates, observed tokens, retained quotes and factual accuracy are separate quantities.

## Remaining release gates

- [ ] Actual Windows RTX 4060 + Qwen3.5:9b 30-minute DDG comparison.
- [ ] Human audit of excluded/deferred pages and original quotations, including cross-language false negatives.
- [ ] Actual CPU-vs-Ollama-vs-lexical embedding experiment, including reloads and total latency.
- [ ] Two-hour laptop soak; later 8/24-hour tests only after that.
- [ ] Independent code review and comprehensive security audit.

No OCR, visual table validation, JavaScript browser rendering, login bypass or cloud reranker requirement was added. Web search queries still leave the computer. A local model does not make public web search offline or inherently confidential.

## Scope of retained data

Fetched originals are preserved even when deferred. Low-ranked *reference* candidates not imported remain in their original project, not in the new project's documents folder; reference_decisions.json records their origin and URL. Export the source project to inspect those originals. Unread-character counts exclude duplicate aliases and do not represent factual coverage.
