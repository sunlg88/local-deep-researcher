# Research PM 0.5 - implementation and validation record

Date: 2026-09-18. Remote baseline: `fa4ced822a6ece3d9575e776fd51afb3200f1560`.
Status: **implementation candidate; live laptop release gates pending**.

## Implemented behavior

The desktop executes `EngineV05`, composed of `pm_search_cycle.py` and
`pm_single_pass.py`, while the old `Engine` remains a regression reference and
lifecycle superclass. The new-project path uses explicitly versioned model
contracts. Existing 0.4.x databases remain read-only.

- Planner produces a fixed task catalogue; Researcher returns query, strategy
  and anchors, not new tasks. A saved catalogue digest detects mutation.
- Search results are normalized and ranked before fetch. Positive/negative
  site constraints and source policy are checked again before redirects.
  Multiple positive site terms are treated as alternate hosts, not a complete
  Boolean query parser. Weak leads are retained in the audit trail; at most one
  weak fallback is scheduled when no positive lexical lead exists.
- Domains are diversified with at most two fetched leads per host/search.
  Word-order duplicate queries are suppressed, but changed dates, exclusions,
  quoted phrases, domains and new non-neutral terms are not silently discarded.
- Persistent search attempts/hit decisions retain the exact backend query,
  scores, reasons, failures, deferred leads and source associations. Interrupted
  saved result batches do not repeat completed downloads.
- Two 403/451 responses within ten minutes trigger a 15-minute project-local
  host cooldown. 404/410 are URL-specific. No login/access restrictions are
  bypassed. A domain or lexical score is not proof of truth or official status.
- Each source range is extracted once for the fixed project/catalogue/model
  version, instead of separately for each task. One evidence record may link
  to several tasks. Task-specific review results remain separate.
- A completed model response is persisted as RESULT_READY before writing
  evidence/task links. Interrupted writes replay that result without a new
  model call. A crash before a network response is persisted can still require
  a repeat call; this is not a universal exactly-once delivery guarantee.
- Estimates are calibrated by model, role and script group. Downward changes
  require at least three successful measurements, retain a 35% margin, move
  at most 10% per observation and have a 0.65 floor. Underestimates are raised
  immediately. Unknown models keep the conservative upper-bound path.
- Default Context remains 8192 and global Output cap 3072. Planner/search/
  extraction use non-thinking requests. User input is not silently truncated.
- Additional instructions allow 4096 UTF-8 bytes, subject to fixed-input
  preflight before creation and after the actual catalogue becomes known.
- GUI and handoff distinguish fetched/relevant/evidence counts, zero yield,
  pending extraction, failures and productive extraction ratio.
- Handoff contains search_attempts.csv/json, search_hits.csv/json,
  research_metrics.json, host_health.json and evidence/task/review mappings,
  in addition to unchanged original files, quotations and hash manifests.
- The legacy continuation button creates a NEW 0.5 project with the original
  topic/instructions and an explicit reference to the old project. Legacy
  export writes outside the old folder to pm_data/legacy_exports/<id>/.

## Reproducible validation

Baseline: 132 tests passed on the unmodified source snapshot.
Current implementation: **190 tests passed**, zero failures and zero skips,
including the original 132 tests and 58 added v0.5 cases.

```sh
xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src/ollama_deep_researcher
```

New tests exercise the actual v0.5 engine. Coverage includes site/redirect
boundaries, host diversity, weak fallbacks, durable search outcomes, zero-yield
versus pending/failed extraction, cooldown, fixed tasks, shared claims,
task-specific reviews, original-tail coverage, cached-response replay,
calibration, input preflight, Tk GUI, legacy immutability and complete exports.

The full-pipeline test uses the production Ollama HTTP transport against a
real local streaming HTTP server with SYNTHETIC replies. Search results and
research content are SYNTHETIC fixtures, not live Qwen or public search results.

In the eight-task one-range fixture, measured results are one source fetch,
one extraction call, one evidence row and eight task links. This does not
establish an eightfold end-to-end speedup: search and review calls still exist.
The mixed Korean/English long-source fixture separately checks that the final
source claim survives, context stays 8192, and no budget-error storm appears.

Implementation was developed with failing regressions before feature changes.
Final inline review also corrected stale usage reuse after preflight failure
and overly broad duplicate suppression of a different unquoted entity.
No independent reviewer approval or full security audit is claimed.

The update ZIP is tested by overlaying it onto a separate baseline directory
and running the entire suite. Published commit CI provides Ubuntu/Windows
installation, tests and compile results. Published source should be downloaded
back and compared against the locally tested file hashes before delivery.

## Metric definitions and remaining limits

- Fetch counts are logical hit-fetch slots, not wire-level requests; redirects
  and interrupted retries can involve more than one network request.
- Attempt/task yields attribute shared documents/evidence to searches; do not
  add those numbers to obtain project unique totals.
- Productive extraction ratio is adopted-claim ranges divided by completed
  ranges, not factual accuracy or complete coverage of user requirements.
- Pending extraction is not zero yield; failed extraction has its own label.
- Deduplication is scoped to known document IDs/versions, not semantic equality
  across separate URLs or re-parsed versions. All originals remain available.
- One extraction returns at most three claims (one on compact recovery). It is
  not a guarantee of exhaustive fact extraction from every source range.
- A domain may be used because it was observed or specified, but corporate or
  government appearance is not automatically certified as trustworthy.
- Fixed-prompt preflight may refuse a 4096-byte instruction when its configured
  context/output/catalogue combination cannot fit safely.
- OCR, image/complex-table understanding, browser rendering, login bypass,
  exact model tokenization and multi-GPU workers are out of scope.

## Pending release gates

- [ ] Actual user Windows laptop / RTX 4060 / Qwen3.5:9b, 30-minute run.
- [ ] Human audit of useful sources, false exclusions and extracted quotations.
- [ ] Live external search/provider availability and real research yield.
- [ ] Two-hour soak with pause/restart and no state corruption.
- [ ] Eight-hour / 24-hour tests only after shorter gates pass.

Keep PR #1 in draft and leave main unchanged until the live gates are reviewed.
Hosted Windows CI is not the user's GPU laptop. Passing these tests does not
certify research accuracy, speed or unattended long-duration reliability.
