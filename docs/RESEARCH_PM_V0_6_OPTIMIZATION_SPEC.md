# Research PM v0.6 Optimization Spec

Date: 2026-09-18  
Baseline: Research PM v0.5, commit `7ad22a107df57d7750b782bd24217caccaa5161a`  
Primary local target: Windows laptop, RTX 4060 Laptop 8 GB, Ollama, `qwen3.5:9b`, context 8192  
Primary comparison backend for release validation: DuckDuckGo

## 1. Release goal

v0.6 is an **efficiency-first retrieval release**.

The optimization target is not “more features.” It is:

> With the same research topic, search backend, local model, context size and elapsed time, produce more useful evidence with fewer wasted model calls, fewer irrelevant extractions, fewer duplicate network/document operations and a shorter time-to-useful-evidence.

v0.6 must preserve the source-first / audit-first guarantees of v0.5. Optimization is never allowed to silently delete originals, manufacture relevance, weaken provenance, or convert a ranking signal into a factual trust claim.

## 2. Reference implementations reviewed

The design borrows *patterns*, not entire applications.

### A. LearningCircuit/local-deep-research
Pinned review reference: commit family around `1d7331436a22488b989e6860852281bf2a0f3f61`.

Useful patterns:
- adaptive per-engine rate limiting with bounded learning;
- canonical URL identity and explicit handling of one URL carrying distinct excerpts;
- dynamic/specialized search-engine routing;
- benchmark-first culture and local-model measurements;
- explicit separation between search result count, filtering and research strategy;
- local knowledge library and semantic retrieval concepts.

Important caution:
- some advertised settings such as `search.quality_check_urls` are currently flagged by that repository's own tests as dead/unconsumed configuration. v0.6 must copy only exercised behavior, never a feature name or documentation claim without code-path verification.
- older/deleted “advanced search” experiments contain useful ideas, but dead code is not treated as production evidence.

### B. assafelovic/gpt-researcher
Pinned review reference: commit family around `6f998577d547b1e54ec662dac63583aa11e3b84b`.

Useful patterns:
- chunk-first context compression;
- embedding similarity as a *pre-filter* before expensive LLM reasoning;
- fast path for small documents, heavier retrieval only for large corpora;
- source curation separated from raw retrieval;
- benchmark emphasis on effective/grounded citations rather than citation count alone.

Important caution:
- its general source curator is another LLM call and can be too expensive on a single 9B local model. v0.6 will not run an LLM curator for every result by default.
- its report-generation pipeline is not our handoff-first objective.

### C. ItzCrazyKns/Vane
Pinned review reference: commit family around `348feca3e378fb4157b217724ed508dc707f853f`.

Useful patterns:
- explicit speed / balanced / quality trade-off;
- several focused queries may be generated in quality mode;
- embedding model is treated as a separate retrieval resource;
- search, answer generation and source selection are separate concerns.

Important caution:
- Vane is an answer engine, not an evidence-preservation PM.
- its web stack, Docker bundle and UI are not imported into the Windows/Tkinter PM.
- quality-mode fan-out must be bounded; more queries are not automatically better.

## 3. What v0.6 keeps because our v0.5 is stronger for this use case

The following are core Research PM advantages and remain non-negotiable:

1. **Project-local SQLite and file isolation.**
2. **Exact original bytes / parsed text preservation.**
3. **Explicit reference-project opt-in; no transitive automatic reuse.**
4. **Legacy project immutability.**
5. **Project-level single-pass extraction.**
6. **One evidence object may link to multiple tasks without cloning the claim.**
7. **Exact quote + source range + content hash provenance.**
8. **Conditions, period, unit and scope are not silently normalized away.**
9. **Incomplete, single-source, uncertain and conflicting material is preserved with status.**
10. **Search-attempt audit trail and handoff bundle.**
11. **Qwen/Ollama token-budget calibration and 8K-safe preflight.**
12. **No cloud search key is required for the default DuckDuckGo path.**
13. **Search/fetch failure isolation and resumability.**
14. **Windows-first simple desktop operation.**

v0.6 extends these guarantees; it does not replace them with a final-report-first architecture.

## 4. What v0.6 explicitly rejects

Do not import or recreate these ideas in the v0.6 core path:

- wholesale migration to Vane/Next.js/Docker;
- wholesale migration to GPT Researcher or another report-writing stack;
- an LLM source-curation call for every search result;
- “official domain = true fact” logic;
- automatic conflict resolution;
- silent dropping of low-ranked originals;
- browser stealth, anti-bot bypass or login circumvention;
- parallel local-Qwen inference on the RTX 4060;
- unbounded multi-query fan-out;
- a new vector database just to rank a handful of web documents;
- increasing default context above 8192 to hide bad retrieval;
- copying dead/advertised-only settings from reference projects;
- enabling a second GPU-resident embedding model by default without measurement.

## 5. v0.6 architecture

### 5.1 Engine version isolation

Create `EngineV06` and keep `EngineV05` intact.

New projects use:

```json
{"engine_version": 6}
```

