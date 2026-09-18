# Project-isolated research collector

Approved by the user in the recovered 2026-09-18 conversation. The final decision
supersedes the earlier shared-database proposal.

## Invariants

- The desktop application creates one SQLite database and document/log/handoff
  directory per project. New projects have no references by default.
- Only explicitly selected reference projects are searched. Reference data is
  copied as a provenance-bearing candidate, never automatically accepted as fact.
- Source policy, original topic and user instructions are preserved at every stage.
  Planner criteria are suggestions, not newly invented mandatory requirements.
- A fetched original is retained even when extraction or review fails.
- Quotes must match the actual text supplied to the extractor; source offsets and
  PDF page locations remain recoverable. Model-generated quotes are not trusted.
- Review status is metadata, not a deletion gate. Single-source and conflicting
  information are retained and clearly labelled. Strict final filtering is opt-in.
- One failed document, malformed model response or failed critic cannot stop
  unrelated work. Storage failures stop safely. Budgets and pause/stop persist.
- Processing history is keyed by question, document version, range and extractor
  version. Retries need a changed input/strategy and are bounded.
- Exports are self-contained: handoff.md, evidence.json, sources.csv,
  unresolved.md, manifest.json and documents/. Candidate material is distinguished.
- Existing mixed databases are backed up with SQLite's online backup API. Only
  explicit historical links are migrated; unassignable material stays in backup.
  Old evidence is marked unreviewed, not silently re-certified.
- No automatic reference traversal, shared knowledge DB, OCR, domain-specific
  rules, paid API dependency, or server deployment is introduced.

## Compatibility and validation

Python >=3.10, Windows Tk desktop, Ollama and SQLite remain the baseline. Public
source network restrictions and domain policies remain in force. Text PDFs are
supported; scans and complex tables remain visibly unverified. Offline fixtures
measure software behaviour, not real-world research accuracy or laptop throughput.
