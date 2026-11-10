import importlib.util
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from detection import Sessions, session_key


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        core = types.ModuleType('visual_workflows')
        core.WorkflowStore = Mock()
        core.WorkflowRunner = Mock()
        spec = importlib.util.spec_from_file_location('integration_under_test',
                    Path(__file__).resolve().parents[1] / 'visual_integration.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', visual_workflows=core):
            spec.loader.exec_module(module)
        self.module, self.core = module, core
        self.window = types.SimpleNamespace(address='one', title='Game')
        self.profile = types.SimpleNamespace(title_re='supported')
        self.game = self.window, self.profile
        self.p = types.SimpleNamespace(preview=False, busy=False, profiles=[], legacy_profiles=[],
            settings={}, sessions=Sessions(), library=Mock(), GLib=Mock(),
            verify_target=Mock(), persist=Mock(), hide=Mock(), Layer=Mock(), window=Mock())
        self.p.library.path = Path(self.temp.name) / 'library.json'
        self.p.sessions.games = {'one': self.game}
        self.visual = module.VisualIntegration(self.p)
        self.workflow = dict(id='w', game='supported')
        self.visual.store.get.return_value = self.workflow
        self.visual.store.automatic.return_value = self.workflow
        self.core.WorkflowRunner.return_value.cancel = threading.Event()
        self.core.WorkflowRunner.return_value.paused = threading.Event()
        self.active = patch.object(module.login, 'active_window', return_value=self.window)
        self.active.start()
        self.addCleanup(self.active.stop)
        self.thread = patch.object(module.threading, 'Thread')
        self.thread.start()
        self.addCleanup(self.thread.stop)

    def test_once_per_session_and_new_session(self):
        self.visual.reconcile()
        self.assertTrue(self.visual.active)
        self.assertIn(session_key(*self.game), self.p.sessions.attempted)
        self.visual._finished(self.visual.runner)
        self.visual.reconcile()
        self.assertEqual(self.core.WorkflowRunner.call_count, 1)
        self.p.sessions.games = {}
        self.visual.reconcile()
        self.assertFalse(self.visual.claims)
        self.p.sessions.games = {'one': self.game}
        self.visual.reconcile()
        self.assertEqual(self.core.WorkflowRunner.call_count, 2)

    def test_background_and_unsupported_never_start(self):
        self.p.sessions.games = {'other': self.game}
        self.visual.reconcile()
        self.assertFalse(self.visual.active)
        self.p.sessions.games = {'one': (self.window, None)}
        self.p.library.game_options.return_value = []
        self.visual.reconcile()
        self.assertFalse(self.visual.active)

    def test_disabled_keeps_first_f9_available(self):
        self.visual.store.automatic.return_value = None
        self.visual.reconcile()
        self.assertFalse(self.visual.owns(self.game))
        self.assertFalse(self.p.sessions.attempted)
        self.assertFalse(self.visual.claims)

    def test_pending_and_manual_input_exclude_automatic(self):
        for field in ('busy', 'pending', 'pending_record', 'pending_capture', 'recorder'):
            setattr(self.p, field, True)
            self.visual.reconcile()
            self.assertFalse(self.visual.active)
            setattr(self.p, field, None)

    def test_loss_cancel_pause_and_no_duplicate_login(self):
        self.visual.reconcile()
        runner = self.visual.runner
        self.assertTrue(self.visual.owns(self.game))
        self.visual.pause()
        self.assertTrue(runner.paused.is_set())
        self.visual.resume()
        self.assertFalse(runner.paused.is_set())
        self.p.verify_target.side_effect = ValueError('focus lost')
        self.visual.reconcile()
        self.assertTrue(runner.cancel.is_set())
        self.assertFalse(self.p.sessions.claim_first(self.game))

    def test_paused_background_allows_editing_but_session_loss_cancels(self):
        self.visual.reconcile()
        runner = self.visual.runner
        self.visual.pause()
        self.p.verify_target.reset_mock()
        self.p.verify_target.side_effect = ValueError('panel focused')
        self.visual.reconcile()
        self.p.verify_target.assert_not_called()
        self.assertFalse(runner.cancel.is_set())
        self.p.sessions.games = {}
        self.visual.reconcile()
        self.assertTrue(runner.cancel.is_set())

    def test_resume_releases_keyboard_and_flushes_before_verify(self):
        self.visual.reconcile()
        self.visual.pause()
        order = Mock()
        order.attach_mock(self.p.Layer.set_keyboard_mode, 'release')
        order.attach_mock(self.p.hide, 'hide')
        order.attach_mock(self.p.window.get_display().sync, 'sync')
        order.attach_mock(self.p.verify_target, 'verify')
        self.visual.resume()
        self.assertEqual([call[0] for call in order.mock_calls],
                         ['release', 'hide', 'sync', 'verify'])

    def test_corrupted_claim_shapes_are_ignored(self):
        for value in ('"one"', '{"one": true}', '[1, "one"]'):
            self.visual.path.write_text(value)
            adapter = self.module.VisualIntegration(self.p)
            self.assertEqual(adapter.claims, set())

    def test_real_core_smoke_without_input(self):
        from macro_library import MacroLibrary
        from visual_workflows import WorkflowStore, WorkflowRunner, new_workflow
        self.p.library.atomic_write = MacroLibrary.atomic_write
        self.p.verify_target.return_value = self.window
        self.p.GLib.idle_add.side_effect = lambda callback, *args: callback(*args)
        workflow = new_workflow('supported', 'Smoke')
        workflow.update(automatic=True, steps=[])
        with patch.object(self.module, 'WorkflowStore', WorkflowStore), \
             patch.object(self.module, 'WorkflowRunner', WorkflowRunner):
            adapter = self.module.VisualIntegration(self.p)
            adapter.store.put(workflow)
            # Run synchronously at the worker boundary, but use the real core.
            with patch.object(self.module.threading, 'Thread',
                              side_effect=lambda **kw: types.SimpleNamespace(start=kw['target'])):
                adapter.reconcile()
            self.assertFalse(adapter.active)
            self.assertIn(session_key(*self.game), adapter.claims)
            self.assertFalse(self.p.sessions.claim_first(self.game))
            self.assertEqual(adapter.status['state'], 'completed')

    def test_real_corrupt_workflow_storage_preserved(self):
        from visual_workflows import WorkflowStore
        path = self.p.library.path.with_name('workflows.json')
        for contents in (b'{broken', b'{"version":99,"workflows":[]}',
                         b'{"version":1,"workflows":"invalid"}'):
            path.write_bytes(contents)
            with patch.object(self.module, 'WorkflowStore', WorkflowStore):
                adapter = self.module.VisualIntegration(self.p)
            self.assertTrue(adapter.error)
            self.assertEqual(adapter.status['state'], 'failed')
            self.assertEqual(adapter.store.workflows, [])
            self.assertIsNone(adapter.store.get('missing'))
            self.assertIsNone(adapter.store.automatic('supported'))
            for method in ('put', 'delete', 'import_image', 'image_path'):
                with self.assertRaisesRegex(ValueError, 'storage unavailable'):
                    getattr(adapter.store, method)('anything')
            adapter.reconcile()
            self.assertFalse(adapter.active)
            self.assertEqual(path.read_bytes(), contents)

    def test_thread_start_failure_retains_claim_without_retry(self):
        self.module.threading.Thread.return_value.start.side_effect = RuntimeError('no thread')
        self.visual.reconcile()
        self.assertFalse(self.visual.active)
        self.assertIsNone(self.visual.worker)
        self.assertEqual(self.visual.status['state'], 'failed')
        self.assertIn(session_key(*self.game), self.visual.claims)
        self.visual.reconcile()
        self.assertEqual(self.core.WorkflowRunner.call_count, 1)

    def test_automatic_start_exception_reports_failure(self):
        self.visual.store.automatic.side_effect = ValueError('broken workflow')
        self.visual.reconcile()
        self.assertEqual(self.visual.status['state'], 'failed')
        self.assertIn('broken workflow', self.visual.status['message'])

    def test_resume_focus_failure_cancels(self):
        self.visual.reconcile()
        runner = self.visual.runner
        self.visual.pause()
        self.p.verify_target.side_effect = ValueError('wrong focus')
        with self.assertRaisesRegex(ValueError, 'wrong focus'):
            self.visual.resume()
        self.assertTrue(runner.cancel.is_set())


