# Research PM v0.6 Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Research PM v0.6 produce more useful evidence per unit of wall time, model call and prompt token by borrowing proven retrieval patterns from Local Deep Research, GPT Researcher and Vane while preserving v0.5's stronger project isolation, source preservation and provenance model.

**Architecture:** v0.6 is a new engine path, not an in-place rewrite of v0.5. The pipeline changes from “search → fetch → mostly-Qwen relevance/extraction” to a progressive cascade: structured search intent → deterministic backend query → fetch quality/identity checks → structure-aware chunks → cheap lexical ranking → optional measured semantic rerank → single-pass Qwen extraction. A replay benchmark is built first and remains the release gate for every optimization.

**Tech Stack:** Python 3.11+, Tkinter, SQLite, Ollama HTTP API, qwen3.5:9b, DuckDuckGo/SearXNG/Tavily adapters, standard-library lexical ranking, optional semantic backend behind an interface, unittest, GitHub Actions Windows + Ubuntu.

**Spec:** `docs/RESEARCH_PM_V0_6_OPTIMIZATION_SPEC.md`

## Global Constraints

- Baseline v0.5 commit: `7ad22a107df57d7750b782bd24217caccaa5161a`.
- Default local model remains `qwen3.5:9b`.
- Default context remains `8192`; output ceiling remains `3072` unless a role-specific cap is lower.
- DuckDuckGo remains the primary release-comparison backend.
- No cloud API key is required for the default path.
- No parallel local-Qwen inference.
- No browser stealth/login bypass.
- No source byte/text deletion because of rank or relevance.
- Project-local SQLite and explicit non-transitive reference projects remain mandatory.
- v0.5 projects are not rewritten in place.
- All behavior changes follow TDD: failing test → minimum implementation → full regression → focused commit.
- External repositories are behavior references. Directly adapted code must preserve license/attribution.
- Optimization is accepted only when replay/live metrics improve without provenance or relevant-source regression.

---

## File structure

### New modules

- `src/ollama_deep_researcher/pm_query_policy.py`
  - Validates structured research intent.
  - Renders short backend-specific queries.
  - Generates bounded fallback variants.
  - Prevents multi-entity Boolean explosion.

- `src/ollama_deep_researcher/pm_chunking_v06.py`
  - Paragraph/heading/page-aware source windows.
  - Exact offsets and neighbor relationships.
  - No source mutation.

- `src/ollama_deep_researcher/pm_retrieval_v06.py`
  - BM25-lite / lexical chunk ranking.
  - Progressive first-pass/second-pass selection.
  - Optional semantic rerank interface.

- `src/ollama_deep_researcher/pm_fetch_quality.py`
  - Search-result ↔ fetched-page consistency signals.
  - Boilerplate/error-page detection.
  - Search excerpt identity.

- `src/ollama_deep_researcher/pm_rate_limit_v06.py`
  - Project-local adaptive engine and host pacing.
  - Bounded median/EWMA and retry classification.

- `src/ollama_deep_researcher/pm_search_cycle_v06.py`
  - v0.6 search/fetch/retrieval orchestration.

- `src/ollama_deep_researcher/pm_engine_v06.py`
  - v0.6 composition, stop policy and role contracts.

- `src/ollama_deep_researcher/pm_benchmark.py`
  - Replay bundle loader.
  - Efficiency metrics and v0.5/v0.6 comparison.

### Existing modules modified

- `src/ollama_deep_researcher/pm_prompts.py`
- `src/ollama_deep_researcher/pm_types.py`
- `src/ollama_deep_researcher/pm_store.py`
- `src/ollama_deep_researcher/pm_projects.py`
- `src/ollama_deep_researcher/pm_research_metrics.py`
- `src/ollama_deep_researcher/pm_gui.py`
- `src/ollama_deep_researcher/pm_export_v05.py` or create `pm_export_v06.py`
- `RESEARCH_PM_KO.md`

### New tests

- `tests/test_pm_v06_benchmark.py`
- `tests/test_pm_v06_query_policy.py`
- `tests/test_pm_v06_chunking.py`
- `tests/test_pm_v06_retrieval.py`
- `tests/test_pm_v06_fetch_quality.py`
- `tests/test_pm_v06_reference_retrieval.py`
- `tests/test_pm_v06_rate_limit.py`
- `tests/test_pm_v06_search_flow.py`
- `tests/test_pm_v06_engine.py`
- `tests/test_pm_v06_gui.py`
- `tests/test_pm_v06_acceptance.py`

---

### Task 1: Build the replay benchmark before optimizing anything

