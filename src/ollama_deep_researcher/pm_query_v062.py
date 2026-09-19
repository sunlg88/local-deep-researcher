"""Independent search targets and shared conditions. Never invent names/domains."""
from copy import deepcopy
import re
from .pm_query_policy import validate_intent, query_variants, _bounded

LANGUAGES=('en','ko','ja','zh','de','fr','es','pt','ru','it','ar','auto')
PROMPT=(
    'Plan up to THREE separate web searches for ONE entity from the original question. '
    'Return only entity, search_phrases, constraints, language, site_hint. '
    'Each search_phrases item is ONE short topic (2-6 words), not a sentence, checklist or explanation. '
    'Different requested metrics go in DIFFERENT items, never concatenated. '
    'Example: {"entity":"Example Engineering","search_phrases":["maximum ingot weight",'
    '"SMR supply contract"],"constraints":[],"language":"en","site_hint":""}. '
    'Keep mandatory numeric/date/exclusion conditions in constraints; do not invent conditions. '
    'Use concise source-language search words, not task instructions or status. '
    'Keep entity names unambiguous; do not guess abbreviations or domains. '
    'site_hint is empty or a host from known_domains. No raw search operators or quotes. '
    'Use feedback to choose an untried topic. This plans searches, not factual conclusions.')
SCHEMA={'type':'object','additionalProperties':False,
    'required':['entity','search_phrases','constraints','language','site_hint'],
    'properties':{'entity':{'type':'string','minLength':1,'maxLength':120},
        'search_phrases':{'type':'array','minItems':1,'maxItems':3,'items':{'type':'string','minLength':1,'maxLength':80}},
        'constraints':{'type':'array','maxItems':4,'items':{'type':'string','minLength':1,'maxLength':80}},
        'language':{'type':'string','enum':list(LANGUAGES)},'site_hint':{'type':'string','maxLength':253}}}


def query_proposals(data,known_domains):
    if not isinstance(data,dict):raise ValueError('Search proposal must be an object')
    if set(data)==set(SCHEMA['required']):
        entity=_bounded(data['entity'],'entity',120)
        phrases=data['search_phrases'];constraints=data['constraints']
        if not isinstance(phrases,list) or not 1<=len(phrases)<=3:raise ValueError('Provide 1..3 independent search_phrases')
        if not isinstance(constraints,list) or len(constraints)>4:raise ValueError('Provide at most 4 shared constraints')
        constraints=[_bounded(x,'constraint',80) for x in constraints]
        lang=data['language'];site=data['site_hint']
    else:
        old=validate_intent(data,known_domains)
        # Compatibility is lossless. New live requests use the small schema;
        # never discard a legacy alloy/negation qualifier while reusing its cache.
        return [dict(query=q,intent=old.to_dict(),variant=i,proposal=deepcopy(data),
                     query_policy='legacy-preserved-v062')
                for i,q in enumerate(query_variants(old,'quality',0))]
    atomic=[]
    for raw in phrases:
        phrase=_bounded(raw,'search phrase',80)
        focus=dict(entity=entity,gap=phrase,keywords=constraints or [phrase],strategy='official_site' if site else 'exact_entity',language=lang,site_hint=site)
        atomic.append(validate_intent(focus,known_domains))
    expanded=[query_variants(f,'quality',0) for f in atomic]
    entries=[];seen=set()
    for variant in range(3):
        for f,queries in zip(atomic,expanded):
            if variant>=len(queries) or queries[variant] in seen:continue
            q=queries[variant];seen.add(q)
            entries.append(dict(query=q,intent=f.to_dict(),variant=variant,proposal=deepcopy(data),query_policy='atomic-v062'))
    return entries
