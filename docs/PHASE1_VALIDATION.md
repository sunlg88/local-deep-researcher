# Research PM phase 1: validation and limitations

Base: sunlg88/local-deep-researcher at a53b13c7022bb1352dc1ca994d07ade3cd3bd62e.
The original graph and search adapters are preserved. The desktop PM is a separate
entrypoint inside the existing package; it does not replace the LangGraph graph.

## Verification performed

Environment: Linux, CPython 3.13.5, Xvfb virtual display.
Command: `xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v`
Result: **52 tests passed, 0 failures, 0 skips**.
All new Python files also passed compileall and Python 3.11 grammar parsing.
The Korean GUI was constructed, refreshed, captured and visually inspected.

Test coverage includes quote rejection, source identity, mirrored-body deduplication,
conflict detection, immutable task criteria, unknown evidence IDs, missing citations,
persisted budgets, pause/resume, reopening SQLite, per-task synthesis, preservation
of older evidence, open/preferred/allowlist source policies, allowlisted-domain
coverage, and cancellation of an actual child process. A local HTTP server exercised Ollama NDJSON transport and model discovery.

The model and search outputs in these tests are SYNTHETIC FIXTURES. Tests do not
prove Qwen research accuracy, Windows compatibility, live web access, throughput,
24-hour reliability, or equivalence to any number of human researchers. No real
Qwen inference or paid API was used in this environment. No independent human or
second-model review was performed. The original full application dependencies were
not installed here; upstream adapter integration is an explicit deployment test.

## Operational contracts

Five sequential roles use one model, not five GPU-resident models: planner,
researcher, extractor, critic and writer. Passing work is not repeated; failing
work receives specific unmet criteria and bounded retries. This is orchestration,
not reinforcement learning or a claim that emotional pressure improves the model.

SQLite stores source text, URLs, collection timestamps, content hashes, claims,
values, units, periods and scope. QUOTE_CHECKED means a normalized quotation exists
in the downloaded text, NOT that the claim is true. REVIEW_PASSED is an automated
review result, NOT independent verification. COMPLETED_REVIEW_REQUIRED still requires
human review. Source group diversity is a heuristic, not proof of independence.

Pause/stop is cooperative. Pending network reads can wait for a timeout. Closing
an Ollama connection does not guarantee immediate GPU cancellation. Interrupted
stages may replay after reopening; model call counters are reserved before calls.
A single OS lock prevents simultaneous local workers sharing one database.

The input budget uses UTF-8 bytes as a conservative approximation plus output and
template reserves, not an exact tokenizer. Retrieval is bounded lexical SQLite
search, not vector RAG. The complete database is not injected into model prompts.
Evidence exports retain historical task links; prompt trimming does not delete them.

## Security and scope

The model cannot run arbitrary commands. A fixed-purpose search subprocess reuses
upstream adapters and is killed on cancellation/timeout. Source URLs must be HTTP(S), without credentials, and resolve to public addresses.
The default open policy accepts public web domains; preferred mode promotes configured
domains without blocking others; allowlist mode blocks every unlisted domain. This is defense
in depth, not a complete DNS-rebinding-resistant network sandbox. Webpage content is
untrusted; prompts instruct the model not to treat it as tool or policy instructions.

HTML/plain text only. PDF/OCR, XLSX, semantic entity normalization, autonomous conflict
resolution, remote networking and startup services are NOT implemented in this phase.
HTML parsing may miss tables or important passages. The simple relevance window can
miss evidence in languages that differ from the task. Upstream empty search responses
can conflate no matches with a failed/blocked search. These remain known limitations.

Research data stays in pm_data and is git-ignored. Ignore rules are not encryption
and can be overridden by a user. Search queries leave the laptop to the chosen
provider. Tavily can incur separate costs; SearXNG needs its own running instance.
The new GUI and search child disable LangSmith tracing. Nothing uploads to GPT.

## Next validation on the user's laptop

Use a narrow topic with manually known answers. Start with the default open-web mode;
then separately exercise preferred and strict allowlist modes. Verify source quotations and values against the actual pages, record usable evidence
per hour, false claims, missing facts and manual correction time. Then test interrupted
runs and increasingly long sessions. Do not interpret the synthetic test suite as a
research productivity benchmark or deploy sensitive company data without approval.
