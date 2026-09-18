# Research PM 0.4.1 - context budget hotfix

Date: 2026-09-18. Baseline: `cf171846b2b5f821928cb82a75e4afe542249f9f`.

## Root cause and evidence

The old engine and HTTP transport independently compared UTF-8 bytes with
`num_ctx - num_predict - 768`. This was an overly restrictive byte upper-bound
policy, not tokenization. Worse, source chunks were sized in characters and
only split AFTER that gate failed. Every large chunk could therefore produce
multiple local budget errors before any model request. Planner retries reused
essentially the same plan contract, and output-length failures discarded the
server's final usage metrics. The user's log proves length-limit failures but
does not establish how many tokens were spent thinking versus writing.

The reported Ollama screen showed `CONTEXT=8192`, `PROCESSOR=100% GPU`. That
rules out the app's 4K slider being the effective context for that loaded run;
it does not certify model quality or all hardware behavior.

## Implemented behavior

- Shared request rendering and budget checks in `pm_budget.py` for engine and
  transport. Qwen uses a padded Unicode/script-sensitive heuristic; other
  models fall back to a conservative byte upper bound. These are NOT exact
  model tokenizers. A 768-token reserve is separate from the role output cap.
- Context stays at the user's saved value (default 8192), with no automatic
  VRAM/context enlargement. Default output caps under global Output=3072:
  planner 1536, researcher 1024, extractor 2560, critic 3072, writer 3072.
- Planner/researcher/extractor request `think=false`. Critic/writer honor the
  reasoning switch and use non-thinking compact retries after failure.
- Initial planning requests at most three tasks (unless a larger minimum was
  explicitly configured). A retry reduces this to the configured minimum.
  The per-request JSON schema agrees with that count.
- Source ranges are fitted BEFORE a call; both children and their original
  character offsets are checkpointed. Normal resizing is `CHUNK_RESIZED`, not
  `WORK_ERROR`. Crash replay preserves unread tails.
- Fixed question/metadata overflow produces one `INPUT_BUDGET_BLOCKED` stop,
  retaining the queue and original. It does not recursively shred documents.
- Output-limited extraction uses fewer claims on a smaller retry. Incomplete
  JSON remains rejected. A writer gets one compact retry, not unbounded output.
- Actual server `prompt_eval_count`/`eval_count`, stop reason, thinking character
  count and answer character count are retained even on length failures.
  Observed input exceeding the estimate increases subsequent project/role
  estimates. A near-context response without output headroom is rejected.
  This mitigates underestimation; it does not prove that every possible model
  template or server-side truncation is detectable.
- `MODEL_COMPLETED` means a JSON model response arrived, NOT that evidence was
  accepted. `EXTRACTION_COMPLETED` separately lists range, relevance and accepted
  claims. No reasoning text is logged by this instrumentation.
- On first resume under this policy, failed old byte-budget ranges are
  requeued; completed work/evidence is kept. A duplicate old failure must not
  overwrite newer DONE/SPLIT records. Non-budget failures are not bulk reset.
- GUI shows the additional-instruction UTF-8 counter. The 1200-byte INPUT FIELD
  limit still exists and is distinct from the model token budget. Validation
  errors no longer instruct the user to install dependencies. Only actual
  import failures show that instruction. Resume can apply explicit Context,
  Output and reasoning changes without replacing topic/instructions/policy.

## Reproducible local validation

Commands (Linux / Python 3.13.5 / Xvfb):

```sh
xvfb-run -a env PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src/ollama_deep_researcher
```

132 tests passed, no failures and no skips. This includes 25 new tests for
multilingual input, proactive fitting, immutable ranges, process interruption,
planner retry differences, real local HTTP streaming, metrics, calibration,
old-checkpoint recovery and Tk GUI input/resume behavior. The first 12 budget
regressions were run on the unmodified baseline and all failed as expected.
Subsequent fixes were likewise driven by failing regression cases. Existing
107 tests continue to pass. Source also parses under Python 3.10 grammar.

A controlled synthetic comparison uses the exact same 1200-byte Korean
instruction and 9540-character Korean source in both versions. The model is a
fixture, NOT real Qwen, and the source is NOT an actual industry document.

| Measure | Baseline | Hotfix |
|---|---:|---:|
| Local PromptBudgetError events | 19 | 0 |
| Extractor calls | 22 | 9 |
| Normal preflight splits | 0 | 6 |
| Stored evidence records | 1 | 1 |
| Original source unchanged | Yes | Yes |

This is a control-flow regression comparison, not a GPU-speed or research
accuracy benchmark. Another test runs the whole engine through a real local
HTTP endpoint with synthetic Ollama responses and retains the final source
claim with no budget errors. GitHub Actions is configured to run the suite
on Ubuntu and Windows with Python 3.11; associated commit checks provide the
CI result, independently of these local results.

## Remaining validation and limits

Real Windows laptop + Qwen3.5:9b, source interpretation, external website
availability and long-duration operation must still be checked on the user's
machine. No independent reviewer or full security audit was performed here.
The original log alone cannot prove all extractor calls failed. Heuristic
budgeting and successful automated tests are not a guarantee against every
future model/context error. Input/context/output configuration can still be
incompatible; the correct behavior is a visible bounded stop, not silent
truncation or unlimited retries. Reusing a stopped old project keeps its old
plan (including a fallback plan); use a new project with an explicit reference
to test the improved planner while reusing originals.

## Primary protocol references

- Ollama Chat API: https://docs.ollama.com/api/chat
- Ollama Thinking: https://docs.ollama.com/capabilities/thinking
- Ollama Context length: https://docs.ollama.com/context-length
- Ollama Modelfile parameters: https://docs.ollama.com/modelfile
