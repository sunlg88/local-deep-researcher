"""One request budget for scheduling AND transport; estimates are not tokenization.

Qwen text is estimated with script-sensitive costs and 20% headroom. Unknown
models use a UTF-8 upper-bound fallback. Neither path is an exact tokenizer for
a model's chat template. A separate 768-token reserve and observed server counts
protect subsequent calls; no context enlargement or model download is implicit.
"""
from copy import deepcopy
import json
import math
import re

from .pm_prompts import BOUNDARY, PROMPTS, SCHEMAS, PromptBudgetError

POLICY_VERSION = 1
RESERVE_TOKENS = 768
OUTPUT_TARGETS = {'planner': 1536, 'researcher': 1024, 'extractor': 2560,
                  'critic': 3072, 'writer': 3072}


class FixedPromptBudgetError(PromptBudgetError):
    """The immutable question/metadata cannot fit; splitting a source cannot help."""


def estimate_tokens(text, model='qwen3.5:9b'):
    """Return a deliberately padded estimate, never present it as an exact count."""
    if 'qwen' not in model.casefold():
        return len(text.encode('utf-8')) + 128
    cost = 0
    for part in re.findall(r'[A-Za-z]+|[0-9]+|[ \t]+|.', text, re.DOTALL):
        code = ord(part[0])
        if part.isascii():
            if part.isalpha():
                cost += math.ceil(len(part) / 3)
            elif part.isdigit():
                cost += math.ceil(len(part) / 2)
            elif part.isspace():
                cost += math.ceil(len(part) / 4)
            else:
                cost += len(part)
        elif (0xAC00 <= code <= 0xD7A3 or 0x3400 <= code <= 0x9FFF or
              0x3040 <= code <= 0x30FF):
            cost += 2
        else:
            # Emoji, unusual scripts and decomposed Unicode get the byte upper bound.
            cost += len(part.encode('utf-8'))
    return math.ceil(cost * 1.2) + 128


def schema_for(role, payload):
    """Match the actual task/claim batch, not a global twenty-task schema."""
    schema = deepcopy(SCHEMAS[role])
    if role == 'planner':
        count = max(1, min(20, int(payload.get('max_tasks', 3))))
        items = schema['properties']['tasks']
        items['maxItems'] = count
        items['minItems'] = max(1, min(count, int(payload.get('min_tasks', 1))))
        props = items['items']['properties']
        props['title']['maxLength'] = 72
        props['query']['maxLength'] = 120
        props['criteria']['minItems'] = 1
        props['criteria']['maxItems'] = 1
        props['criteria']['items']['maxLength'] = 120
    elif role == 'extractor':
        schema['properties']['claims']['maxItems'] = max(1, min(3, int(payload.get('max_claims', 3))))
    elif role == 'writer':
        schema['properties']['summary']['maxLength'] = 1800 if payload.get('compact_retry') else 3000
    return schema


def request_parts(cfg, role, payload):
    """Render exactly the same text for both the engine and the HTTP boundary."""
    prompt = BOUNDARY + PROMPTS[role]
    if payload.get('compact_retry'):
        prompt += ' Retry: return the smallest valid JSON answer, without long explanations.'
    public = {k: v for k, v in payload.items() if not k.startswith('_pm_')}
    user = json.dumps(public, ensure_ascii=False, separators=(',', ':'))
    scale = max(1.0, min(16.0, float(payload.get('_pm_budget_scale', 1.0))))
    estimated = math.ceil(estimate_tokens(prompt + user, cfg.model) * scale)
    output = min(cfg.output_tokens, OUTPUT_TARGETS[role])
    budget = cfg.context_tokens - output - RESERVE_TOKENS
    metrics = {'estimated_input_tokens': estimated, 'input_budget': budget,
               'output_budget': output, 'context_tokens': cfg.context_tokens,
               'prompt_bytes': len((prompt + user).encode('utf-8')),
               'estimate_method': 'qwen-unicode-heuristic-v1' if 'qwen' in cfg.model.casefold() else 'utf8-upper-bound-v1',
               'estimate_scale': scale,
               'think': bool(cfg.think and role in ('critic', 'writer') and not payload.get('compact_retry'))}
    return prompt, user, metrics


def ensure_fits(metrics):
    if metrics['estimated_input_tokens'] > metrics['input_budget']:
        raise PromptBudgetError(
            f"Estimated input {metrics['estimated_input_tokens']} tokens exceeds "
            f"budget {metrics['input_budget']} (context={metrics['context_tokens']}, "
            f"output={metrics['output_budget']}, reserve={RESERVE_TOKENS}; "
            f"method={metrics['estimate_method']}, NOT an exact tokenizer count)")
