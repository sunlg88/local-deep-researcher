"""Hotfix regressions: production boundaries, not a claim of live search quality."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_query_policy import validate_intent, compile_query
from ollama_deep_researcher import pm_v06_store as audit
from ollama_deep_researcher import pm_research_metrics as metrics
from v06_fixtures import Model06, Web06, TEXT


def intent(**kw):
    d=dict(entity='Sheffield Forgemasters',gap='SMR pressure vessel contracts',
           keywords=['SMR','pressure vessel'],strategy='exact_entity',language='en',site_hint='')
    d.update(kw)
    return d


class QueryBoundary061(unittest.TestCase):
    def test_logged_instruction_dump_cannot_be_compiled_as_search_gap(self):
        rows=json.loads((Path(__file__).parent/'fixtures/v061_observed_queries.json').read_text())['queries']
        gap=rows[0].split('" ',1)[1].split(' SMR pressure vessel')[0]
        with self.assertRaisesRegex(ValueError,'gap|language'):
            validate_intent(intent(gap=gap),set())

    def test_multigap_sentence_rejected_without_silently_losing_constraints(self):
        with self.assertRaises(ValueError):
            validate_intent(intent(gap='press capacity, ingot weight, commissioning year'),set())

    def test_multi_entity_comma_list_rejected(self):
        with self.assertRaises(ValueError):
            validate_intent(intent(entity='Sheffield Forgemasters, Japan Steel Works'),set())

    def test_language_hint_en_cannot_hide_korean_instruction_sentence(self):
        with self.assertRaisesRegex(ValueError,'language|gap'):
            validate_intent(intent(gap='\uacf5\uae09 \uacc4\uc57d \uc5ec\ubd80 \ud655\uc778 \ud544\uc694'),set())

    def test_compact_korean_gap_is_allowed_with_correct_language(self):
        d=intent(entity='\ub450\uc0b0\uc5d0\ub108\ube4c\ub9ac\ud2f0',gap='SMR \uacf5\uae09 \uacc4\uc57d',language='ko')
        self.assertIn(d['gap'],compile_query(validate_intent(d,set()),'duckduckgo'))

    def test_numbers_negations_remain_in_single_gap(self):
        d=intent(gap='2026 contracts 500 MW',keywords=['BWRX-300','not cancelled'])
        query=compile_query(validate_intent(d,set()),'duckduckgo')
        for x in ['2026','500 MW','not cancelled']:self.assertIn(x,query)


class EngineHotfix061(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)

    def build(self, model=None, web=None, **settings):
        cfg=Settings(max_attempts=settings.pop('max_attempts',1),draft_enabled=False,**settings)
        pid=self.w.create_v06('Sheffield Forgemasters nuclear forging projects',cfg)
        st=self.w.open(pid)
        e=EngineV06(st,model or Model06(intents=[intent()]),web or Web06())
        return pid,st,e

    def run_engine(self,e,pid):
        with patch.object(e.pacer,'wait',return_value=.1):
            for _ in range(160):
                if not e.step(pid):return
        self.fail('Hotfix run did not terminate within 160 state transitions')

    def test_partial_city_name_does_not_fetch_unrelated_university(self):
        web=Web06([{'url':'https://university.example/home','title':'University of Sheffield',
                    'content':'Sheffield students, research projects and global rankings.'}])
        pid,st,e=self.build(web=web)
        self.run_engine(e,pid)
        self.assertEqual(web.fetches,[])
        self.assertFalse([p for r,p in e.model.payloads if r=='extractor'])
        self.assertEqual(len(metrics.hits(st)),1) # Candidate is audited, not erased.

    def test_search_engine_result_wrapper_not_fetched_as_evidence(self):
        web=Web06([{'url':'https://www.bing.com/copilotsearch?q=quotes+of+the+day',
                    'title':'quotes of the day','content':'Inspiration and quotes'}])
        pid,st,e=self.build(web=web)
        self.run_engine(e,pid)
        self.assertEqual(web.fetches,[])

    def test_exact_entity_partner_source_still_fetched(self):
        url='https://customer.example/contract'
        web=Web06([{'url':url,'title':'SMR contract with Sheffield Forgemasters',
                    'content':'Sheffield Forgemasters supplies a pressure vessel.'}],
                   {url:'Sheffield Forgemasters supplies a pressure vessel for an SMR contract.'})
        pid,st,e=self.build(web=web)
        self.run_engine(e,pid)
        self.assertEqual(web.fetches,[url])
        self.assertTrue([p for r,p in e.model.payloads if r=='extractor'])

    def test_wrong_body_with_generic_words_is_preserved_not_sent_to_qwen(self):
        url='https://university.example/home'
        web=Web06([{'url':url,'title':'Sheffield Forgemasters SMR projects','content':'pressure vessel contracts'}],
                   {url:('University of Sheffield students study research projects and global rankings. '*60)})
        pid,st,e=self.build(web=web)
        self.run_engine(e,pid)
        self.assertEqual(st.counts()['documents'],1)
        self.assertEqual(st.all_documents()[0]['body'],web.bodies[url])
        self.assertFalse([p for r,p in e.model.payloads if r=='extractor'])

    def test_accepted_long_entity_never_fails_the_100_character_anchor_boundary(self):
        long_name=' '.join(['Longname']*14) # 125 minus spaces? reduce to <=120
        long_name=long_name[:119].rstrip()
        d=intent(entity=long_name)
        validate_intent(d,set())
        pid,st,e=self.build(model=Model06(intents=[d]),web=Web06(hits=[]))
        self.run_engine(e,pid)
        errors=[r['message'] for r in st.events(pid,200) if r['kind']=='WORK_ERROR']
        self.assertFalse(any('Search anchors' in s for s in errors),errors)
        self.assertEqual(len(e.web.queries),1)
        for a in metrics.attempts(st):
            self.assertTrue(all(0<len(x)<=100 for x in a['anchors']))

    def test_cached_variants_used_before_another_researcher_call(self):
        pid,st,e=self.build(web=Web06(hits=[]),max_attempts=3)
        self.run_engine(e,pid)
        self.assertEqual(len(e.web.queries),3)
        self.assertEqual(len([p for r,p in e.model.payloads if r=='researcher']),1)

    def test_resume_keeps_variant_cache_and_does_not_recall_researcher(self):
        pid,st,e=self.build(web=Web06(hits=[]),max_attempts=3)
        with patch.object(e.pacer,'wait',return_value=.1):
            for _ in range(20):
                e.step(pid)
                if len(e.web.queries)==1:break
        resumed=EngineV06(st,e.model,e.web)
        self.run_engine(resumed,pid)
        self.assertEqual(len([p for r,p in e.model.payloads if r=='researcher']),1)
        self.assertEqual(len(set(e.web.queries)),3)

    def _seed_legacy_selected_range(self,e,st,pid,body):
        s=st.load(pid)
        s['tasks']=[e.make_task({'title':'Sheffield Forgemasters SMR pressure vessel',
                               'query':'Sheffield Forgemasters SMR','criteria':['SMR pressure vessel']},1)]
        s['tasks'][0]['attempts']=s['settings']['max_attempts']
        s.update(stage='select',references_v06_scanned=True)
        e.upgrade_budget_checkpoint(s)
        did=st.add_document('https://source.example/p','University of Sheffield',body,
                            metadata={'research_intent':intent()})
        from ollama_deep_researcher.pm_fetch_quality import reuse_key
        plan={'document_id':did,'reuse_key':reuse_key(body,'University of Sheffield'),
              'alias_of':None,'status':'QUEUED','quality':{'status':'READY','score':.1,'reasons':[]},
              'query':s['topic'],'task_terms':['Sheffield Forgemasters'], 'ranked':[], 'chunks':[],
              'selected_ranges':[[0,len(body)]],'pass_number':0,'unread_chars':len(body),'total_chars':len(body)}
        audit.save_plan(st,plan);metrics.queue_document(st,did)
        s['enqueued_documents']=[did];e._restore_plan(s,plan);st.save(s)
        return did

    def test_first_irrelevant_stops_pending_split_children_of_wrong_entity(self):
        pid,st,e=self.build()
        body=('University of Sheffield student projects and generic research rankings. '*230)
        did=self._seed_legacy_selected_range(e,st,pid,body)
        self.run_engine(e,pid)
        self.assertEqual(len([p for r,p in e.model.payloads if r=='extractor']),1)
        self.assertEqual(audit.get_plan(st,did)['status'],'DEFERRED')
        self.assertEqual(st.document(did)['body'],body)
        self.assertGreater(audit.get_plan(st,did)['unread_chars'],0)
        # A resumed project cannot resurrect persisted SPLIT children.
        s=st.load(pid);s.update(stage='select',status='PENDING');st.save(s)
        self.run_engine(EngineV06(st,e.model,e.web),pid)
        self.assertEqual(len([p for r,p in e.model.payloads if r=='extractor']),1)

    def test_irrelevant_intro_does_not_cancel_a_strong_entity_fact_at_tail(self):
        class TailModel(Model06):
            def ask(self,role,payload,check):
                if role=='extractor':
                    self.payloads.append((role,dict(payload)));check()
                    fact='Sheffield Forgemasters has a 500 t SMR pressure vessel contract.'
                    if fact in payload['source_text']:
                        from test_pm import claim
                        row=claim(value='500',period='');row.update(entity='Sheffield Forgemasters',subentity='SMR pressure vessel',metric='mass',scope='contract',quote=fact,claim_text=fact,
                            task_ids=[t['id'] for t in payload['task_catalog']])
                        return dict(relevance='relevant',reason='Synthetic tail fact',claims=[row])
                    return dict(relevance='irrelevant',reason='Synthetic introduction only',claims=[])
                return super().ask(role,payload,check)
        pid,st,e=self.build(model=TailModel())
        body=('General background introduction. '*220)+'\n\nSheffield Forgemasters has a 500 t SMR pressure vessel contract.'
        self._seed_legacy_selected_range(e,st,pid,body)
        self.run_engine(e,pid)
        self.assertEqual(st.counts()['evidence'],1)
        self.assertGreater(len([p for r,p in e.model.payloads if r=='extractor']),1)

    def test_model_failed_event_explains_cause_without_api_secret(self):
        class Bad(Model06):
            def ask(self,role,payload,check):
                self.last_metrics={'answer_chars':54}
                raise ValueError('Bad response Authorization: Bearer sk-very-secret-token-value')
        pid,st,e=self.build(model=Bad())
        with self.assertRaises(ValueError):e._ask(st.load(pid),'researcher',{})
        event=next(json.loads(r['message']) for r in st.events(pid,100) if r['kind']=='MODEL_FAILED')
        self.assertEqual(event.get('error_type'),'ValueError')
        self.assertTrue(event.get('error_stage'))
        self.assertIn('Bad response',event.get('error_message',''))
        self.assertNotIn('sk-very-secret-token-value',json.dumps(event))

if __name__=='__main__':unittest.main()