Existing v0.5 projects remain readable and resumable with v0.5. A deliberate **“0.6 방식으로 이어서 조사”** action creates a new v0.6 project referencing the selected prior project.

### 5.2 Deterministic query compiler

The local LLM no longer returns an arbitrary backend query string as the primary contract.

Researcher returns a bounded semantic intent:

```json
{
  "entity": "Sheffield Forgemasters",
  "gap": "SMR pressure vessel forging contracts",
  "keywords": ["BWRX-300", "pressure vessel"],
  "strategy": "exact_entity",
  "language": "en",
  "site_hint": ""
}
```

The code renders backend-specific queries.

Rules:
- one entity per query by default;
- one information gap per query;
- no multi-company OR chains in normal mode;
- `site:` can only be rendered from user allowlists or already-observed/approved domains;
- quality mode may deterministically emit up to 3 variants from one intent;
- failed/zero-yield strategies trigger a deterministic fallback sequence rather than another unconstrained Boolean query.

### 5.3 Retrieval cascade

Every fetched document follows a cheap-to-expensive cascade:

```text
search hit
→ deterministic URL/site/policy screening
→ fetch
→ redirect/policy verification
→ document quality check
→ content-hash dedup
→ structure-aware chunking with exact offsets
→ cheap lexical/BM25-lite chunk ranking
→ optional semantic rerank for ambiguous/large documents
→ Qwen extraction only on highest-value windows
→ evidence / task linking
```

Low-ranked text is not deleted. It remains available for a second-pass gap search.

### 5.4 Structure-aware chunking

Replace raw character slicing as the preferred first path for v0.6.

Chunks preserve:
- document id and content hash;
- exact start/end character offsets;
- heading/page hints when available;
- neighbor relationships;
- original quote mapping.

Target windows are paragraph/section based. A selected chunk can expand to immediate neighbors so a sentence does not lose its conditions or table caption.

### 5.5 Cheap retrieval before Qwen

Default v0.6 uses a dependency-light lexical ranker first.

Semantic reranking is an **optional second stage** and is not enabled by default until the laptop benchmark proves a net win.

Semantic backend candidates are benchmarked, not assumed:
- CPU multilingual sentence embedding;
- Ollama embedding endpoint;
- lexical-only baseline.

The release default must be selected by measured end-to-end latency, irrelevant-extraction reduction and model-load churn on the target laptop.

### 5.6 Reference-project relevance filtering

The current v0.5 reference candidate search can surface text that matches generic words but is semantically irrelevant.

v0.6 applies the same retrieval cascade to reference-project documents:
- coarse SQL candidate discovery;
- title/body lexical scoring;
- content-hash dedup;
- optional semantic rerank;
- only top candidates enter the extraction queue.

Rejected/deferred reference candidates remain auditable and are not deleted.

### 5.7 Page mismatch / low-value fetch detection

After a page is fetched, compare:
- search title/snippet;
- final URL/host;
- fetched title;
- first meaningful body text;
- query anchors.

Hard reject only objective violations such as source policy / explicit site / redirect violation.

A likely search-result/page mismatch becomes `CONTENT_MISMATCH_DEFERRED`, not data deletion.

Short error pages, cookie-only pages and obvious boilerplate pages are retained as failed/deferred documents but do not consume a Qwen extractor call.

### 5.8 URL and document identity

Use two distinct identities:

1. **Search excerpt identity:** canonical URL + normalized snippet digest.  
   This preserves two genuinely different excerpts from one page.

2. **Document identity:** parsed content hash.  
   Multiple URLs with the same actual body do not trigger repeated extraction.

Canonical URL logic continues to strip trackers and normalize HTTP(S), with a tested bounded list of safe tracker parameters.

### 5.9 Adaptive access pacing

Extend v0.5 host cooldown with a simple, bounded adaptive pacing layer inspired by LearningCircuit:

- search-backend pacing and host-fetch pacing are separate;
- learn only after >=3 observations;
- bounded median/EWMA estimate;
- 429/503/timeouts increase delay;
- successful requests gradually reduce delay;
- 403/451 keep the existing host-cooldown semantics and are not bypassed;
- 404/410 stay URL-specific;
- jitter is bounded;
- estimates are project-local by default.

No reinforcement-learning framework is introduced.

### 5.10 Optimization modes

Add:

- `efficient` — minimum calls, one focused query, lexical retrieval only by default.
- `balanced` — default; one focused query per turn, bounded fallback variants, lexical retrieval and optional semantic escalation only when useful.
- `quality` — up to three deterministic query variants, deeper retrieval and more second-pass chunk coverage.

All modes preserve the same originals/provenance. They change budgets, not truth rules.

### 5.11 Diminishing-return stop policy

A task must stop wasting calls when additional work is no longer productive.

Examples:
- 2 consecutive zero-yield searches → force strategy change;
- 4 consecutive zero-yield searches after strategy changes → mark `STALLED` and keep unresolved gaps;
- task criteria reviewed as supported + 2 further searches with no new useful evidence → `COMPLETE`;
- all tasks COMPLETE/STALLED → project can finish even if global call/search budget remains.