**Files:**
- Create: `src/ollama_deep_researcher/pm_benchmark.py`
- Create: `tests/test_pm_v06_benchmark.py`
- Modify: `src/ollama_deep_researcher/pm_export_v05.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class EfficiencyMetrics:
    model_calls: int
    extractor_calls: int
    extractor_prompt_tokens: int
    irrelevant_extractions: int
    accepted_claims: int
    unique_relevant_documents: int
    fetch_attempts: int
    fetch_failures: int
    zero_yield_searches: int
    duplicate_fetches: int
    duplicate_extractions: int

def metrics_from_events(events: list[dict], evidence: list[dict]) -> EfficiencyMetrics: ...
def compare_efficiency(baseline: EfficiencyMetrics, candidate: EfficiencyMetrics) -> dict: ...
def load_replay_bundle(path: Path) -> dict: ...
```

Replay bundle export:

```text
replay/
  manifest.json
  search_hits.jsonl
  fetch_outcomes.jsonl
  documents/
  expected_labels.json
```

- [ ] **Step 1: Write failing metric tests**

```python
def test_efficiency_metrics_count_productive_and_irrelevant_extraction():
    events = [
        {"kind":"MODEL_COMPLETED","role":"extractor","prompt_eval_count":1400},
        {"kind":"EXTRACTION_COMPLETED","relevance":"irrelevant","accepted_claims":0},
        {"kind":"MODEL_COMPLETED","role":"extractor","prompt_eval_count":900},
        {"kind":"EXTRACTION_COMPLETED","relevance":"relevant","accepted_claims":2,
         "document":"d1"},
    ]
    m = metrics_from_events(events, [{"id":"e1"},{"id":"e2"}])
    assert m.extractor_calls == 2
    assert m.extractor_prompt_tokens == 2300
    assert m.irrelevant_extractions == 1
    assert m.accepted_claims == 2
```

Also test divide-by-zero handling and distinct-document counting.

- [ ] **Step 2: Run focused test and verify RED**

Run:

```bash
python -m unittest tests.test_pm_v06_benchmark -v
```

Expected: import/function failures.

- [ ] **Step 3: Implement metrics and replay validation**

Reject malformed replay entries rather than silently skipping them. Every document entry must include id, URL, content hash and body path.

- [ ] **Step 4: Extend v0.5 handoff with an optional replay bundle**

The replay export must be additive. Existing handoff consumers remain valid.

- [ ] **Step 5: Verify GREEN + full suite**

```bash
python -m unittest tests.test_pm_v06_benchmark -v
python -m unittest discover -s tests -v
```

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_benchmark.py         src/ollama_deep_researcher/pm_export_v05.py         tests/test_pm_v06_benchmark.py
git commit -m "test(pm): add v0.6 replay efficiency baseline"
```

---

### Task 2: Replace arbitrary Researcher queries with structured intents

**Files:**
- Create: `src/ollama_deep_researcher/pm_query_policy.py`
- Modify: `src/ollama_deep_researcher/pm_prompts.py`
- Test: `tests/test_pm_v06_query_policy.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ResearchIntent:
    entity: str
    gap: str
    keywords: tuple[str, ...]
    strategy: str
    language: str
    site_hint: str

def validate_intent(data: dict, known_domains: set[str]) -> ResearchIntent: ...
def compile_query(intent: ResearchIntent, backend: str) -> str: ...
def query_variants(intent: ResearchIntent, mode: str, zero_yield_streak: int) -> list[str]: ...
```

Allowed strategies:

```python
("broad", "exact_entity", "official_site", "pdf", "gap")
```

- [ ] **Step 1: Write failing query-policy tests**

```python
def test_compiler_keeps_one_entity_and_one_gap():
    i = ResearchIntent(
        entity="Sheffield Forgemasters",
        gap="SMR pressure vessel forging",
        keywords=("BWRX-300","pressure vessel"),
        strategy="exact_entity",
        language="en",
        site_hint="",
    )
    q = compile_query(i, "duckduckgo")
    assert '"Sheffield Forgemasters"' in q
    assert " OR " not in q
    assert "Doosan" not in q

def test_unknown_site_hint_is_rejected():
    with self.assertRaises(ValueError):
        validate_intent(
            {"entity":"X","gap":"capacity","keywords":["forging"],
             "strategy":"official_site","language":"en","site_hint":"guessed.example"},
            known_domains={"known.example"},
        )

def test_quality_mode_has_at_most_three_variants():
    assert len(query_variants(intent, "quality", 0)) <= 3
