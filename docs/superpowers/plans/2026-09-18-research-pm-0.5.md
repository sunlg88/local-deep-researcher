# Research PM 0.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Research PM from stable source collection to high-yield, low-duplication research collection without weakening source preservation, auditability, project isolation or resumability.

**Architecture:** Keep the existing project-local source-first architecture, but make search attempts explicit objects, screen search hits deterministically before fetch, and extract each document range once per project instead of once per task. Tasks become a fixed planning/search taxonomy; claims are stored once and linked to one or more tasks. Budget calibration, observability and legacy migration are layered on top of those contracts.

**Tech Stack:** Python 3.11+, Tkinter, SQLite, Ollama HTTP API, existing DuckDuckGo/SearXNG/Tavily adapters, unittest, GitHub Actions Windows + Ubuntu.

**Spec:** `docs/RESEARCH_PM_V0_5_SPEC.md`

## Global Constraints

- Default context remains 8192.
- No new cloud dependency is required for the default DuckDuckGo + Ollama path.
- Project-local SQLite, originals, evidence and handoff remain physically isolated.
- Reference projects remain explicit opt-in only; no transitive automatic reuse.
- No source, quote or failed document is deleted because a model rejects it.
- No incomplete model JSON is accepted as a completed result.
- User topic/instructions are never silently shortened.
- Exact source byte/text versions and quote locations remain auditable.
- 0.4.x projects are not rewritten in place; they remain readable/exportable/referenceable.
- All behavior changes use TDD: failing test first, then minimal implementation.
- Every task below ends with a focused commit and a reviewer gate.

---

## File map

### New modules

- `src/ollama_deep_researcher/pm_search_quality.py`
  - Parses site constraints.
  - Normalizes queries.
  - Scores/ranks hits.
  - Enforces objective hard rejects.
  - Applies host diversity and near-duplicate query detection.

- `src/ollama_deep_researcher/pm_research_metrics.py`
  - Defines persistent search-attempt outcome records.
  - Computes task/project yield metrics.
  - Produces compact feedback payloads for the Researcher and GUI.

### Existing modules to modify

- `pm_prompts.py`
  - Fixed task graph contract.
  - Researcher returns query strategy + anchors, never follow-up tasks.
  - Extractor returns claims with `task_ids`.

- `pm_types.py`
  - Search strategy validation.
  - 4096-byte additional-instruction limit.
  - 0.5 settings for host diversity/cooldown if needed.

- `pm_engine.py`
  - Fixed task count.
  - Search attempt lifecycle.
  - Search hit screening.
  - Project-level document queue.
  - Single-pass extraction.
  - Evidence-to-task linking.
  - Search outcome feedback.
  - Host cooldown integration.

- `pm_store.py`
  - `search_attempts` table.
  - `search_hits` table or equivalent JSON-backed records.
  - `evidence_tasks` table.
  - Project-level extraction checkpoints.
  - Aggregated metrics queries.

- `pm_io.py`
  - Search result metadata normalization.
  - No semantic LLM filtering here; deterministic network boundary only.

- `pm_budget.py`
  - Bidirectional calibration with safe floor/ceiling and minimum sample count.

- `pm_gui.py`
  - 0.5 version/labels.
  - Search/fetch/yield metrics.
  - 4096-byte instruction counter and preflight.
  - Legacy “0.5 방식으로 이어서 조사” action.

- `pm_export.py`
  - Search-attempt trail.
  - Screening reasons.
  - evidence-task mappings.
  - Productivity metrics.

- `pm_projects.py`
  - New-project-from-legacy-reference helper.
  - engine_version=5 persistence.

### Tests

- Create: `tests/test_pm_search_quality.py`
- Create: `tests/test_pm_single_pass.py`
- Create: `tests/test_pm_search_attempts.py`
- Create: `tests/test_pm_v05_gui.py`
- Modify: `tests/test_pm_collector_flow.py`
- Modify: `tests/test_pm_budget.py`
- Modify: `tests/test_pm_projects.py`
- Modify: `.github/workflows/pm-tests.yml` only if required for new live/synthetic fixtures; Windows + Ubuntu matrix remains mandatory.

---

### Task 1: Freeze the task graph and update model contracts

**Files:**
- Modify: `src/ollama_deep_researcher/pm_prompts.py`
- Modify: `src/ollama_deep_researcher/pm_engine.py`
- Test: `tests/test_pm_collector_flow.py`

