# Research PM 0.6.1 hotfix - execution and validation record

Baseline: cb7580bf5c9abd04a7e34080071975c528391dc5. Scope: the six user-approved hotfixes.

## Diagnosis

- Reproduced the baseline: 262 tests passed locally before editing.
- Added 16 production-boundary regressions before implementation: 12 failed, 4 passed.
- The anchor mismatch can strand an active PLANNED search. A bounded 160-transition test reproduces the repeated failure; this must be recovered on existing checkpoints too.
- Repeated irrelevant reads are not only a second-pass issue. A single selected range is split into child ranges by the budget preflight. Cancellation must cover those pending children and their durable plan, not just future second passes.
- `prefilter_rejected` counts hard URL/policy rejects, not all ranking deferrals. A zero count alone does not prove the filter did nothing. We test the actually selected URLs and fetch calls.
- The uploaded log contains executed queries and model metrics, not raw model replies or full source pages. Reconstructed intents and synthetic documents are labelled as such. The original user log is not uploaded to the public repository.

## Ordered changes

1. Compact one-gap intent validation, bounded corrective retry, language consistency; preserve all original requirements rather than truncating them into a query.
2. Entity-aware search lead and page signals; search-wrapper URLs are not factual source pages. Weak leads are deferred/audited, not deleted.
3. Negative extraction feedback can defer pending split children only when no accepted evidence and no strong entity/technical signal elsewhere in the original. Positive tails and uncertain results must survive.
4. Reuse unseen deterministic variants of the current validated intent before asking the local model again. Scope cache to project/question/policy and bound exhausted-intent retries.
5. Generate bounded search anchors separately from the full entity/gap. Recover incompatible persisted active attempts without an error loop.
6. Log failure class, phase, sanitized message and available transport counters; classify cancellation separately and never log API secrets or model reasoning.

## Invariants

Original bytes, parsed text, existing evidence, source offsets and v0.5 behavior remain intact. Context stays 8192. No new dependency, paid API, second model or source-specific answer is introduced. Deferred does not mean false or permanently irrelevant. A hotfix does not certify the factual correctness of any adopted claim.

## Release checks

- Focused red/green tests and full regression, including source-at-tail, resume and malformed response tests.
- Published-commit Windows/Ubuntu CI and byte-identical update ZIP verification.
- The target laptop, real Qwen output, public DuckDuckGo relevance, and long-duration validation remain user-side live checks, not inferred from fixture success.

## Implemented result

- Compact gap: <=80 characters / <=10 words; complete query <=300 characters / <=24 words. Instruction dumps and multi-entity lists cause one bounded correction request, never silent constraint truncation. These structural guards do not mathematically prove that a phrase has one semantic topic.
- Anchors are independently generated at <=100 characters. Long preserved entity names no longer contradict the downstream hint limit. Incompatible saved PLANNED intents are retired once with originals/work retained.
- Entity-aware search candidates use a full nearby name signal rather than a shared city name. A bounded topic-only exploration path remains for sector pages or abbreviated names. Deferrals are not assertions of irrelevance. Search wrappers are not accepted as source pages.
- Original body quality uses the focused intent, not the union of all instructions. Actual fetched-title numeric context remains eligible; a search title alone does not establish it.
- Negative reading feedback defers still-pending split children only when there is no adopted/uncertain result and no strong signal elsewhere in the original. It is durable over restart. Positive tail fixtures are retained.
- Unseen validated-intent variants run before another Researcher call. Changed instructions/policy/new task evidence invalidate the cache; exhausted repeated intents stop rather than spin.
- MODEL_FAILED includes error_type, error_stage, error_message and available HTTP/token/termination details. Sensitive values are redacted; raw model text/reasoning is not logged. User cancellation is MODEL_CANCELLED.
- HOTFIX_POLICY reports 0.6.1 once. engine_version remains 6; no original DB schema conversion is required. The GUI identifies the installed hotfix.

## Evidence recorded during this implementation

Local Linux / Python 3.13.5 / Xvfb: 300 tests passed, no failures/skips (262 baseline + 38 added tests). compileall and git diff --check passed. Windows/Ubuntu CI must be checked on the published commit; do not infer CI success from this local result.

Representative deterministic regressions:

| Case | Baseline behaviour observed in the new failing test | Hotfix behaviour |
|---|---|---|
| Shared city / unrelated university result | Fetched | Deferred before fetch; hit retained |
| Search wrapper URL | Fetched | No fetch |
| Mislabelled source body | Reached extractor | Original retained, no extractor call |
| Seeded old wrong-entity split queue | 3 extractor calls | 1 call, remaining ranges deferred |
| Three empty-search variants | 3 Researcher calls | 1 Researcher call; 3 distinct queries |
| Accepted 119-character entity | Repeated anchor validation error | Bounded hints; search executes |
| Bad final JSON | Failed without cause in MODEL_FAILED | JSONDecodeError / response_json |
| Useful fact at document tail | Retained | Retained |

These are controlled synthetic program/HTTP tests. The source log lacks original model replies and source-page bytes, so this is NOT a literal replay of all user data, a live DuckDuckGo comparison, an actual Qwen speed benchmark, or factual accuracy certification. No exact token-saving percentage is asserted.

## Application and remaining checks

1. Stop PM completely, then copy pm_data to a separate backup.
2. Overlay the update files at the existing START_RESEARCH_PM.bat directory; keep pm_data and .pm-venv.
3. Start the existing BAT. The window says Research PM 0.6.1.
4. An unfinished v0.6 project can use its normal resume button. The '0.6 continuation' button is for older engine versions, not for converting 0.6 into 0.6.1. A terminal/completed run requires a new project; select references explicitly when reusing its sources.
5. For a clean performance test, use a fresh project without references, DuckDuckGo, qwen3.5:9b, context 8192, output ceiling 3072, balanced mode, semantic reranking off.

Watch: INTENT_RETRY (one corrective request), QUERY_VARIANT_REUSED (no model call), READING_DEFERRED (original retained), ANCHORS_REPAIRED / INTENT_RETIRED (saved checkpoint repair), and MODEL_FAILED with its error class and phase. SEARCH_COMPLETED separates focus_deferred_hits from objective prefilter_rejected.

Limitations: lexical decisions may defer useful cross-language/alias-only pages. Audit deferred candidates and original sources. The local runtime has no user Qwen/GPU access and no public-search integration test; the Windows laptop run and long soak remain unverified. Independent reviewer approval remains pending; current review is self-review plus regression evidence. The release remains a draft development build, not a merge into main.
