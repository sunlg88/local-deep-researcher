"""The current task is explicit after the broad project context."""
import json
import tempfile
import unittest
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_budget import request_parts
from v06_fixtures import Model06,Web06
class ActiveTask062(unittest.TestCase):
    def test_current_task_follows_global_company_list(self):
        with tempfile.TemporaryDirectory() as td:
            cfg=Settings(draft_enabled=False);w=Workspace(td)
            instruction='Investigate Sheffield Forgemasters, Japan Steel Works and Doosan Enerbility.'
            pid=w.create_v06('Nuclear forging supply',cfg,instruction);st=w.open(pid);model=Model06()
            engine=EngineV06(st,model,Web06(hits=[]))
            engine._ask(st.load(pid),'researcher',{'task':'Doosan Enerbility SMR supply','criteria':['supply records']})
            rendered=json.loads(request_parts(cfg,'researcher',model.payloads[-1][1])[1])
            self.assertEqual(list(rendered)[-1],'active_task')
            self.assertEqual(rendered['active_task'],'Doosan Enerbility SMR supply')
            self.assertEqual(rendered['instructions'],instruction)