```

Add a regression test using the exact bad v0.5 pattern:

```text
(Sheffield Forgemasters OR JSW Metals OR Doosan Enerbility)
AND (SMR OR NuScale OR BWRX-300)
...
```

The compiler must never create that shape from one intent.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Add `V06_PROMPTS['researcher']` and schema**

Researcher output must contain semantic fields only. It does not output raw operators such as `OR`, `site:`, `filetype:`.

- [ ] **Step 4: Implement deterministic rendering and fallback variants**

Fallback order:

```text
exact entity + gap
→ exact entity + 1-2 keywords
→ PDF variant
→ broad entity + gap
→ known official domain only when known_domains contains it
```

- [ ] **Step 5: Verify GREEN + regression**

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_query_policy.py         src/ollama_deep_researcher/pm_prompts.py         tests/test_pm_v06_query_policy.py
git commit -m "feat(pm): compile bounded v0.6 search intents"
```

---

### Task 3: Add structure-aware, provenance-safe chunking

**Files:**
- Create: `src/ollama_deep_researcher/pm_chunking_v06.py`
- Test: `tests/test_pm_v06_chunking.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SourceChunk:
    id: str
    start: int
    end: int
    text: str
    heading: str
    page_hint: str
    previous_id: str | None
    next_id: str | None

def build_chunks(body: str, *, target_chars: int = 1600,
                 max_chars: int = 2400) -> list[SourceChunk]: ...

def expand_with_neighbors(chunks: list[SourceChunk], chunk_id: str,
                          max_chars: int = 3200) -> tuple[int, int, str]: ...
```

- [ ] **Step 1: Write failing provenance tests**

Required assertions:
- `body[chunk.start:chunk.end] == chunk.text`;
- no non-whitespace source text disappears from the union of chunks;
- paragraph boundaries are preferred over arbitrary character cuts;
- a heading remains attached to the following paragraph;
- neighbor expansion still maps to one exact contiguous source range.

```python
def test_quote_offsets_round_trip():
    body = "Heading\n\nAlpha condition 500 C.\n\nBeta result."
    chunks = build_chunks(body, target_chars=25, max_chars=40)
    for c in chunks:
        assert body[c.start:c.end] == c.text
```

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement paragraph-first splitting**

Do not normalize or rewrite source text. Use offsets into the original string.

- [ ] **Step 4: Add pathological-input tests**
  - very long unbroken paragraph;
  - CRLF;
  - CJK text;
  - empty/whitespace input.

- [ ] **Step 5: Verify GREEN**

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_chunking_v06.py         tests/test_pm_v06_chunking.py
git commit -m "feat(pm): add provenance-safe structure chunking"
```

---

### Task 4: Add cheap lexical retrieval before Qwen

**Files:**
- Create: `src/ollama_deep_researcher/pm_retrieval_v06.py`
- Test: `tests/test_pm_v06_retrieval.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class RankedChunk:
    chunk_id: str
    lexical_score: float
    semantic_score: float | None
    combined_score: float
    reasons: tuple[str, ...]

def rank_chunks(query_text: str, chunks: list[SourceChunk],
                task_terms: list[str]) -> list[RankedChunk]: ...

def select_progressive(ranked: list[RankedChunk], mode: str,
                       pass_number: int) -> list[str]: ...

class SemanticReranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...
```

Implementation constraint: first version uses a standard-library BM25-lite/token-frequency ranker. No heavy dependency is added here.

- [ ] **Step 1: Write failing relevance tests**

Fixture contains:
- English grammar distractor;
- forging press specification;
- unrelated culture paragraph.

```python
def test_forging_chunk_ranks_above_grammar_distractor():
    ranked = rank_chunks(
        'nuclear forging press capacity BWRX-300',
        chunks,
        ['forging','press','capacity','BWRX-300'],
    )
    assert ranked[0].chunk_id == 'forging'
```

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement lexical scoring**

Score components:
- exact entity phrase;
- anchor/token overlap;
- title/heading bonus;
- rare-token weighting inside the document;
- no “official = true” bonus.

- [ ] **Step 4: Implement progressive selection**

Initial budgets:

```python
FIRST_PASS = {"efficient":2, "balanced":3, "quality":5}
SECOND_PASS = {"efficient":0, "balanced":2, "quality":5}
```

Second pass must choose the next unseen ranked chunks, not repeat the first pass.

- [ ] **Step 5: Verify GREEN + deterministic ordering**

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_retrieval_v06.py         tests/test_pm_v06_retrieval.py
git commit -m "feat(pm): rank source chunks before model extraction"
```

---

### Task 5: Add fetched-page quality checks and two-level identity