**Interfaces:**
- Researcher output:
  - `query: str`
  - `strategy: "broad" | "exact_entity" | "official_site" | "pdf" | "gap"`
  - `anchors: list[str]` length 2..6
- Researcher must not return `followups`.
- Planner task list is immutable after planning.

- [ ] **Step 1: Write failing tests**

Add tests proving:

```python
def test_researcher_cannot_append_followup_tasks():
    # run plan + multiple researcher cycles
    # assert task IDs/titles are unchanged after plan

def test_researcher_contract_returns_strategy_and_anchors():
    # inspect outbound schema and accepted fixture
    # assert query/strategy/anchors only
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_pm_collector_flow -v
```

Expected: failures because current Researcher schema still allows `followups` and engine appends tasks.

- [ ] **Step 3: Change schemas/prompts minimally**

Update `PROMPTS['researcher']` to request strategy and anchors. Remove follow-up task generation from `Engine.research()`.

- [ ] **Step 4: Run focused tests and full regression**

```bash
python -m unittest tests.test_pm_collector_flow -v
python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_prompts.py src/ollama_deep_researcher/pm_engine.py tests/test_pm_collector_flow.py
git commit -m "feat(pm): freeze v0.5 research task graph"
```

---

### Task 2: Add deterministic search-query and hit screening

**Files:**
- Create: `src/ollama_deep_researcher/pm_search_quality.py`
- Modify: `src/ollama_deep_researcher/pm_types.py`
- Test: `tests/test_pm_search_quality.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SearchIntent:
    query: str
    strategy: str
    anchors: tuple[str, ...]
    explicit_sites: tuple[str, ...]

@dataclass(frozen=True)
class HitDecision:
    accepted: bool
    score: float
    reasons: tuple[str, ...]
    host: str

def parse_search_intent(query: str, strategy: str, anchors: list[str]) -> SearchIntent: ...
def score_hit(intent: SearchIntent, hit: dict, settings: Settings) -> HitDecision: ...
def diversify_hits(rows: list[tuple[dict, HitDecision]], per_host: int = 2) -> list[tuple[dict, HitDecision]]: ...
def query_signature(query: str) -> frozenset[str]: ...
def near_duplicate_query(a: str, b: str, threshold: float = 0.82) -> bool: ...
```

- [ ] **Step 1: Write failing tests**

Required cases:

```python
def test_explicit_site_cn_rejects_microsoft_before_fetch():
    intent = parse_search_intent('Entity site:cn', 'official_site', ['Entity'])
    decision = score_hit(intent, {'url':'https://microsoft.com/x','title':'x','content':'x'}, Settings())
    assert decision.accepted is False
    assert 'SITE_MISMATCH' in decision.reasons

def test_weak_anchor_match_is_ranked_not_hard_deleted():
    ...

def test_host_diversity_caps_two_per_host():
    ...

def test_near_duplicate_query_detects_word_order_changes():
    ...
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_pm_search_quality -v
```

- [ ] **Step 3: Implement lexical scoring**

Use only deterministic data already returned by the search backend:
- URL host/path
- title
- snippet/content if present
- preferred-domain membership
- explicit `site:` constraints
- strategy and anchors

Hard reject only:
- invalid URL
- explicit site mismatch
- policy disallow
- known permanent URL failure

Everything else receives a score and reason trail.

- [ ] **Step 4: Verify GREEN + regression**

```bash
python -m unittest tests.test_pm_search_quality -v
python -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_search_quality.py src/ollama_deep_researcher/pm_types.py tests/test_pm_search_quality.py
git commit -m "feat(pm): screen and diversify search hits"
```

---

### Task 3: Persist search attempts and yield feedback

**Files:**
- Create: `src/ollama_deep_researcher/pm_research_metrics.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Test: `tests/test_pm_search_attempts.py`

**Interfaces:**

```python
def begin_search_attempt(store, project_id: str, task_id: str, query: str,
                         strategy: str, anchors: list[str]) -> str: ...

def record_hit_decision(store, attempt_id: str, hit: dict, decision: HitDecision) -> None: ...

def finish_search_attempt(store, attempt_id: str, **counts: int) -> None: ...

def task_search_feedback(store, task_id: str, limit: int = 3) -> list[dict]: ...
```

Persistent counters:
- returned_hits
- prefilter_rejected
- duplicate_skipped
- fetched
- fetch_failed
- documents_collected
- relevant_documents
- accepted_claims

- [ ] **Step 1: Write failing persistence/reopen tests**

```python
def test_search_attempt_survives_reopen():
    ...

