# Research PM 0.6.2 reliability repair

Baseline: e7efd4f1ba60458ebada2de0506ed75194f773ac (0.6.1).
Keep feature/research-pm-v06-optimization and draft PR #2 isolated from main/v0.5.

## Root causes and repairs

1. compact_retry requested a shorter answer but appended error context to a near-full input. The new bounded researcher view keeps exact topic/instructions/task criteria and projects only a bounded subset of optional prior attempts, queries and evidence metadata. Repair uses fewer records than the ordinary call. All audit records remain in the project DB. Truly oversized immutable questions still fail explicitly.
2. The topic-only exploration predicate also overrode subsequent negative reading feedback. After a negative extraction, topic-only signals no longer suffice: existing accepted/uncertain results or a target entity anywhere in the full source preserve reading. Otherwise remaining split children are durably deferred, not deleted. Literal alias/cross-language matching remains a limitation.
3. Remaining ranges of the first document could monopolize extraction. After each completed extraction, other queued documents get a turn before more ranges of that document. Exact offsets and durable checkpoints remain.
4. A single gap plus many keywords recreated a multi-topic research instruction. The live researcher schema now requests up to three independent search phrases and shared constraints. Distinct targets precede quotation/PDF fallback variants. Original proposals remain. Legacy valid intents are reused losslessly, not shortened by deleting alloy/negation qualifiers.
5. The actual search library/version was not logged. Published duckduckgo_search v8.1.1 forces backends=['bing'] regardless of requested backend. The user's installed version is UNKNOWN; logs do not prove which upstream release ran. v0.6.2 pins ddgs==9.16.0 and verifies its explicit html.duckduckgo.com engine. No hidden auto-provider fallback or API peer cache. This identifies the HTTP endpoint, not the ultimate provenance of all indexed web results.
6. Actual public-search testing exposed another upstream boundary: the engine base class returns None on non-200 responses, which can be presented as no results. A narrow checked engine wrapper now retains real HTTP status, Retry-After and challenge failure before delegating to the upstream parser. It does not bypass challenges, use a proxy or silently switch engines. Empty results are only accepted from a recognizable normal empty-results page.
7. Actual CPU Qwen testing exposed a hidden 60-second first-response socket cap, despite request_timeout=240. For engine_version=6 the configured inference timeout now reaches the HTTP boundary; legacy versions retain their old policy. Overall response checks remain. Before first response bytes, cancellation may wait for a blocking socket operation; this is not an instantaneous-cancellation guarantee.

## Dependencies and data safety

Run UPDATE_SEARCH_BACKEND.bat once after overlay. It installs ddgs==9.16.0 in the existing .pm-venv; no Ollama model is downloaded by the app/updater. Legacy v0.5 retains its worker. A missing/wrong dependency fails before Planner inference. Engine version remains6; unfinished 0.6/0.6.1 projects, including INPUT_BUDGET_BLOCKED, can be resumed without starting over. Back up pm_data first.

Defaults remain qwen3.5:9b / context8192 / output3072 / balanced / semantic off.
CONTEXT_COMPACTED, SEARCH_PROVIDER, QUERY_VARIANT_REUSED and READING_DEFERRED provide traceable diagnostics. Ranking and accepted-claim counts are not truth certificates. Previous incorrect/missing research is not retrospectively certified.

## Reference implementations inspected

Behaviour references, not wholesale code imports or proof of our quality:
- LangChain short-term-memory trim-before-model: https://docs.langchain.com/oss/python/langchain/short-term-memory
- GPT Researcher context/compression.py, separately bounded retrieval context: https://github.com/assafelovic/gpt-researcher/blob/main/gpt_researcher/context/compression.py
- Legacy backend override: https://github.com/deedy5/ddgs/blob/v8.1.1/duckduckgo_search/duckduckgo_search.py
- Pinned engine selection: https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/ddgs.py
- Actual DDG endpoint/parser: https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/engines/duckduckgo.py
- Non-200 handling seam: https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/base.py
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs

## Regression evidence

Baseline300 tests. Added28 tests, total328. Growing-history, repeated-negative-reading, atomic-query, provider/HTTP and timeout failures were observed before fixes. The local Linux/Python3.13/Xvfb suite passes328; compileall passes. Loopback tests exercise HTTP framing with synthetic model replies and are not real-model performance tests.

Scenarios include50 increasing history sizes; a bad first proposal followed by a genuinely smaller repair and search; other-document scheduling; ASME topic-only guide deferral; exact-original retention; useful facts at document tails and another task's entity; unchanged legacy qualifiers; missing provider before any model call; refused unexpected engines; provider metadata on errors; actual non-200/challenge responses not counted as empty success; configured socket timeout.

## Actual public-network validation

Run35409389801 on commit3e75b4e completed all three public searches with HTTP200 from the explicit DuckDuckGo endpoint. Results: Sheffield Forgemasters9, Japan Steel Works7, Doosan Enerbility9. Each query yielded a fetched body. Sheffield's first Rolls-Royce page returned403; the next Sheffield Forgemasters page was fetched. The JSW first fetched page was a third-party article, not an authoritative verification; its search results also included JSW and OSTI pages. Doosan's fetched source was its own NuScale page. A previous run had unconfirmed empty responses for two queries; that failure is retained in the validation record, not hidden.

This small search check establishes actual endpoint/result/fetch behaviour, not complete source coverage or scientific accuracy. Results and access policies can change. The script spaces requests30s and fetches at most three candidates per query. No private research DB or user log is uploaded.

## Actual model validation and final CI

The initial actual Qwen3.5:9b CPU test correctly extracted a synthetic4200-tonne statement and rejected a university distractor, but three researcher requests timed out at the hidden60s socket cap. The timeout was corrected and actual model checks rerun on c79efafe. Consult the PR and the corresponding actual artifacts for the final result; this document does not predeclare a pass.

Final Windows/Linux CI logs must likewise be read from the published commit. The optional workflow runs on explicit commit markers or manual dispatch. It pins official Ollama0.34.2 with a verified binary SHA256 and uses transient model weights in CI, not in the user's ZIP. Its three real researcher and two synthetic-source extraction checks are separate from the public-search checks and do not constitute a long integrated Windows/GPU research run.

## Limitations

Independent reviewer approval, long target-laptop soak, alias/cross-language false-negative audit, full research coverage and factual accuracy remain unverified. No promise of zero future errors, exhaustive extraction or guaranteed GPU speedup. No OCR, browser automation, login circumvention, added GPU model or enlarged default context.

Apply as described in APPLY_0.6.2.txt. The adapter has changed, so old/new runs are not an identical-provider benchmark without controlling installed library versions.