**Files:**
- Create: `src/ollama_deep_researcher/pm_fetch_quality.py`
- Modify: `src/ollama_deep_researcher/pm_types.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Test: `tests/test_pm_v06_fetch_quality.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class PageQuality:
    status: str
    score: float
    reasons: tuple[str, ...]

def search_excerpt_key(url: str, snippet: str) -> str: ...
def document_body_key(body: str) -> str: ...
def assess_fetched_page(*, query_anchors: list[str], search_title: str,
                        search_snippet: str, fetched_title: str,
                        body: str) -> PageQuality: ...
```

Statuses:
- `READY`
- `LOW_SIGNAL_DEFERRED`
- `CONTENT_MISMATCH_DEFERRED`
- `ERROR_PAGE_DEFERRED`

- [ ] **Step 1: Write failing identity tests**

```python
def test_same_url_distinct_snippets_keep_distinct_excerpt_keys():
    assert search_excerpt_key(url, "press capacity") != search_excerpt_key(url, "SMR contract")

def test_same_body_on_different_urls_has_same_document_key():
    assert document_body_key(body) == document_body_key(body)
```

- [ ] **Step 2: Write failing page-quality tests**
  - cookie-only page becomes deferred;
  - “English conjunction grammar” body returned for nuclear forging search is deferred;
  - a relevant page with different title but matching anchor/body is not hard rejected.

- [ ] **Step 3: Verify RED**

- [ ] **Step 4: Implement quality signals**

No deferred page is deleted. Raw bytes/text and reason are retained.

- [ ] **Step 5: Store excerpt key and body key**

Document extraction dedup uses body key. Search decision history uses excerpt key.

- [ ] **Step 6: Verify GREEN + full suite**

- [ ] **Step 7: Commit**

```bash
git add src/ollama_deep_researcher/pm_fetch_quality.py         src/ollama_deep_researcher/pm_types.py         src/ollama_deep_researcher/pm_store.py         tests/test_pm_v06_fetch_quality.py
git commit -m "feat(pm): defer mismatched pages before model extraction"
```

---

### Task 6: Apply retrieval ranking to reference projects

**Files:**
- Modify: `src/ollama_deep_researcher/pm_projects.py`
- Modify: `src/ollama_deep_researcher/pm_retrieval_v06.py`
- Test: `tests/test_pm_v06_reference_retrieval.py`

**Interfaces:**

```python
def reference_candidates_v06(self, pid: str, intent: ResearchIntent,
                             task_catalog: list[dict], limit: int = 12) -> list[dict]: ...
```

Candidate dict adds:
- `lexical_score`
- `retrieval_reason`
- `content_hash`
- `origin_project_id`
- `origin_document_id`

- [ ] **Step 1: Write the regression test from the real observed failure**

Create a reference project containing:
1. English grammar post using common words;
2. nuclear forging capability document.

Query is the SMR/forging topic.

Assert only the forging document enters the first extraction queue. Grammar text remains in the old project and may appear as a deferred candidate record.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement coarse SQL → lexical ranking → content-hash dedup**

Do not call Qwen merely to rank reference candidates.

- [ ] **Step 4: Verify legacy source immutability with hashes before/after**

- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_projects.py         src/ollama_deep_researcher/pm_retrieval_v06.py         tests/test_pm_v06_reference_retrieval.py
git commit -m "perf(pm): rank reference candidates before extraction"
```

---

### Task 7: Add bounded adaptive search/host pacing

**Files:**
- Create: `src/ollama_deep_researcher/pm_rate_limit_v06.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Test: `tests/test_pm_v06_rate_limit.py`

**Interfaces:**

```python
@dataclass
class PaceState:
    samples: int
    base_wait: float
    success_rate: float
    recent_waits: list[float]

class AdaptivePacer:
    def next_wait(self, key: str) -> float: ...
    def record(self, key: str, wait: float, *, success: bool,
               status_code: int | None, error_type: str = "") -> None: ...
```

Separate keys:
- `search:duckduckgo`
- `search:searxng`
- `host:example.com`

- [ ] **Step 1: Write failing behavior tests**

```python
def test_three_rate_limit_failures_raise_wait():
    p = AdaptivePacer(...)
    for _ in range(3):
        p.record("search:duckduckgo", .1, success=False, status_code=429)
    assert p.next_wait("search:duckduckgo") > .1

def test_successes_reduce_wait_slowly_not_to_zero():
    ...

def test_403_uses_host_cooldown_not_retry_spin():
    ...
