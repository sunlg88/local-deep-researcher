# Implementation provenance and plan execution

This implementation independently expresses the approved patterns in Research PM's existing interfaces. No third-party application or source file was copied wholesale. No dependency was added to the default installation.

Pattern references reviewed during planning:

- LearningCircuit/local-deep-research, `1d7331436a22488b989e6860852281bf2a0f3f61`: bounded adaptive pacing, source/excerpt identity and retrieval-stage separation.
- assafelovic/gpt-researcher, `6f998577d547b1e54ec662dac63583aa11e3b84b`: chunk-first context retrieval and a cheap path before a heavier curation step.
- ItzCrazyKns/Vane, `348feca3e378fb4157b217724ed508dc707f853f`: explicit efficiency/quality modes and bounded query fan-out.

These references are not an endorsement of every advertised feature in those projects. Dead settings, report-first architecture, unbounded inference fan-out and per-hit LLM curation were not imported. Research PM keeps its own project boundaries, immutable source bytes, exact quote positions, multiple task links and handoff-first workflow.

Optional adapter references:

- https://docs.ollama.com/api/embed — model/input, truncate=false, options, keep_alive, embedding vectors and timing fields.
- https://sbert.net/docs/package_reference/sentence_transformer/model.html — CPU device, local_files_only, trust_remote_code and encode.

## Approved task execution map

1. Replay/efficiency measurement: implemented, including incomplete-log warnings and hash-checked inputs.
2. Semantic intent and compiler: implemented, bounded corrections, no guessed domains or stripped constraints.
3. Structure-aware chunking: implemented with original offsets and neighbor expansion.
4. Lexical progressive retrieval: implemented; scores are reading priorities, not truth judgements.
5. Page quality and identity: implemented; reuse narrowed to exact body + compatible title after review.
6. Reference relevance: implemented, local read-only scan, explicit deferred-candidate audit.
7. Pacing: implemented; Retry-After overrides learned caps; successes reduce rather than inflate delay.
8. Search cycle: implemented with versioned error-preserving adapters and original-first commits.
9. Engine and stop policy: implemented, pending/access failures separated from zero-yield.
10. GUI and continuation: implemented; v0.5 resumes with v0.5, new v0.6 data are separate.
11. Optional semantic adapters: implemented and protocol-tested; real model comparison PENDING, default OFF.
12. Metrics/export: implemented with retrieval decisions, unread ranges, aliases, pacing and replay files.
13. Controlled acceptance/OS CI: implementation tests present; published commit's CI artifacts are the evidence of platform execution.
14. Live laptop DDG A/B and soak: PENDING. No fabricated live measurements.

Local commits were made at verified component checkpoints. Publication may squash them into a reviewable candidate commit. Main and the v0.5 branch are not automatically merged.