def test_hit_decision_reason_is_auditable():
    ...

def test_task_feedback_returns_zero_yield_attempts():
    ...
```

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Add tables and metric helpers**

Prefer normalized SQLite rows for search attempts/hit decisions because these are high-volume and queryable. Keep bounded text fields.

- [ ] **Step 4: Reopen DB and verify metrics survive restart**

Run:

```bash
python -m unittest tests.test_pm_search_attempts -v
```

- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_research_metrics.py src/ollama_deep_researcher/pm_store.py tests/test_pm_search_attempts.py
git commit -m "feat(pm): persist search attempt yield metrics"
```

---

### Task 4: Integrate screened search into Engine.research()

**Files:**
- Modify: `src/ollama_deep_researcher/pm_engine.py`
- Modify: `src/ollama_deep_researcher/pm_io.py`
- Test: `tests/test_pm_search_attempts.py`
- Test: `tests/test_pm_collector_flow.py`

**Interfaces:**
- `Engine.research()` consumes `strategy`, `anchors`.
- Search attempt feedback is sent into the next Researcher prompt as `attempt_feedback`.
- Fetch quota is applied after screening/diversification.

- [ ] **Step 1: Write failing end-to-end search tests**

Required:
- `site:cn` + Microsoft hit => no fetch call.
- 5 hits from one host + 2 from others => maximum two fetched from first host.
- same/near-identical query returned again => search adapter not called.
- first strategy produces zero yield => next Researcher payload contains zero-yield feedback.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement integration**

Order must be:

```text
Researcher
→ normalize intent
→ search backend
→ persistent hit decisions
→ objective hard rejects
→ score/rank
→ host diversity
→ known-URL dedupe/cooldown
→ fetch
→ collect document
→ finish search attempt
```

- [ ] **Step 4: Full regression**

- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_engine.py src/ollama_deep_researcher/pm_io.py tests/test_pm_search_attempts.py tests/test_pm_collector_flow.py
git commit -m "feat(pm): integrate yield-aware screened search"
```

---

### Task 5: Add host health without overblocking

**Files:**
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Modify: `src/ollama_deep_researcher/pm_engine.py`
- Test: `tests/test_pm_search_attempts.py`

**Interfaces:**

```python
def host_health(store, host: str) -> dict: ...
def record_host_failure(store, host: str, http_code: int | None) -> None: ...
def host_in_cooldown(store, host: str, now_ts: float) -> bool: ...
```

Rules:
- 404/410: URL-specific permanent only.
- 403/451: after repeated failures on same host, project-local cooldown.
- 5xx/timeouts: retryable, do not permanent-block host.
- Previously collected documents remain usable.

- [ ] **Step 1: Write failing tests for 403 host cooldown and 404 URL-only behavior**
- [ ] **Step 2: Verify RED**
- [ ] **Step 3: Implement minimal host-health persistence**
- [ ] **Step 4: Verify GREEN + full suite**
- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_store.py src/ollama_deep_researcher/pm_engine.py tests/test_pm_search_attempts.py
git commit -m "feat(pm): add bounded host health tracking"
```

---

### Task 6: Introduce project-level single-pass source extraction

**Files:**
- Modify: `src/ollama_deep_researcher/pm_prompts.py`
- Modify: `src/ollama_deep_researcher/pm_engine.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Create: `tests/test_pm_single_pass.py`

**Interfaces:**

Extractor payload:

```python
{
  "task_catalog": [
    {"id":"t001","title":"...","criteria":[...]},
    ...
  ],
  "source_url": "...",
  "source_text": "...",
  "source_range": {"start": 0, "end": 3500},
  "max_claims": 3
}
```

Each claim adds:

```python
"task_ids": ["t001", "t004"]
```

Store API:

```python
def link_evidence_task(eid: str, task_id: str) -> None: ...
def evidence_task_ids(eid: str) -> list[str]: ...
def task_evidence_ids(task_id: str) -> list[str]: ...
```

- [ ] **Step 1: Write failing schema/store tests**

Required:
- extractor rejects unknown task IDs.
- one evidence row can link to three tasks.
- task queries retrieve linked evidence without cloning evidence rows.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Add `evidence_tasks` table and APIs**

Table:

```sql
CREATE TABLE evidence_tasks(
    evidence_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY(evidence_id, task_id)
);
```

- [ ] **Step 4: Change extractor contract**

Task catalogue is fixed and bounded. Extractor may return an empty `task_ids` list only when relevance is uncertain; such material is retained as unassigned/unconfirmed, not promoted as task evidence.

- [ ] **Step 5: Verify GREEN**

```bash
python -m unittest tests.test_pm_single_pass -v
```

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_prompts.py src/ollama_deep_researcher/pm_engine.py src/ollama_deep_researcher/pm_store.py tests/test_pm_single_pass.py
git commit -m "feat(pm): link single-pass evidence to multiple tasks"
```

