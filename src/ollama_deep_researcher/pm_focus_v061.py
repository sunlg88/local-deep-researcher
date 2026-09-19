"""Focused lead signals, not truth judgements. No company-specific knowledge base.

Only the current search entity/gap supply signals. The long user instruction
and unrelated task titles must not make a random page 'relevant'. Deferrals
retain candidates/originals and can be reconsidered by a new focused query.
"""
from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import urlsplit, unquote

from .pm_retrieval_v06 import terms
from .pm_search_quality import score_hit, HitDecision

SUFFIXES = {'inc', 'ltd', 'limited', 'corporation', 'corp', 'company', 'co', 'the', 'and'}
GENERIC = set('capacity project projects record records contract contracts supply actual maximum '
              'size weight year years current new latest data information homepage website '
              'overview number results global world international official'.split())


def _words(text):
    return re.findall(r'[^\W_]+(?:[-.][^\W_]+)*', unicodedata.normalize('NFKC',str(text)).casefold())


@dataclass(frozen=True)
class FocusSignal:
    entity_present: bool
    topic_matches: tuple[str, ...]
    distinctive_matches: tuple[str, ...]

    @property
    def plausible(self):
        return (self.entity_present and bool(self.topic_matches)) or len(self.distinctive_matches) >= 2


def focused_signal(data: dict, text: str) -> FocusSignal:
    name=[w for w in _words(data.get('entity','')) if w not in SUFFIXES]
    body=_words(text)
    name_set=set(name)
    # Full entity tokens must be near each other; a shared city is insufficient.
    present=False
    if name_set:
        positions=[i for i,w in enumerate(body) if w in name_set]
        for i in positions:
            if name_set <= set(body[i:i+max(12,len(name)+3)]):
                present=True;break
    query=' '.join([str(data.get('gap',''))]+[str(x) for x in data.get('keywords',[]) if isinstance(x,str)])
    topic=set(terms(query))-name_set
    matched=topic & set(terms(text))
    # Generic words/dates alone cannot qualify a source without its full entity.
    specific={w for w in matched if w not in GENERIC and not w.replace('.','').isdigit()}
    return FocusSignal(present,tuple(sorted(matched)),tuple(sorted(specific)))


def is_search_wrapper(url: str) -> bool:
    p=urlsplit(url)
    host=(p.hostname or '').casefold().removeprefix('www.')
    return ((host=='bing.com' and p.path.rstrip('/') in ('/search','/copilotsearch')) or
            (host.startswith('google.') and p.path.rstrip('/')=='/search') or
            (host=='duckduckgo.com' and p.path.rstrip('/') in ('','/html','/lite') and bool(p.query)))


def score_focused_hit(search_intent, hit, settings, data: dict) -> HitDecision:
    base=score_hit(search_intent,hit,settings)
    if not base.accepted:
        return base
    if is_search_wrapper(hit.get('url','')):
        return HitDecision(False,0,base.reasons+('SEARCH_RESULT_WRAPPER',),base.host)
    text=' '.join([str(hit.get('title','')),str(hit.get('content',hit.get('snippet',''))),
                   unquote(hit.get('url',''))])
    signal=focused_signal(data,text)
    if signal.entity_present:
        # Official homepages may lack a technical snippet. They remain leads,
        # ranked below full-entity + topic hits, not certified facts.
        score=base.score+10+(5 if signal.topic_matches else 0)
        reasons=tuple(r for r in base.reasons if r!='LOW_LEXICAL_MATCH')+('ENTITY_MATCH',)
        return HitDecision(True,score,reasons,base.host)
    if len(signal.distinctive_matches)>=2:
        # A useful sector-wide or alias-language page can have no full company
        # name in its snippet. Preserve a bounded exploratory path.
        return HitDecision(True,base.score+1,
            tuple(r for r in base.reasons if r!='LOW_LEXICAL_MATCH')+('TOPIC_EXPLORATION',),base.host)
    return HitDecision(True,0,base.reasons+('ENTITY_TOPIC_DEFERRED',),base.host)
