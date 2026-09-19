# Research PM0.6.2 reliability implementation plan

> Use superpowers:executing-plans and test-driven-development. Check off only observed results.

**Goal:** Repair retry growth and negative-reading loops, expose the real search provider, and test beyond synthetic responses.
**Architecture:** Persistent original/audit state is distinct from bounded model views. Search targets compile to separate queries. Exploration and continued reading use different criteria. Explicit maintained DDG engine. Keep engine_version6 and prior databases.
**Tech stack:** Python3.11+, Tkinter, SQLite, Ollama JSON, ddgs9.16.0, unittest, Windows/Linux CI.
**Spec:** RESEARCH_PM_V0_6_2_RELIABILITY.md and the user's0.6.1 failure log.

## Constraints
Retain exact question/instructions, originals, quotations, offsets and completed evidence. No additional GPU model/context increase. No guessed domain/hidden provider fallback. Main/v0.5 untouched. No private project uploads. Tests and factual accuracy are different.

## Tasks
- [x] pm_context_v062: project optional history before every request; use transport renderer; repair must actually be smaller. Tests: huge history, immutable core,50-cycle growth, corrective HTTP request.
- [x] pm_query_v062: separate search_phrases plus constraints, target-first durable queue, reuse before new model calls, old qualifier preservation. Tests:3searches/1researcher, restart, operators/domains, persisted proposal.
- [x] pm_single_pass_v06: topic-only signal does not override negative evidence; rotate document ranges fairly. Tests: ASMEguide1call, retained tails/other-task entity, queue/recovery.
- [x] explicit maintained search worker: pinned engine/endpoint and provider metadata, early dependency diagnostic, no hidden alternate provider. Tests: mismatch, metadata, missing dependency, non-200 and challenge handling.
- [x] actual CPU test found60s hidden cap; honour configured timeout in v0.6, retain v0.5 policy; regression boundary tests.
- [x] local328tests and compileall.
- [x] actual public search3queries and fetched bodies; original failed trial retained and distinguished.
- [ ] read final actual-model artifact and final Windows/Linux CI; no inferred passes.
- [ ] verify published source and overlay ZIP; keep pm_data/.pm-venv markers.

External patterns: LangChain before-model trimming, GPT Researcher bounded retrieval context, official Ollama structured outputs, deedy5/ddgs version-pinned engine selection and HTTP parsing. Independent review, target GPU soak and source accuracy remain separate gates.