---

### Task 7: Replace task-local chunk queues with a project document queue

**Files:**
- Modify: `src/ollama_deep_researcher/pm_engine.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Test: `tests/test_pm_single_pass.py`
- Test: `tests/test_pm_collector_flow.py`

**Interfaces:**
- Project state adds `document_queue`.
- Processing key excludes task title and includes:
  - document ID/version
  - project question digest
  - task-catalog digest
  - start/end
  - extractor version

- [ ] **Step 1: Write failing duplicate-extraction test**

```python
def test_same_document_discovered_by_eight_tasks_is_extracted_once_per_range():
    # all tasks discover same document
    # assert extractor calls equal number of source ranges, not ranges * tasks
```

Also test:
- crash between SPLIT checkpoint and state save;
- pause/reopen preserves unread tail;
- one range can produce task links for multiple tasks.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement project queue**

Do not delete task `document_ids`; keep them for search provenance. They no longer own extraction queues.

- [ ] **Step 4: Update Critic scheduling**

Critic still reviews per task, but task evidence comes from `evidence_tasks`.

- [ ] **Step 5: Run focused + full suites**

- [ ] **Step 6: Commit**

```bash
git add src/ollama_deep_researcher/pm_engine.py src/ollama_deep_researcher/pm_store.py tests/test_pm_single_pass.py tests/test_pm_collector_flow.py
git commit -m "feat(pm): extract each source range once per project"
```

---

### Task 8: Add safe downward token-budget calibration

**Files:**
- Modify: `src/ollama_deep_researcher/pm_budget.py`
- Modify: `src/ollama_deep_researcher/pm_engine.py`
- Test: `tests/test_pm_budget.py`

**Interfaces:**

Persist per-role calibration stats:

```python
{
  "scale": 1.0,
  "samples": 0,
  "max_observed_ratio": 0.0,
  "recent_ratios": [...]
}
```

Rules:
- actual > estimate => upward correction immediately.
- downward correction only after >=3 successful samples.
- new scale target = conservative percentile/maximum recent ratio * safety factor.
- never below configured safe floor.
- never use truncated/length-failed samples for downward correction.

- [ ] **Step 1: Write failing tests**

Required:
- 3 over-conservative successful samples lower scale.
- 1 sample does not.
- a later under-estimate immediately raises scale.
- output headroom is unchanged.
- source tails remain preserved.

- [ ] **Step 2: Verify RED**
- [ ] **Step 3: Implement minimal calibration state**
- [ ] **Step 4: Run budget suite + full regression**
- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_budget.py src/ollama_deep_researcher/pm_engine.py tests/test_pm_budget.py
git commit -m "perf(pm): calibrate conservative token estimates safely"
```

---

### Task 9: Improve instruction UX and preflight

**Files:**
- Modify: `src/ollama_deep_researcher/pm_types.py`
- Modify: `src/ollama_deep_researcher/pm_gui.py`
- Test: `tests/test_pm_v05_gui.py`

**Interfaces:**
- `MAX_INSTRUCTION_BYTES = 4096`.
- New helper:

```python
def preflight_research_start(settings: Settings, topic: str, instructions: str) -> dict:
    # planner/researcher fixed-prompt budget check only
```

- [ ] **Step 1: Write failing tests**

Required:
- 1201-byte instructions accepted.
- <=4096 accepted if fixed prompt fits.
- >4096 rejected by storage guard.
- <=4096 but incompatible context/output rejected before project creation with budget-specific message.

