"""Input feedback and budget-only resume settings; no external services."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'), 'Display required')
class BudgetDesktop(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from ollama_deep_researcher.pm_gui import App
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.app = App(self.root, Path(self.tmp.name))
        self.root.update()

    def test_length_error_is_not_dependency_install_error(self):
        self.app.topic.set('Synthetic topic')
        self.app.instructions.insert('1.0', '\uac00'*401)
        with patch.dict(sys.modules, {'ollama_deep_researcher.utils':types.ModuleType('utils')}), \
             patch('ollama_deep_researcher.pm_gui.messagebox.showerror') as error:
            self.app.start()
        self.assertEqual(len(error.call_args_list), 1)
        self.assertNotIn('INSTALL_PM_DEPENDENCIES', error.call_args.args[1])
        self.assertIn('1203', error.call_args.args[1].replace(',',''))
        self.assertEqual(self.app.store.projects(), [])

    def test_input_counter_uses_utf8_bytes(self):
        self.assertTrue(hasattr(self.app, 'instruction_count'), 'Visible UTF-8 counter missing')
        self.app.instructions.insert('1.0', '\uac00'*400)
        self.root.update()
        self.assertIn('1200', self.app.instruction_count.get().replace(',',''))
        self.app.instructions.insert('end', '\uac00')
        self.root.update()
        self.assertIn('1203', self.app.instruction_count.get().replace(',',''))

    def test_paused_project_resume_applies_only_explicit_budget_controls(self):
        cfg = self.app.settings()
        pid = self.app.store.create('Keep this original question', cfg, 'Keep this instruction')
        self.app.pid = pid
        s = self.app.store.load(pid)
        s.update(status='PAUSED', stage='select')
        self.app.store.save(s)
        self.app.values['context_tokens'].set('16384')
        self.app.values['output_tokens'].set('2048')
        self.app.topic.set('Do not overwrite saved topic')
        self.app.domains.set('do-not-change.example')
        with patch.object(self.app, 'launch') as launch, patch('ollama_deep_researcher.pm_gui.messagebox.showinfo'):
            self.app.resume()
        state = self.app.store.load(pid)
        self.assertEqual(state['settings']['context_tokens'],16384)
        self.assertEqual(state['settings']['output_tokens'],2048)
        self.assertEqual(state['settings']['allowed_domains'],[])
        self.assertEqual(state['topic'],'Keep this original question')
        self.assertEqual(state['instructions'],'Keep this instruction')
        launch.assert_called_once()

    def test_budget_blocked_project_can_resume_without_losing_queue(self):
        pid = self.app.store.create('Question', self.app.settings())
        self.app.pid = pid
        state = self.app.store.load(pid)
        state.update(status='INPUT_BUDGET_BLOCKED', stage='plan')
        self.app.store.save(state)
        with patch.object(self.app, 'launch') as launch, patch('ollama_deep_researcher.pm_gui.messagebox.showinfo'):
            self.app.resume()
        launch.assert_called_once()
        self.assertEqual(self.app.store.load(pid)['stage'], 'plan')
