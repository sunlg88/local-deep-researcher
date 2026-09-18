"""Optional measured embedding adapters. Disabled by default; never pull weights.

API references: docs.ollama.com/api/embed and sbert.net SentenceTransformer API.
CPU-only placement is requested for both adapters; real laptop impact must be measured.
"""
import json
import math
from pathlib import Path
import time
from urllib.request import Request

from .pm_io import opener


def cosine_scores(rows, count):
    if not isinstance(rows,list) or len(rows)!=count+1 or not rows or not isinstance(rows[0],list):
        raise ValueError('Wrong embedding response shape')
    size=len(rows[0])
    if not 1<=size<=16384: raise ValueError('Unbounded embedding dimensions')
    normalized=[]
    for row in rows:
        if not isinstance(row,list) or len(row)!=size or not all(type(x) in (int,float) and math.isfinite(x) for x in row):
            raise ValueError('Invalid embedding vector')
        length=math.sqrt(sum(float(x)*float(x) for x in row))
        if not math.isfinite(length) or length<=0: raise ValueError('Invalid embedding norm')
        normalized.append([x/length for x in row])
    return [max(-1.,min(1.,sum(a*b for a,b in zip(normalized[0],row)))) for row in normalized[1:]]


def validate_texts(query,texts):
    if not isinstance(texts,list) or len(texts)>32 or not all(isinstance(t,str) and len(t)<=6000 for t in [query]+texts):
        raise ValueError('Semantic batch exceeds local bounds')
    return [query]+texts


class CPUEmbedding:
    name='cpu-local-sentence-transformer'
    def __init__(self,cfg):
        self.path=cfg.semantic_model
        self.model=None
        self.last_metrics={}
        self.check=lambda:None

    def score(self,query,texts):
        inputs=validate_texts(query,texts)
        started=time.monotonic();load=0.
        self.check()
        if self.model is None:
            path=Path(self.path)
            if not path.is_dir(): raise ValueError('An existing local embedding directory is required; no download attempted')
            from sentence_transformers import SentenceTransformer
            self.model=SentenceTransformer(str(path.resolve()),device='cpu',local_files_only=True,trust_remote_code=False)
            load=time.monotonic()-started
        self.check()
        # Do not silently truncate a scientific condition to fit the embedding model.
        tokenized=self.model.tokenizer(inputs,truncation=False,padding=False)
        if any(len(ids)>self.model.max_seq_length for ids in tokenized['input_ids']):
            raise ValueError('Embedding input exceeds local model length; use lexical fallback')
        vectors=self.model.encode(inputs,batch_size=4,device='cpu',normalize_embeddings=True,show_progress_bar=False)
        self.check()
        self.last_metrics={'seconds':time.monotonic()-started,'load_seconds':load,
                           'placement_requested':'cpu','memory_bytes':None}
        return cosine_scores(vectors.tolist(),len(texts))


class OllamaEmbedding:
    name='ollama-embedding-cpu-requested'
    def __init__(self,cfg):
        self.cfg=cfg
        self.checked=False
        self.last_metrics={}
        self.check=lambda:None

    def _request(self,path,data=None):
        self.check()
        request=Request(self.cfg.ollama_url.rstrip('/')+path,
            data=None if data is None else json.dumps(data).encode('utf-8'),
            headers={'Content-Type':'application/json'})
        with opener().open(request,timeout=min(self.cfg.request_timeout,30)) as response:
            body=response.read(4_000_001)
        self.check()
        if len(body)>4_000_000: raise ValueError('Embedding response exceeds transport budget')
        reply=json.loads(body)
        if not isinstance(reply,dict) or reply.get('error'): raise ValueError('Embedding server rejected request')
        return reply

    def score(self,query,texts):
        inputs=validate_texts(query,texts)
        if not self.checked:
            models=self._request('/api/tags').get('models',[])
            names={m.get('name') for m in models if isinstance(m,dict)}
            wanted=self.cfg.semantic_model
            if wanted not in names and wanted+':latest' not in names:
                raise ValueError('Embedding model is not installed; no pull attempted')
            self.checked=True
        started=time.monotonic()
        reply=self._request('/api/embed',{'model':self.cfg.semantic_model,'input':inputs,
                    'truncate':False,'keep_alive':'30m','options':{'num_gpu':0}})
        self.last_metrics={k:reply[k] for k in ('load_duration','total_duration','prompt_eval_count') if k in reply}
        self.last_metrics.update(seconds=time.monotonic()-started,placement_requested='cpu',memory_bytes=None)
        return cosine_scores(reply.get('embeddings'),len(texts))


def build_backend(cfg):
    if cfg.semantic_rerank=='off': return None
    return CPUEmbedding(cfg) if cfg.semantic_backend=='cpu' else OllamaEmbedding(cfg)
