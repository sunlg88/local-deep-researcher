"""Desktop shell tests. Use xvfb-run on headless Linux."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

class Desktop(unittest.TestCase):
    def test_gui_entrypoint_present(self):
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_gui'))
    def test_windows_launcher(self):
        self.assertTrue((Path(__file__).resolve().parents[1]/'START_RESEARCH_PM.bat').exists())
    @unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Display required')
    def test_gui_construct_and_refresh(self):
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_gui'))
        import tkinter as tk
        from ollama_deep_researcher.pm_gui import App
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            try:
                app = App(root, Path(folder)); root.update(); app.refresh()
                self.assertEqual(app.model.get(), 'qwen3.5:9b')
                self.assertEqual(app.source_policy.get(), '일반 웹 조사'); self.assertEqual(app.domains.get(), '')
                self.assertEqual(app.settings().source_mode, 'open')
                self.assertIsNotNone(app.start_button); self.assertIsNotNone(app.pause_button)
            finally: root.destroy()

    @unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Display required')
    def test_gui_uses_isolated_store_and_explicit_reference_selection(self):
        import tkinter as tk
        from unittest.mock import patch
        from ollama_deep_researcher.pm_gui import App
        from ollama_deep_researcher.pm_projects import Workspace
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk()
            try:
                app=App(root, Path(folder));root.update()
                self.assertIsInstance(app.store,Workspace)
                self.assertEqual(app.reference_ids,[])
                self.assertTrue(hasattr(app,'reference_button'))
                self.assertIn('time_limit_minutes',app.values)
                self.assertEqual(app.instructions.get('1.0','end').strip(),'')
                self.assertFalse(app.advanced.winfo_ismapped())
                first=app.store.create('Reference project',app.settings())
                app.reference_ids=[first]
                app.pid=app.store.create('Isolated child',app.settings(),reference_projects=app.reference_ids)
                captured=[]
                class DummyEngine:
                    def __init__(self, store, model, web): captured.append(store)
                    def run(self,pid): pass
                with patch('ollama_deep_researcher.pm_gui.Engine',DummyEngine):
                    app.launch();app.worker.join(2)
                self.assertEqual(captured[0].project_id,app.pid)
                self.assertEqual(captured[0].path.name,'project.sqlite3')
                self.assertFalse((Path(folder)/'research.sqlite3').exists())
            finally: root.destroy()
