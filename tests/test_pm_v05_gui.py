"""Input preflight and immutable legacy continuation, including the real Tk shell."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_types import Settings,validate_research_input
from ollama_deep_researcher import pm_budget
from ollama_deep_researcher.pm_store import Store
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_engine_v05 import EngineV05
from v05_fixtures import Model05,Web05


def make_legacy(w):
    pid='0123456789abcdef'
    folder=w.project_path(pid)
    st=Store(folder,filename='project.sqlite3',project_id=pid)
    st.create('Legacy original topic',Settings(),'Legacy instructions')
    st.add_document('https://example.org/a','Legacy','Original preserved text.')
    return pid


def hashes(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))}


class InputAndLegacy05(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
    def test_4096_byte_instructions_supported(self):
        try: validate_research_input('Topic','x'*4096)
        except ValueError as exc: self.fail(str(exc))
        with self.assertRaises(ValueError): validate_research_input('Topic','x'*4097)
    def test_preflight_checks_all_fixed_roles(self):
        self.assertTrue(hasattr(pm_budget,'preflight_research_start'))
        result=pm_budget.preflight_research_start(Settings(),'Topic','x'*2000)
        self.assertEqual(set(result),{'planner','researcher','extractor','critic','writer'})
        with self.assertRaises(pm_budget.FixedPromptBudgetError):
            pm_budget.preflight_research_start(Settings(context_tokens=4096,output_tokens=2048),
                'Topic','\uac00'*1365)
    def test_new_project_is_engine_five(self):
        pid=self.w.create('New',Settings())
        self.assertEqual(self.w.load(pid).get('engine_version'),5)
    def test_legacy_open_and_export_do_not_modify_old_folder(self):
        pid=make_legacy(self.w); root=self.w.project_path(pid); before=hashes(root)
        old=self.w.open(pid)
        self.assertTrue(getattr(old,'read_only',False))
        old.all_documents(); old.counts()
        path=self.w.export(pid)
        self.assertFalse(path.is_relative_to(root))
        self.assertEqual(hashes(root),before)
    def test_legacy_continuation_is_new_explicit_reference(self):
        pid=make_legacy(self.w); root=self.w.project_path(pid); before=hashes(root)
        self.assertTrue(hasattr(self.w,'continue_as_v05'))
        new=self.w.continue_as_v05(pid)
        self.assertNotEqual(new,pid)
        state=self.w.load(new)
        self.assertEqual(state['engine_version'],5)
        self.assertEqual(state['reference_projects'],[pid])
        self.assertEqual(state['topic'],'Legacy original topic')
        self.assertEqual(state['instructions'],'Legacy instructions')
        self.assertEqual(hashes(root),before)
    def test_legacy_execution_is_refused_before_schema_mutation(self):
        pid=make_legacy(self.w);root=self.w.project_path(pid);before=hashes(root)
        with self.assertRaisesRegex(ValueError,'[Ll]egacy'):
            EngineV05(self.w.open(pid),Model05(),Web05())
        self.assertEqual(hashes(root),before)


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Display required')
class Desktop05(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from ollama_deep_researcher.pm_gui import App
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=tk.Tk();self.addCleanup(self.root.destroy)
        self.app=App(self.root,Path(self.tmp.name));self.root.update()
    def test_version_and_productivity_columns(self):
        self.assertIn('0.6',self.root.title())
        for key in ('searches','fetched','relevant','zero_yield'):
            self.assertIn(key,self.app.table['columns'])
    def test_gui_preflight_rejects_before_creating_project(self):
        self.app.topic.set('Topic')
        self.app.instructions.insert('1.0','\uac00'*1365)
        self.app.values['context_tokens'].set('4096');self.app.values['output_tokens'].set('2048')
        with patch('ollama_deep_researcher.pm_gui.messagebox.showerror') as error, patch.object(self.app,'launch'):
            self.app.start()
        self.assertTrue(error.called)
        self.assertEqual(self.app.store.projects(),[])
    def test_resume_legacy_does_not_write_it(self):
        pid=make_legacy(self.app.store);self.app.pid=pid;root=self.app.store.project_path(pid);before=hashes(root)
        with patch('ollama_deep_researcher.pm_gui.messagebox.showinfo'),patch.object(self.app,'launch') as launch:
            self.app.resume()
        self.assertFalse(launch.called)
        self.assertEqual(hashes(root),before)