```

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement bounded median/EWMA**

Bounds:
- floor 0.05 s;
- normal ceiling 10 s;
- minimum three samples before learned adjustment.

- [ ] **Step 4: Persist project-local state**

No global cross-project behavior by default.

- [ ] **Step 5: Verify GREEN**

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_rate_limit_v06.py         src/ollama_deep_researcher/pm_store.py         tests/test_pm_v06_rate_limit.py
git commit -m "perf(pm): learn bounded search and host pacing"
```

---

### Task 8: Build the v0.6 search/fetch/retrieval cycle

**Files:**
- Create: `src/ollama_deep_researcher/pm_search_cycle_v06.py`
- Modify: `src/ollama_deep_researcher/pm_research_metrics.py`
- Test: `tests/test_pm_v06_search_flow.py`

**Interfaces:**

```python
class SearchCycleV06(SearchCycle):
    def research(self, state, task, cfg): ...
    def enqueue_ranked_document(self, state, task, document_id, intent): ...
```

The persisted search attempt adds:
- `intent_json`
- `compiled_query`
- `variant_index`
- `page_quality_status`
- `ranked_chunks`
- `selected_chunk_ids`
- `model_skipped_reason`

- [ ] **Step 1: Write failing end-to-end synthetic test**

Synthetic search returns:
- one site mismatch;
- one cookie/error page;
- one grammar distractor;
- one relevant forging page;
- duplicate body on another URL.

Expected:
- site mismatch not fetched;
- error page not sent to extractor;
- grammar page fetched/preserved but not sent to extractor;
- duplicate body extracted once;
- forging page produces extraction work.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement the cascade in exact order**

```text
intent
→ compile query
→ backend pacing
→ search
→ existing v0.5 URL/site filter
→ fetch pacing
→ fetch
→ redirect policy
→ page quality
→ content-body dedup
→ structure chunks
→ lexical rank
→ select first pass
→ enqueue extraction
```

- [ ] **Step 4: Add progressive second pass**

If a high-ranking document returns zero accepted claims, enqueue the next unseen ranked chunks once according to mode budget.

- [ ] **Step 5: Verify resume after interruption**

Checkpoint before every irreversible external operation and before/after selected-chunk queue changes.

- [ ] **Step 6: Full regression**

- [ ] **Step 7: Commit**

```bash
git add src/ollama_deep_researcher/pm_search_cycle_v06.py         src/ollama_deep_researcher/pm_research_metrics.py         tests/test_pm_v06_search_flow.py
git commit -m "feat(pm): integrate v0.6 progressive retrieval cycle"
```

---

### Task 9: Add EngineV06 and diminishing-return stopping

**Files:**
- Create: `src/ollama_deep_researcher/pm_engine_v06.py`
- Modify: `src/ollama_deep_researcher/pm_prompts.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Test: `tests/test_pm_v06_engine.py`

**Interfaces:**

```python
class EngineV06(SearchCycleV06, EngineV05):
    ...

def next_task_action(task: dict, mode: str) -> str:
    # CONTINUE | CHANGE_STRATEGY | COMPLETE | STALLED
```

State counters:
- `zero_yield_streak`
- `no_new_evidence_streak`
- `strategy_changes`
- `productive_attempts`

- [ ] **Step 1: Write failing stop-policy tests**

```python
def test_two_zero_yields_force_strategy_change():
    task = fixture_task(zero_yield_streak=2)
    assert next_task_action(task, "balanced") == "CHANGE_STRATEGY"

def test_four_zero_yields_after_strategy_changes_stall_task():
    task = fixture_task(zero_yield_streak=4, strategy_changes=2)
    assert next_task_action(task, "balanced") == "STALLED"

def test_supported_task_with_two_empty_followups_completes():
    ...
```

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement EngineV06**

Use v0.5 critic/evidence-task review logic unchanged unless a test proves a required change.

- [ ] **Step 4: Add v0.6 extractor contract**

Extractor still sees exact text and task catalogue. It must not see only a paraphrased retrieval summary.

- [ ] **Step 5: Verify no task explosion and no duplicate source-range extraction**

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_engine_v06.py         src/ollama_deep_researcher/pm_prompts.py         src/ollama_deep_researcher/pm_store.py         tests/test_pm_v06_engine.py
git commit -m "feat(pm): add v0.6 engine and diminishing-return stops"
```

---

### Task 10: Add optimization modes and explicit v0.6 continuation

**Files:**
- Modify: `src/ollama_deep_researcher/pm_types.py`
- Modify: `src/ollama_deep_researcher/pm_projects.py`
- Modify: `src/ollama_deep_researcher/pm_gui.py`
- Test: `tests/test_pm_v06_gui.py`
- Test: `tests/test_pm_projects.py`

