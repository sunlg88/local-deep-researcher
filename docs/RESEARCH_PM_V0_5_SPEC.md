# Research PM 0.5 Design Spec

Date: 2026-09-18  
Baseline: Research PM 0.4.1, branch `feature/research-pm-phase1`.

## 1. Goal

0.5 changes the optimization target from **stable source collection** to **high-yield, low-duplication research collection** while preserving the 0.4 source-first guarantees.

The system must continue to preserve originals, provenance, incomplete work and explicit project boundaries. It must additionally spend fewer model/search calls on obviously irrelevant search hits and avoid re-reading the same source range independently for every task.

## 2. Evidence from the 0.4.1 live run

The current live run proves the context-budget hotfix is working: extractor requests complete with `done_reason=stop`, preflight range fitting emits `CHUNK_RESIZED`, and the previous PromptBudgetError storm is absent.

However, the same run shows the next bottlenecks:

1. Many consecutive extractions return `relevance=irrelevant`, `accepted_claims=0`.
2. The same document/range is processed independently by multiple tasks.
3. A query containing `site:cn` returned unrelated Microsoft TechNet URLs, which were fetched before relevance was known.
4. The planner starts small, but researcher follow-up tasks can expand the task graph even when the newly fetched material later proves irrelevant.
5. The Qwen input estimator remains intentionally conservative; examples near the 4864-token estimated budget used only roughly 2200 observed prompt tokens, creating extra source splits.

## 3. Non-negotiable invariants

- Project-local SQLite, originals, evidence and handoff remain physically isolated.
- Reference projects remain explicit opt-in only; no transitive automatic reuse.
- No source, quote or failed document is deleted because a model rejects it.
- No incomplete model JSON is accepted as a completed result.
- A search or fetch failure cannot stop unrelated tasks.
- User topic/instructions are never silently shortened.
- Exact source byte/text versions and quote locations remain auditable.
- 8K context remains the default. 0.5 must improve scheduling before recommending larger context.
- No new cloud dependency is required for the default DuckDuckGo + Ollama path.

## 4. Architecture changes

### 4.1 Fixed task graph

Planner creates the task graph once. Researcher no longer creates follow-up tasks.

Adaptive research continues through new queries within existing tasks and critic `next_query` feedback. This prevents task explosion and makes the task catalogue stable enough for shared document extraction.

### 4.2 Search intent and deterministic hit screening

Each researcher response returns:

- `query`
- `strategy`: `broad | exact_entity | official_site | pdf | gap`
- `anchors`: 2-6 short entity/technical terms

The search layer parses explicit `site:` constraints from the query and enforces them on result hosts before fetch.

Every hit receives a deterministic score from URL/title/snippet overlap, preferred-domain status, explicit site constraints, duplicate state and host diversity. Hard rejection is limited to objective conditions such as explicit site mismatch, invalid URL or already-known permanent failure. Weak lexical matches are ranked down, not blindly destroyed; if no strong result exists, one low-score fallback may still be fetched.

### 4.3 Search-attempt outcome feedback

Each query gets a persistent `search_attempt_id` and counters:

- returned hits
- prefilter rejected
- duplicate skipped
- fetched
- fetch failed
- documents collected
- relevant documents
- accepted claims

The next researcher call receives the previous attempt outcomes so it can change strategy instead of merely changing words.

Near-duplicate queries are blocked using normalized token similarity, not only exact string equality.

### 4.4 Project-level single-pass extraction

Tasks are search branches, not independent copies of the source corpus.

A document range is extracted once for the project. The extractor receives the original topic/instructions and the fixed task catalogue. Every returned claim includes `task_ids` identifying which planned tasks it supports.

A new `evidence_tasks` table links one evidence record to one or more tasks. The same quote/value is stored once even if it supports several tasks.

The processing key becomes project-level:

`document version + project question + task-catalog digest + source range + extractor version`

It no longer contains an individual task title.

### 4.5 Search/fetch host health

- 404/410 remain URL-specific permanent failures.
- Repeated 403/451 for the same host create a project-local host cooldown.
- Host cooldown is advisory for later search results and is logged; originals already collected are untouched.
- Search results are diversified by host so a single domain cannot consume the entire fetch quota.

### 4.6 Bidirectional budget calibration

0.4.1 only increases token-estimate scale when actual usage exceeds the estimate.

0.5 keeps immediate upward correction but permits slow downward correction after at least three successful observations for the same role. The target remains conservative: observed/estimated ratio plus safety margin, clamped to a safe floor. The estimator must never reduce source preservation or output headroom.

### 4.7 Better observability

GUI task rows add:

- searches
- fetched documents
- relevant documents
- evidence count
- zero-yield streak

Project status adds:

- hits screened
- prefilter rejected
- duplicate fetch avoided
- irrelevant extraction count
- productive extraction ratio
- host failures

Handoff exports the same metrics and the search-hit decision trail.

### 4.8 Instruction UX

Raise additional instructions from 1200 UTF-8 bytes to 4096 UTF-8 bytes.

Before project creation, run the same fixed-prompt budget preflight used by the engine. A long instruction is accepted when it fits the configured context; otherwise the GUI explains the fixed-prompt budget problem before starting.

## 5. Backward compatibility

0.4.x projects are never rewritten in place into the new single-pass extraction model.

They remain readable, exportable and selectable as reference projects.

GUI adds **“0.5 방식으로 이어서 조사”** for a legacy project. It creates a new 0.5 project with the same topic/instructions and the legacy project as an explicit reference. This preserves the old audit trail while letting 0.5 re-use its originals safely.

New projects store `engine_version=5`.

## 6. Out of scope for 0.5

- OCR or vision extraction for scanned PDFs.
- JavaScript/browser automation or login bypass.
- Exact Qwen tokenizer embedding.
- Automatic trust claims for a domain solely because it is corporate/government/academic.
- Automatic merging of conflicting technical values.
- Multiple concurrent local-model workers on one project.
- Raising default context above 8192.

## 7. Acceptance criteria

### Search quality
- An explicit `site:cn` query must not fetch `microsoft.com`.
- Invalid/off-constraint hits are recorded with reason and skipped before network fetch.
- No more than two fetched results per host in one search attempt by default.
- Near-duplicate queries are not executed repeatedly.

### Extraction efficiency
- If eight tasks discover the same document, each source range is model-extracted once, not eight times.
- A claim may support multiple tasks through `evidence_tasks` without duplicate evidence rows.
- Original document bytes/text remain unchanged.

### Task stability
- Task count after planning remains fixed for the project.
- Critic/researcher may alter queries but cannot append tasks.

### Budget efficiency
- Upward calibration remains immediate.
- Downward calibration requires at least three successful observations and keeps a safety margin.
- No PromptBudgetError recursion or silent truncation.

### Recovery
- Pause/stop/restart preserves search attempts, project document queue, extraction work and evidence-task links.
- Legacy 0.4.x projects remain intact and can seed a new 0.5 project explicitly.

### Validation
- Existing 132 tests stay green unless a test intentionally changes for the new fixed-task contract.
- New 0.5 tests run on Ubuntu and Windows Python 3.11 CI.
- A 30-minute live Qwen3.5:9b test must show no budget-error storm, no duplicate full-source extraction across tasks, and no obvious explicit-site mismatch fetches.
- Live research accuracy remains a human-review item and is not certified by automated tests.
