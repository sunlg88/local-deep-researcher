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