Quality mode may use larger thresholds, but must remain bounded.

### 5.12 Benchmark / replay harness

Optimization changes are not accepted because they “feel faster.”

Add deterministic replay bundles containing:
- normalized search hits;
- fetch outcomes;
- parsed documents;
- expected relevant/irrelevant fixture labels where available;
- model-call counters and timing measurements.

Two levels of validation:

#### A. Offline replay
Same inputs for v0.5 vs v0.6 retrieval logic.

Primary metrics:
- extractor calls;
- irrelevant extractor calls;
- prompt tokens;
- accepted evidence count;
- relevant source coverage;
- duplicate fetch/extraction count;
- time spent in deterministic retrieval.

#### B. Live 30-minute DuckDuckGo A/B
Same topic, Qwen3.5:9b, 8192 context, same laptop, same search backend.

Live metrics:
- time to first accepted evidence;
- accepted claims per 100 model calls;
- accepted claims per 10k prompt tokens;
- productive extraction ratio;
- irrelevant extraction ratio;
- fetch failure ratio;
- unique relevant source count;
- zero-yield searches;
- total model calls/searches;
- wall-clock throughput.

A single benchmark number is never treated as research accuracy.

## 6. Initial optimization budgets

These are starting defaults, not immutable claims:

| Mode | Query variants | Fetch leads / attempt | Chunk first-pass | Semantic rerank | Second pass |
|---|---:|---:|---:|---|---|
| efficient | 1 | 1 | top 2 | off | only on explicit gap |
| balanced | 1 | 2 | top 3 | adaptive/optional | top next 2 after zero evidence |
| quality | up to 3 | 3 | top 5 | optional on large docs | enabled |

The benchmark may change these values before release, but any change must be recorded in the validation document.

## 7. Optimization KPI / release gate

### Deterministic replay requirements
Against v0.5 on the same replay corpus:

- >= 50% reduction in irrelevant extractor calls;
- >= 35% reduction in extractor prompt tokens for the same or higher accepted evidence count;
- zero regression in project-level duplicate extraction guarantees;
- same-or-better relevant source coverage;
- the known “English grammar reference document” class of distractor must not reach Qwen extraction when lexical/semantic retrieval clearly ranks it below relevant material;
- no silent loss of source bytes or unread text.

### Live DuckDuckGo gate
v0.6 should beat v0.5 on at least 4 of these 6 efficiency metrics, with no >10% regression in accepted evidence or unique relevant source count:

1. time to first accepted evidence;
2. accepted claims / model call;
3. accepted claims / prompt token;
4. productive extraction ratio;
5. irrelevant extraction ratio;
6. zero-yield searches / total searches.

The live run is observational because search-engine results vary.

### Stability gate
- full existing test suite remains green;
- Windows Python 3.11 CI green;
- Ubuntu Python 3.11 CI green;
- 30-minute live run green;
- 2-hour soak without state corruption / runaway loop;
- main remains untouched until those gates pass.

## 8. File responsibility map

### New files
- `pm_query_policy.py` — structured research intent and backend query compilation.
- `pm_chunking_v06.py` — structure-aware chunk windows with exact offsets.
- `pm_retrieval_v06.py` — lexical ranking, progressive retrieval, optional semantic interface.
- `pm_fetch_quality.py` — page/body mismatch and boilerplate/error-page checks.
- `pm_rate_limit_v06.py` — bounded adaptive backend/host pacing.
- `pm_search_cycle_v06.py` — v0.6 search/fetch/retrieval orchestration.
- `pm_engine_v06.py` — version-6 engine composition and contracts.
- `pm_benchmark.py` — replay runner and metric calculations.

### Modified files
- `pm_prompts.py` — add v0.6 structured researcher/extractor contracts.
- `pm_types.py` — optimization mode / optional semantic settings.
- `pm_store.py` — retrieval queue, pacing stats, excerpt identity, benchmark-safe metrics.
- `pm_projects.py` — engine_version 6 and explicit continue-as-v0.6.
- `pm_research_metrics.py` — retrieval and efficiency metrics.
- `pm_export_v05.py` or a new `pm_export_v06.py` — v0.6 retrieval decision trail.
- `pm_gui.py` — optimization mode and v0.6 metrics.
- `RESEARCH_PM_KO.md` — behavior / migration / validation instructions.

## 9. Out of scope for v0.6

- OCR / vision;
- JS browser automation;
- authentication bypass;
- cloud reranker requirement;
- parallel local Qwen inference;
- new vector database;
- automatic final factual verdicts;
- automatic source trust certification;
- 16K/32K default context;
- replacing Tkinter with a web frontend;
- replacing project-local databases with a global knowledge graph.

## 10. Licensing / implementation rule

External repositories are references for architecture and behavior. Prefer independent reimplementation against our interfaces.

If code is directly adapted:
- record original repository, file and commit;
- preserve required license notices;
- keep the adapted portion minimal;
- add behavior tests proving the adopted logic works in Research PM's constraints.

The optimization benchmark, not GitHub popularity, decides whether a borrowed idea remains enabled.
