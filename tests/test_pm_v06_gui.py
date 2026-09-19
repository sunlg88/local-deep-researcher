"""Real Tk controls select the versioned engine, not just a changed title."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_types import Settings


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Display required')
class Desktop06(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from ollama_deep_researcher.pm_gui import App
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        dialog=patch("ollama_deep_researcher.pm_gui.messagebox.showerror")
        self.errors=dialog.start();self.addCleanup(dialog.stop)
        self.root=tk.Tk();self.addCleanup(self.root.destroy)
        self.app=App(self.root,Path(self.tmp.name));self.root.update()

    def test_new_start_uses_version_six_and_modes(self):
        self.assertIn('0.6',self.root.title())
        self.assertEqual(self.app.settings().optimization_mode,'balanced')
        self.assertEqual(self.app.settings().semantic_rerank,'off')
        self.app.topic.set('TEST press')
        with patch.object(self.app,'launch'):
            self.app.start()
        state=self.app.store.load(self.app.pid)
        self.assertEqual(state['engine_version'],6)

    def test_launch_dispatches_v05_and_v06_to_different_engines(self):
        pid5=self.app.store.create('TEST press',Settings())
        pid6=self.app.store.create_v06('TEST press',Settings())
        calls=[]
        class Old:
            def __init__(self,st,model,web): calls.append(('old',type(web).__name__))
            def run(self,pid): pass
        class New:
            def __init__(self,st,model,web): calls.append(('new',type(web).__name__))
            def run(self,pid): pass
        with patch('ollama_deep_researcher.pm_gui.Engine',Old), patch('ollama_deep_researcher.pm_gui.EngineV06',New):
            for pid in (pid5,pid6):
                self.app.pid=pid;self.app.launch();self.app.worker.join(2)
        self.assertEqual(calls,[('old','Web'),('new','WebV06')])

    def test_continue_button_creates_new_six_without_mutating_five(self):
        pid=self.app.store.create('TEST press',Settings())
        path=self.app.store.project_path(pid)/'project.sqlite3'
        before=path.read_bytes();self.app.pid=pid
        with patch.object(self.app,'launch'):
            self.app.continue_legacy()
        state=self.app.store.load(self.app.pid)
        self.assertEqual(state['engine_version'],6)
        self.assertEqual(state['reference_projects'],[pid])
        self.assertEqual(path.read_bytes(),before)

    def test_resume_does_not_overwrite_mode_or_search_policy(self):
        pid=self.app.store.create_v06('TEST press',Settings(optimization_mode='quality'))
        self.app.pid=pid
        with patch.object(self.app,'launch'):
            self.app.resume()
        self.assertEqual(self.app.store.load(pid)['settings']['optimization_mode'],'quality')

    def test_new_continuation_uses_explicitly_selected_mode(self):
        from ollama_deep_researcher.pm_gui import OPTIMIZATION_NAMES
        pid=self.app.store.create('TEST press',Settings())
        self.app.pid=pid
        self.app.optimization_mode.set(OPTIMIZATION_NAMES['quality'])
        with patch.object(self.app,'launch'): self.app.continue_legacy()
        self.assertEqual(self.app.store.load(self.app.pid)['settings']['optimization_mode'],'quality')