**Interfaces:**

```python
optimization_mode: str = "balanced"
semantic_rerank: str = "off"  # off | auto | on

def continue_as_v06(self, source_project_id: str,
                    settings: Settings | None = None) -> str: ...
```

Mode labels in GUI:
- `효율 우선` → efficient
- `균형` → balanced
- `품질 우선` → quality

- [ ] **Step 1: Write failing settings tests**
  - invalid mode rejected;
  - default is balanced;
  - old saved settings load as balanced.

- [ ] **Step 2: Write failing project immutability test**

Hash the source v0.5 DB/original files before and after `continue_as_v06()`; hashes must match.

- [ ] **Step 3: Verify RED**

- [ ] **Step 4: Implement new-project continuation**

New state:

```python
state["engine_version"] = 6
state["reference_projects"] = [source_project_id]
```

- [ ] **Step 5: Wire GUI engine selection by engine_version**

v0.5 resume still instantiates EngineV05. v0.6 instantiates EngineV06.

- [ ] **Step 6: Verify GREEN + Tk tests**

- [ ] **Step 7: Commit**

```bash
git add src/ollama_deep_researcher/pm_types.py         src/ollama_deep_researcher/pm_projects.py         src/ollama_deep_researcher/pm_gui.py         tests/test_pm_v06_gui.py tests/test_pm_projects.py
git commit -m "feat(pm): add v0.6 optimization modes and continuation"
```

---

### Task 11: Add optional semantic reranking behind a benchmark gate

**Files:**
- Modify: `src/ollama_deep_researcher/pm_retrieval_v06.py`
- Modify: `src/ollama_deep_researcher/pm_types.py`
- Test: `tests/test_pm_v06_retrieval.py`
- Create: `tests/test_pm_v06_semantic.py`

**Interfaces:**

```python
class SemanticBackend(Protocol):
    name: str
    def score(self, query: str, texts: list[str]) -> list[float]: ...

class NullSemanticBackend:
    name = "off"
    def score(...): ...

def combine_scores(lexical: float, semantic: float | None,
                   mode: str) -> float: ...
```

No specific embedding package becomes mandatory in this task.

- [ ] **Step 1: Write failing interface/fallback tests**

If semantic backend errors, lexical ranking continues and logs `SEMANTIC_RERANK_SKIPPED`; no document disappears.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement interface and null backend**

- [ ] **Step 4: Add benchmark adapters in a separate optional module only after measuring**
  - CPU multilingual embedding adapter;
  - Ollama embedding adapter.

Each adapter must expose memory/load timing.

- [ ] **Step 5: Run three-way benchmark**
  - lexical only;
  - CPU embedding;
  - Ollama embedding.

Measure:
- first-use load time;
- average rerank time;
- Qwen model reload/unload side effects;
- irrelevant extractor calls avoided;
- accepted evidence preserved.

- [ ] **Step 6: Set release default**

Default stays `off` unless the target laptop benchmark demonstrates:
- >=20% additional irrelevant-call reduction over lexical-only;
- <=10% wall-time regression;
- no repeated Qwen model unload/reload.

If not met, ship the semantic interface disabled and document the result.

- [ ] **Step 7: Commit**

```bash
git add src/ollama_deep_researcher/pm_retrieval_v06.py         src/ollama_deep_researcher/pm_types.py         tests/test_pm_v06_retrieval.py tests/test_pm_v06_semantic.py
git commit -m "feat(pm): gate optional semantic reranking by benchmark"
```

---

### Task 12: Export optimization decisions and surface the right metrics

**Files:**
- Create: `src/ollama_deep_researcher/pm_export_v06.py`
- Modify: `src/ollama_deep_researcher/pm_gui.py`
- Modify: `src/ollama_deep_researcher/pm_research_metrics.py`
- Test: `tests/test_pm_v06_gui.py`
- Test: `tests/test_pm_v06_benchmark.py`

**Interfaces:**

GUI project metrics:
- model calls;
- extractor calls;
- productive extraction ratio;
- irrelevant extractions;
- accepted claims;
- prompt tokens / accepted claim;
- fetch failure ratio;
- deferred low-signal pages;
- duplicate bodies avoided;
- zero-yield streak;
- current pace wait.

Handoff adds:
- `retrieval_decisions.csv`
- `chunk_rankings.jsonl`
- `efficiency_metrics.json`
- `pacing.json`
- replay bundle.

- [ ] **Step 1: Write failing export tests**

Every selected Qwen chunk must be traceable back to:
- document;
- exact source offsets;
- retrieval score/reason;
- search attempt.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement bounded aggregate queries**

