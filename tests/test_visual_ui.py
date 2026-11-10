"""Real GTK construction and draft editing; no desktop input or detection."""
import copy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro
from macro_manager import Manager
from visual_workflows import new_workflow


class MemoryStore:
    def __init__(self):
        self.workflows = []

    def get(self, identity):
        return next((w for w in self.workflows if w['id'] == identity), None)

    def put(self, workflow):
        self.workflows = [w for w in self.workflows if w['id'] != workflow['id']]
        if workflow['automatic']:
            for other in self.workflows:
                if other['game'] == workflow['game']:
                    other['automatic'] = False
        self.workflows.append(copy.deepcopy(workflow))

    def delete(self, identity):
        self.workflows = [w for w in self.workflows if w['id'] != identity]

    def automatic(self, pattern):
        return next((w for w in self.workflows if w['game'] == pattern and w['automatic']), None)


class VisualUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = login.Profile('Demo', '^Demo$', '', '', 1920, 1080, ['enter'])
        self.library = MacroLibrary(Path(self.tmp.name) / 'library.json', [self.profile])
        self.picker = Picker(preview=True, width=360, library=self.library)
        self.picker.legacy_profiles = [self.profile]
        self.picker.scan = lambda: None
        self.store = MemoryStore()
        self.picker.visual = SimpleNamespace(store=self.store, status=dict(state='idle', step=0, message='Ready'),
            start=Mock(), pause=Mock(), resume=Mock(), stop=Mock(), runner=None, active=False,
            pattern=lambda game: game[1].title_re if game[1] else None, _release_focus=Mock())
        self.picker.verify_target = Mock(side_effect=lambda game: game[0])
        self.manager = Manager(self.picker, offscreen=True, width=360, height=600)
        self.ui = self.manager.visual_page
        self.addCleanup(self.picker.window.destroy)
        self.addCleanup(self.manager.window.destroy)

    def pump(self):
        while self.picker.Gtk.events_pending():
            self.picker.Gtk.main_iteration_do(False)

    def wait_worker(self):
        end = time.monotonic() + 3
        while self.ui.busy and time.monotonic() < end:
            self.pump()
            threading.Event().wait(.01)
        self.pump()
        self.assertFalse(self.ui.busy)

    def controls(self, root=None):
        root = root or self.ui.form
        yield root
        if isinstance(root, self.picker.Gtk.Container):
            for child in root.get_children():
                yield from self.controls(child)

    def control(self, name):
        return next(w for w in self.controls() if w.get_accessible().get_name() == name
                    and isinstance(w, (self.picker.Gtk.Entry, self.picker.Gtk.ComboBoxText, self.picker.Gtk.CheckButton)))

    def test_tab_order_narrow_width_and_existing_groups(self):
        self.ui.new()
        self.ui.draft['name'] = 'A long workflow name ' * 4
        self.ui.add_step()
        self.ui.step()['name'] = 'A long step name ' * 5
        for kind in ('click', 'key', 'wait', 'macro'):
            self.ui.add_type.set_active_id(kind)
            self.ui.add_action()
        self.ui.step()['actions'][-1]['macro_id'] = self.library.macros[0]['id']
        self.ui.save()
        self.manager.show()
        self.manager.tabs.set_current_page(2)
        self.pump()
        names = [self.manager.tabs.get_tab_label(self.manager.tabs.get_nth_page(i)).get_accessible().get_name()
                 for i in range(self.manager.tabs.get_n_pages())]
        self.assertEqual(names, ['Library', 'History', 'Visual Workflows', 'Login Credentials', 'Blacklist'])
        self.assertLessEqual(self.ui.form.get_preferred_width()[0], 336)
        widths = {name: getattr(self.manager, name).get_preferred_width()[0]
                  for name in ('root', 'views', 'tabs')}
        self.assertLessEqual(self.manager.window.get_allocated_width(), 360, widths)
        self.assertEqual(len(self.manager.rows), 2)
        self.assertTrue(self.ui.scroll.get_vadjustment().get_upper() > 600)

    def test_crud_automatic_and_draft_isolation(self):
        self.ui.new()
        self.control('Name').set_text('First')
        self.control('Run automatically for this game').set_active(True)
        self.ui.save()
        first = self.ui.draft['id']
        self.ui.duplicate()
        self.assertNotEqual(first, self.ui.draft['id'])
        self.assertFalse(self.ui.draft['automatic'])
        self.control('Run automatically for this game').set_active(True)
        self.ui.save()
        second = self.ui.draft['id']
        self.assertEqual(self.store.automatic('^Demo$')['id'], second)
        self.assertFalse(self.store.get(first)['automatic'])
        self.ui.selector.set_active_id(first)
        self.control('Name').set_text('Unsaved')
        self.assertEqual(self.store.get(first)['name'], 'First')
        self.ui.delete()
        self.assertIsNone(self.store.get(first))

    def test_steps_and_multiple_typed_actions(self):
        self.ui.new()
        self.ui.add_step()
        self.control('Step name').set_text('One')
        self.ui.add_step()
        self.control('Step name').set_text('Two')
        self.ui.move_step(-1)
        self.assertEqual([s['name'] for s in self.ui.draft['steps']], ['Two', 'One'])
        for kind in ('click', 'key', 'wait', 'macro'):
            self.ui.add_type.set_active_id(kind)
            self.ui.add_action()
        self.control('Existing macro').set_active_id(self.library.macros[0]['id'])
        self.assertEqual(self.ui.step()['actions'][-1]['macro_id'], self.library.macros[0]['id'])
        self.ui.move_action(-1)
        self.assertEqual([a['type'] for a in self.ui.step()['actions']], ['click', 'key', 'macro', 'wait'])
        self.ui.remove_action()
        self.assertEqual(len(self.ui.step()['actions']), 3)
        self.ui.remove_step()
        self.assertEqual(len(self.ui.draft['steps']), 1)

    def test_game_options_are_library_only_and_macro_exact_pattern(self):
        other = new_macro('normal')
        other.update(game='Other', title_re='Other', name='Other macro')
        self.library.put(other)
        self.ui.new()
        self.ui.add_step()
        self.ui.add_type.set_active_id('macro')
        self.ui.add_action()
        combo = self.control('Existing macro')
        self.assertEqual(len(combo.get_model()), 1)
        self.assertEqual(len(self.control('Game').get_model()), len(self.library.game_options([self.profile])))
        self.ui.draft['game'] = 'Unregistered'
        with self.assertRaises(ValueError):
            self.ui.save()

    def test_capture_rejects_unrecognized_game_and_recognition_is_input_free(self):
        self.ui.new()
        self.ui.add_step()
        window = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 1920, 1080)
        self.picker.game = (window, self.profile)
        self.picker.sessions.games = {}
        with self.assertRaises(ValueError):
            self.ui.capture()
        self.picker.sessions.games = {'demo': self.picker.game}
        self.ui.step()['image'] = 'template.png'
        with patch('visual_images.recognize', return_value=dict(x=20, y=30, score=.95)) as recognize, \
                patch('ui_visual_workflows.time.sleep'), patch.object(self.manager, 'show') as show:
            self.ui.test()
            self.wait_worker()
        recognize.assert_called_once()
        self.picker.visual._release_focus.assert_called_once()
        show.assert_called_once_with(refresh=False)
        self.picker.visual.start.assert_not_called()
        self.assertIn('No input sent', self.manager.notice.get_text())

    def test_crop_worker_and_runtime_controls(self):
        from PIL import Image
        self.ui.new()
        self.ui.add_step()
        self.ui.image = Image.new('RGB', (200, 100), 'red')
        self.ui.crop = [10, 20, 50, 40]
        self.store.import_image = Mock(return_value='crop.png')
        self.ui.apply_crop()
        self.wait_worker()
        self.assertEqual(self.ui.image.size, (50, 40))
        self.assertEqual(self.ui.step()['image'], 'crop.png')
        self.ui.crop = [40, 0, 50, 40]
        with self.assertRaises(ValueError):
            self.ui.apply_crop()
        self.ui.pause()
        self.ui.resume()
        self.ui.stop()
        self.picker.visual.pause.assert_called_once()
        self.picker.visual.resume.assert_called_once()
        self.picker.visual.stop.assert_called_once()
        self.picker.visual.status.update(state='paused', step=2, message='Waiting')
        self.ui.update_status()
        self.assertIn('paused · Step 3 · Waiting', self.ui.status.get_text())

    def test_opening_manager_pauses_active_workflow_before_editing(self):
        self.picker.visual.active = True
        self.manager.show()
        self.picker.visual.pause.assert_called_once()
        self.picker.visual.resume.assert_not_called()

    def test_storage_error_is_visible_on_empty_page_and_status(self):
        error = 'Workflow storage is invalid. Original file preserved.'
        self.picker.visual.error = error
        self.ui.refresh()
        self.ui.update_status()
        self.assertEqual(self.ui.status.get_text(), error)
        labels = [w.get_text() for w in self.controls() if isinstance(w, self.picker.Gtk.Label)]
        self.assertIn(error, labels)

    def test_capture_countdown_worker_and_save_with_real_store(self):
        from PIL import Image
        from visual_workflows import WorkflowStore
        self.picker.visual.store = WorkflowStore(self.library)
        self.ui.new()
        self.ui.add_step()
        window = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 200, 100)
        game = (window, self.profile)
        self.picker.sessions.games = {'demo': game}
        with patch('visual_images.capture', return_value=Image.new('RGB', (200, 100))) as capture, \
                patch('ui_visual_workflows.time.sleep'), patch.object(self.manager, 'hide') as hide:
            self.ui.capture()
            self.wait_worker()
        hide.assert_called_once()
        capture.assert_called_once_with(window)
        self.picker.visual._release_focus.assert_called_once()
        self.ui.crop = [5, 10, 40, 30]
        self.ui.apply_crop()
        self.wait_worker()
        self.ui.step()['condition'] = 'image'
        for kind in ('click', 'key', 'wait', 'macro'):
            self.ui.add_type.set_active_id(kind)
            self.ui.add_action()
        self.control('Existing macro').set_active_id(self.library.macros[0]['id'])
        self.ui.save()
        saved = self.ui.store.get(self.ui.draft['id'])
        self.assertEqual(len(saved['steps'][0]['actions']), 4)
        self.assertTrue(self.ui.store.image_path(saved['steps'][0]['image']).exists())
        self.picker.visual.start.assert_not_called()

    def test_identity_uses_exact_adapter_pattern_not_matching_title(self):
        self.ui.new()
        self.ui.add_step()
        window = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 200, 100)
        other = login.Profile('Other identity', 'Demo', '', '', 1920, 1080, [])
        self.picker.game = (window, other)
        self.picker.sessions.games = {'demo': self.picker.game}
        with patch('visual_images.capture') as capture:
            with self.assertRaises(ValueError):
                self.ui.capture()
        capture.assert_not_called()
        self.picker.visual._release_focus.assert_not_called()

    def test_capture_refreshes_geometry_after_countdown(self):
        from PIL import Image
        import visual_images
        self.ui.new()
        self.ui.add_step()
        stale = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 200, 100)
        fresh = login.Window('demo', 'Demo', 'GeForceNOW', 50, 70, 400, 300)
        game = (stale, self.profile)
        self.picker.game = game
        self.picker.sessions.games = {'demo': game}
        self.picker.verify_target.side_effect = lambda target: fresh
        screenshot = Image.new('RGB', (400, 300))
        with patch('ui_visual_workflows.time.sleep') as countdown, \
                patch('visual_images.capture', return_value=screenshot) as capture, \
                patch('visual_images.normalize', wraps=visual_images.normalize) as normalize, \
                patch.object(self.manager, 'show') as show:
            self.ui.capture()
            self.wait_worker()
        countdown.assert_called_once_with(3)
        self.picker.verify_target.assert_called_once_with(game)
        capture.assert_called_once_with(fresh)
        normalize.assert_called_once_with(screenshot, (1920, 1080))
        show.assert_called_once_with(refresh=False)

    def test_capture_aborts_changed_live_session_during_countdown(self):
        self.ui.new()
        self.ui.add_step()
        window = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 200, 100)
        self.picker.sessions.games = {'demo': (window, self.profile)}
        def disappear(_):
            self.picker.sessions.games.clear()
        with patch('ui_visual_workflows.time.sleep', side_effect=disappear), \
                patch('visual_images.capture') as capture, patch.object(self.manager, 'show') as show:
            self.ui.capture()
            self.wait_worker()
        capture.assert_not_called()
        self.picker.verify_target.assert_not_called()
        show.assert_called_once_with(refresh=False)
        self.assertIn('session changed', self.manager.notice.get_text())

    def test_recognition_verifies_fresh_window_and_restores_on_focus_error(self):
        self.ui.new()
        self.ui.add_step()
        self.ui.step()['image'] = 'template.png'
        stale = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 200, 100)
        fresh = login.Window('demo', 'Demo', 'GeForceNOW', 60, 90, 400, 200)
        game = (stale, self.profile)
        self.picker.sessions.games = {'demo': game}
        self.picker.verify_target.side_effect = lambda target: fresh
        with patch('ui_visual_workflows.time.sleep'), patch('visual_images.recognize', return_value=None) as recognize, \
                patch.object(self.manager, 'hide') as hide, patch.object(self.manager, 'show') as show:
            self.ui.test()
            self.wait_worker()
            hide.assert_called_once()
            show.assert_called_once_with(refresh=False)
        self.assertEqual(recognize.call_args.args[0], fresh)
        self.picker.verify_target.side_effect = login.LoginError('Wrong focused game')
        with patch('ui_visual_workflows.time.sleep'), patch('visual_images.recognize') as recognize, \
                patch.object(self.manager, 'show') as show:
            self.ui.test()
            self.wait_worker()
        recognize.assert_not_called()
        show.assert_called_once_with(refresh=False)
        self.picker.visual.start.assert_not_called()
        self.picker.visual.resume.assert_not_called()
        self.assertIn('Wrong focused game', self.manager.notice.get_text())

    def test_preview_rejects_oversized_image_before_copy(self):
        self.ui.new()
        self.ui.add_step()
        self.store.image_path = Mock(return_value='tampered.png')
        image = Mock(width=10000, height=10000)
        context = Mock()
        context.__enter__ = Mock(return_value=image)
        context.__exit__ = Mock(return_value=False)
        with patch('PIL.Image.open', return_value=context):
            self.ui.load_preview('tampered.png')
            self.wait_worker()
        image.copy.assert_not_called()
        self.assertIn('too large', self.manager.notice.get_text())


if __name__ == '__main__':
    unittest.main()