- [ ] **Step 2: Verify RED**
- [ ] **Step 3: Implement limit/preflight**
- [ ] **Step 4: Verify GUI creates no project on failed preflight**
- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_types.py src/ollama_deep_researcher/pm_gui.py tests/test_pm_v05_gui.py
git commit -m "feat(pm): expand research instructions with budget preflight"
```

---

### Task 10: Add 0.5 GUI productivity metrics

**Files:**
- Modify: `src/ollama_deep_researcher/pm_gui.py`
- Modify: `src/ollama_deep_researcher/pm_research_metrics.py`
- Test: `tests/test_pm_v05_gui.py`

**Interfaces:**

Task table columns:
- task
- state
- searches
- fetched
- relevant
- evidence
- zero_yield

Project status:
- hits screened
- prefilter rejected
- duplicate fetch avoided
- irrelevant extraction
- productive extraction ratio
- host failures

- [ ] **Step 1: Write failing GUI metric tests**
- [ ] **Step 2: Verify RED under Xvfb/Tk**
- [ ] **Step 3: Implement bounded metric refresh**
- [ ] **Step 4: Verify no full-table expensive scan every 1 second; metrics helper returns aggregates in bounded queries**
- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_gui.py src/ollama_deep_researcher/pm_research_metrics.py tests/test_pm_v05_gui.py
git commit -m "feat(pm): surface research yield in desktop UI"
```

---

### Task 11: Export search decisions and task mappings

**Files:**
- Modify: `src/ollama_deep_researcher/pm_export.py`
- Test: `tests/test_pm_delivery.py`
- Test: `tests/test_pm_search_attempts.py`

**Interfaces:**

Add exports:
- `search_attempts.csv`
- `search_hits.csv`
- evidence JSON includes `task_ids`
- manifest includes yield metrics

`unresolved.md` adds:
- zero-yield tasks
- inaccessible/cooldown hosts
- high-rejection search attempts
- unassigned claims

- [ ] **Step 1: Write failing export tests**
- [ ] **Step 2: Verify RED**
- [ ] **Step 3: Implement CSV/JSON/markdown exports with formula escaping preserved**
- [ ] **Step 4: Verify generated handoff can be read without DB access**
- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_export.py tests/test_pm_delivery.py tests/test_pm_search_attempts.py
git commit -m "feat(pm): export search quality and evidence-task provenance"
```

---

### Task 12: Preserve legacy 0.4.x and add explicit 0.5 continuation

**Files:**
- Modify: `src/ollama_deep_researcher/pm_projects.py`
- Modify: `src/ollama_deep_researcher/pm_store.py`
- Modify: `src/ollama_deep_researcher/pm_gui.py`
- Test: `tests/test_pm_projects.py`
- Test: `tests/test_pm_v05_gui.py`

**Interfaces:**

New project state:

```python
"engine_version": 5
```

New Workspace method:

```python
def continue_as_v05(self, legacy_project_id: str, settings: Settings | None = None) -> str:
    # creates a NEW project
    # copies topic/instructions
    # explicit reference_projects=[legacy_project_id]
    # does not mutate legacy DB