GUI refresh must not scan every document/chunk each second.

- [ ] **Step 4: Verify formula-escaping for CSV remains intact**

- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_export_v06.py         src/ollama_deep_researcher/pm_gui.py         src/ollama_deep_researcher/pm_research_metrics.py         tests/test_pm_v06_gui.py tests/test_pm_v06_benchmark.py
git commit -m "feat(pm): expose v0.6 retrieval efficiency metrics"
```

---

### Task 13: Add full v0.6 synthetic acceptance suite

**Files:**
- Create: `tests/test_pm_v06_acceptance.py`
- Modify: `.github/workflows/pm-tests.yml` only if required for artifact retention.

**Fixture scenario:**
- 8 fixed research tasks;
- 2 relevant official/technical pages;
- 1 English grammar distractor;
- 1 cookie wall;
- 1 repeated 403 host;
- 1 duplicate body at a different URL;
- 1 useful page with two distinct search snippets;
- 1 legacy/reference copy;
- long CJK/English mixed instructions and source text.

- [ ] **Step 1: Write acceptance test**

Required assertions:

```python
assert grammar_page.qwen_extractor_calls == 0
assert cookie_page.qwen_extractor_calls == 0
assert duplicate_body.total_extractions == 1
assert useful_page.distinct_excerpt_records == 2
assert project.task_count == planned_task_count
assert project.prompt_budget_errors == 0
assert project.original_hashes_unchanged is True
assert project.resume_result == uninterrupted_result
```

- [ ] **Step 2: Compare replay metrics against v0.5 fixture**

Release thresholds:
- irrelevant extractor calls reduced >=50%;
- extractor prompt tokens reduced >=35%;
- accepted evidence count same or higher;
- relevant source coverage same or higher.

- [ ] **Step 3: Run Linux full suite**

```bash
xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src/ollama_deep_researcher
```

- [ ] **Step 4: Push and require GitHub Actions**
  - Ubuntu / Python 3.11 green.
  - Windows / Python 3.11 green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_pm_v06_acceptance.py .github/workflows/pm-tests.yml
git commit -m "test(pm): add v0.6 optimization acceptance gate"
```

---

### Task 14: 30-minute live DuckDuckGo A/B gate on the target laptop

**Files:**
- Create: `docs/RESEARCH_PM_V0_6_VALIDATION.md`
- Modify: `RESEARCH_PM_KO.md`

**Test conditions:**

Keep constant:
- same nuclear/SMR forging topic;
- same user instructions;
- DuckDuckGo;
- qwen3.5:9b;
- context 8192;
- output ceiling 3072;
- same laptop;
- 30-minute wall-clock limit.

v0.5 comparison uses the existing project/export. v0.6 is a new project referencing the old project only when that is part of the test definition; record which run type was used.

- [ ] **Step 1: Capture v0.5 baseline metrics**

Record:
- model_calls;
- extractor_calls;
- prompt_tokens;
- accepted_claims;
- unique relevant documents;
- productive extraction ratio;
- irrelevant extraction ratio;
- zero-yield search ratio;
- fetch failure ratio;
- time to first accepted claim.

- [ ] **Step 2: Run v0.6 balanced mode for 30 minutes**

Do not change search backend or context mid-run.

- [ ] **Step 3: Calculate six-metric comparison**

v0.6 must beat v0.5 on at least 4 of:
1. time to first evidence;
2. claims/model call;
3. claims/prompt token;
4. productive extraction ratio;
5. irrelevant extraction ratio;
6. zero-yield search ratio.

And:
- accepted evidence count must not regress >10%;
- unique relevant source count must not regress >10%.

- [ ] **Step 4: Inspect false negatives manually**

Review at least:
- 10 pre-Qwen deferred pages;
- all pages with high lexical score but no extraction;
- all reference candidates filtered out from the first pass.

Record whether apparently useful material was suppressed.

- [ ] **Step 5: Run 2-hour soak only after the 30-minute gate passes**

Check:
- resume/pause;
- DB integrity;
- no runaway search/query loop;
- no PromptBudgetError storm;
- no repeated Qwen unload/load caused by optional semantic backend;
- GPU remains single-model inference lane.

- [ ] **Step 6: Write factual validation report**

Do not claim research accuracy from efficiency metrics alone.

- [ ] **Step 7: Commit validation docs**

```bash
git add docs/RESEARCH_PM_V0_6_VALIDATION.md RESEARCH_PM_KO.md
git commit -m "docs(pm): record v0.6 optimization validation"
```

---

## Release gate

