"""Bounded failure diagnostics. Never emit raw model output or thinking text."""
import os
import re


def safe_error(error) -> str:
    text=str(error)
    # Redact configured credentials even when a transport embeds them verbatim.
    for key,value in os.environ.items():
        if value and len(value)>=4 and any(w in key.upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD')):
            text=text.replace(value,'[REDACTED]')
    text=re.sub(r'(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)(bearer\s+)[^\s,;]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s&;,]+',r'\1[REDACTED]',text)
    text=re.sub(r'\b(?:sk-|tvly-)[A-Za-z0-9_-]+','[REDACTED]',text)
    text=re.sub(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@',r'\1[REDACTED]@',text)
    return ' '.join(text.split())[:700]


def failure_details(exc, stage='model_call') -> dict:
    name=type(exc).__name__
    if name=='OutputLimitError': stage='output_limit'
    elif name in ('PromptBudgetError','FixedPromptBudgetError'): stage='input_budget'
    result={'error_type':name,'error_stage':stage,'error_message':safe_error(exc)}
    code=getattr(exc,'code',None)
    if isinstance(code,int): result['http_status']=code
    return result