```

- [ ] **Step 1: Write failing immutability tests**
- [ ] **Step 2: Verify RED**
- [ ] **Step 3: Implement helper + GUI action**
- [ ] **Step 4: Hash legacy DB/originals before/after and assert unchanged**
- [ ] **Step 5: Commit**

```bash
git add src/ollama_deep_researcher/pm_projects.py src/ollama_deep_researcher/pm_store.py src/ollama_deep_researcher/pm_gui.py tests/test_pm_projects.py tests/test_pm_v05_gui.py
git commit -m "feat(pm): continue legacy research as isolated v0.5 project"
```

---

### Task 13: 0.5 end-to-end synthetic acceptance suite

**Files:**
- Create: `tests/test_pm_v05_acceptance.py`
- Modify: `.github/workflows/pm-tests.yml` only if needed for test duration/artifacts.

**Acceptance fixture:**

Create synthetic search results containing:
- relevant official source
- unrelated Microsoft source for a `site:cn` query
- five duplicate-host pages
- one 403 host
- same document returned to eight tasks
- one claim supporting three tasks
- long Korean/English mixed source requiring preflight chunking

- [ ] **Step 1: Write acceptance test before any final refactor**

Assertions:
- Microsoft mismatch never fetched.
- host diversity applied.
- repeated 403 host cooled down.
- same source range extracted once.
- task graph never grows.
- one evidence row maps to multiple tasks.
- no PromptBudgetError storm.
- pause/reopen produces same final mappings.
- originals unchanged.

- [ ] **Step 2: Run under Linux**

```bash
xvfb-run -a env PYTHONPATH=src python -m unittest tests.test_pm_v05_acceptance -v
```

- [ ] **Step 3: Run full Linux suite**

```bash
xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src/ollama_deep_researcher
```

- [ ] **Step 4: Push and require both CI jobs green**
  - Ubuntu / Python 3.11
  - Windows / Python 3.11

- [ ] **Step 5: Commit any test-only fixes separately**

```bash
git add tests/test_pm_v05_acceptance.py .github/workflows/pm-tests.yml
git commit -m "test(pm): add v0.5 quality and dedup acceptance suite"
```

---

### Task 14: Live Qwen3.5:9b laptop validation gate

**Files:**
- Create/modify: `docs/RESEARCH_PM_V0_5_VALIDATION.md`
- Modify: `RESEARCH_PM_KO.md`

**Interfaces:** None; this is the release gate.

- [ ] **Step 1: Back up current `pm_data`**

- [ ] **Step 2: Start a NEW 0.5 project using the same nuclear/SMR forging topic**

Use:
- Qwen3.5:9b
- Context 8192
- Output 3072
- default DuckDuckGo
- 30-minute first run

- [ ] **Step 3: Capture metrics**

Record:
- searches
- hits screened
- prefilter rejects
- fetched
- fetch failures
- unique documents
- extractor calls
- relevant documents
- accepted claims
- duplicate fetch avoided
- productive extraction ratio
- any budget/model errors

- [ ] **Step 4: Compare against the 0.4.1 observed failure pattern**

Required release observations:
- no PromptBudgetError storm;
- no explicit-site mismatch fetch such as `site:cn` → Microsoft;
- same document ranges are not extracted once per task;
- at least some useful claims are produced if suitable sources are actually found;
- if no claims are produced, handoff must clearly show whether the bottleneck was search quality, access failure or extractor relevance.

- [ ] **Step 5: Soak test only after 30-minute gate passes**

Run 2-hour test. Do not start 8h/24h validation until 2h completes without state corruption or repeated runaway loops.

- [ ] **Step 6: Update validation docs with factual results only**

Do not claim research accuracy from automated control-flow tests.

- [ ] **Step 7: Final release commit**

```bash
git add docs/RESEARCH_PM_V0_5_VALIDATION.md RESEARCH_PM_KO.md
git commit -m "docs(pm): record Research PM 0.5 live validation"
```

---

## Release gate

0.5 is ready for normal use only when all are true:

- [ ] Fixed task graph verified.
- [ ] Explicit site mismatch filtered before fetch.
- [ ] Near-duplicate query suppression verified.
- [ ] Host diversity verified.
- [ ] Search attempt metrics survive restart.
- [ ] Same source range extracted once per project.
- [ ] Evidence can map to multiple tasks without duplicate evidence rows.
- [ ] Original bytes/text remain immutable.
- [ ] Bidirectional budget calibration passes regression tests.
- [ ] 4096-byte instructions pass preflight rules.
- [ ] Legacy 0.4.x project is unchanged after 0.5 continuation.
- [ ] Ubuntu CI green.
- [ ] Windows CI green.
- [ ] 30-minute live Qwen3.5:9b gate completed.
- [ ] 2-hour soak completed before merge to main.
- [ ] PR remains draft until the laptop live gate is reviewed.

## Self-review

### Spec coverage

- Search mismatch and poor ranking: Tasks 2, 4, 5.
- Repeated irrelevant extraction and duplicate task processing: Tasks 6, 7.
- Search yield feedback: Tasks 3, 4, 10, 11.
- Conservative token estimate: Task 8.
- Instruction length/UX: Task 9.
- Legacy compatibility: Task 12.
- Observability/handoff: Tasks 10, 11.
- Windows/Linux regression and real laptop validation: Tasks 13, 14.

No spec requirement is intentionally deferred outside the stated 0.5 out-of-scope list.

### Type consistency

- Researcher `strategy/anchors` is introduced in Task 1 and consumed by Tasks 2/4.
- `HitDecision` is introduced in Task 2 and persisted in Task 3.
- `evidence_tasks` is introduced in Task 6 and becomes the task evidence source in Task 7.
- `engine_version=5` is introduced only for new projects in Task 12.

### Placeholder scan

No TBD/TODO/“implement later” placeholders are part of the implementation instructions.