v0.6 is ready for normal use only when all items are true:

- [ ] Replay benchmark exists and can compare v0.5/v0.6 deterministically.
- [ ] Researcher emits structured intent, not arbitrary Boolean query strings.
- [ ] One-entity/one-gap query compiler tests pass.
- [ ] Structure-aware chunks preserve exact offsets.
- [ ] Grammar/cookie/error distractors are preserved but skip Qwen by default.
- [ ] Search excerpt identity preserves distinct excerpts from one URL.
- [ ] Duplicate content at different URLs is extracted once.
- [ ] Reference-project distractors are ranked below relevant candidates without modifying the source project.
- [ ] Adaptive pacing never bypasses 403/451 policy.
- [ ] v0.5 projects remain unchanged.
- [ ] v0.6 projects use engine_version 6.
- [ ] Efficient / balanced / quality modes are bounded.
- [ ] Semantic reranking default is backed by laptop measurement or remains off.
- [ ] Replay: >=50% fewer irrelevant extractor calls.
- [ ] Replay: >=35% fewer extractor prompt tokens for same-or-better evidence.
- [ ] Relevant source coverage does not regress in replay.
- [ ] Existing tests plus v0.6 tests pass.
- [ ] Windows CI green.
- [ ] Ubuntu CI green.
- [ ] 30-minute DuckDuckGo A/B passes.
- [ ] Manual false-negative review completed.
- [ ] 2-hour soak passes.
- [ ] Draft PR remains unmerged until live validation is reviewed.

## Borrow / Keep / Reject implementation map

| Area | Borrow | Keep ours | Reject |
|---|---|---|---|
| Query strategy | Vane bounded quality fan-out; LDR strategy routing | fixed task catalogue + attempt feedback | arbitrary long Boolean queries |
| Retrieval | GPT Researcher chunk compression concept | exact source ranges + single-pass evidence | LLM curator on every result |
| Search pacing | LearningCircuit adaptive pacing concept | v0.5 host cooldown / failure isolation | unbounded retry/exploration |
| URL dedup | LearningCircuit canonical identity + distinct excerpt concept | body hash + provenance | URL-only dedup that loses excerpts |
| Modes | Vane speed/balanced/quality idea | our budgets/provenance invariants | “quality = just more calls” |
| Knowledge reuse | semantic retrieval concept | explicit reference projects, no transitive reuse | global automatic evidence reuse |
| Reporting | benchmark/citation-grounding methodology | handoff-first evidence bundle | replacing PM with final-report generator |
| UI | simple mode selector / metrics | Windows Tkinter PM | full Next.js/Docker migration |

## Self-review

### Spec coverage
- External-project comparison: captured in Borrow/Keep/Reject map and Tasks 2, 4, 7, 10, 11.
- Search-query optimization: Task 2.
- Source/chunk relevance optimization: Tasks 3-6.
- Access/rate optimization: Task 7.
- Single-pass/provenance preservation: Tasks 5, 8, 9, 13.
- Reference project optimization: Task 6.
- Optimization modes: Task 10.
- Semantic ranking only if measured: Task 11.
- Metrics/handoff: Tasks 1 and 12.
- Deterministic replay and live A/B: Tasks 1, 13, 14.
- Legacy safety: Tasks 6, 10, 13.
No spec requirement is intentionally uncovered.

### Type consistency
- `ResearchIntent` is defined in Task 2 and consumed by Tasks 6 and 8.
- `SourceChunk` is defined in Task 3 and consumed by Task 4.
- `RankedChunk` and `SemanticReranker` are defined in Task 4 and extended in Task 11.
- `AdaptivePacer` is defined in Task 7 and consumed by Task 8.
- `EngineV06` is created only after SearchCycleV06 exists.
- `continue_as_v06` creates engine_version 6 and never mutates source projects.

### Placeholder scan
The implementation sequence contains no TBD/TODO/“implement later” placeholder. Optional semantic activation has an explicit measurable gate and a defined safe fallback.

## Execution recommendation

Implement in this order without skipping the benchmark task:

```text
1 benchmark
→ 2 query policy
→ 3 chunking
→ 4 lexical retrieval
→ 5 fetch quality / identity
→ 6 reference filtering
→ 7 adaptive pacing
→ 8 integrated search cycle
→ 9 EngineV06 stop policy
→ 10 modes / continuation
→ 11 semantic experiment
→ 12 UI/export metrics
→ 13 synthetic acceptance
→ 14 live A/B + soak
```

The most important rule for v0.6 is: **if an optimization cannot beat the baseline under replay or live measurement, do not keep it merely because another GitHub project uses it.**