class PickerVisualGtkTests(unittest.TestCase):
    def test_f9_owned_session_opens_panel_without_login_and_blocks_manual_macro(self):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
        except (ImportError, ValueError):
            self.skipTest('GTK unavailable')
        if not Gtk.init_check()[0]:
            self.skipTest('GTK display unavailable')
        from auto_macro import Picker
        picker = Picker(preview=True)
        self.addCleanup(picker.window.destroy)
        window = types.SimpleNamespace(address='game', title='Supported game')
        profile = types.SimpleNamespace(title_re='Supported game')
        game = window, profile
        picker.sessions.games = {'game': game}
        picker.game = game
        picker.visual.game = game
        picker.visual.runner = types.SimpleNamespace(cancel=threading.Event(), paused=threading.Event())
        with patch.object(picker, 'scan'), patch.object(picker, 'render') as render, \
             patch.object(picker, 'run_login') as login_run, \
             patch.object(picker, 'launch') as launch, \
             patch('auto_macro.login.active_window', return_value=window):
            picker.open(auto=True)
            login_run.assert_not_called()
            render.assert_called_once()
            self.assertTrue(picker.visual.runner.paused.is_set())
            with patch.object(picker, 'verify_target', side_effect=ValueError('panel focused')):
                picker.visual.reconcile()
            self.assertTrue(picker.visual.runner.paused.is_set())
            picker.run_macro(dict(hidden=False, enabled=True))
            launch.assert_not_called()
            self.assertFalse(picker.visual.runner.cancel.is_set())
            picker.hide()
            self.assertTrue(picker.visual.runner.paused.is_set())
            picker.visual.runner.paused.clear()
            picker.manager = Mock()
            picker.manage()
            self.assertTrue(picker.visual.runner.paused.is_set())
            picker.manager.show.assert_called_once()
            # A paused workflow for A must not consume B's first-login claim.
            other = types.SimpleNamespace(address='other', title='Other game')
            picker.sessions.games['other'] = (other, types.SimpleNamespace(title_re='Other game'))
            with patch('auto_macro.login.active_window', return_value=other), \
                 patch.object(picker.library, 'auto_for', return_value={'id': 'other-login'}), \
                 patch.object(picker.sessions, 'claim_first') as claim:
                picker.open(auto=True)
                claim.assert_not_called()


if __name__ == '__main__':
    unittest.main()
