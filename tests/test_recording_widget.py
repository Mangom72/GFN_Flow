"""Passive recording HUD layout and lifecycle; only synthetic recording data."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro, BUTTONS
from macro_manager import Manager
from macro_recorder import recorded_action_hint
from ui_recording import RecordingWidget, elapsed_text, recording_margins


class RecordingWidgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.profile = login.Profile('Demo', 'Demo', '', '', 1920, 1080, [])
        library = MacroLibrary(Path(temp.name) / 'library.json')
        with patch.object(login, 'load_profiles', return_value=({}, [self.profile])):
            self.picker = Picker(preview=True, library=library)
        self.addCleanup(self.picker.window.destroy)
        self.game = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 1280, 720)
        self.hud = RecordingWidget(self.picker)
        self.picker.recording_widget = self.hud

    def pump(self):
        loop = self.picker.GLib.MainLoop()
        self.picker.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
        loop.run()

    def test_timer_count_translation_and_compact_layout(self):
        self.hud.show(self.game)
        self.hud.update(23, 75)
        self.pump()
        self.assertEqual(self.hud.clock.get_text(), '01:15')
        self.assertEqual(self.hud.count.get_text(), '23 actions')
        self.assertIn('F9', self.hud.window.get_accessible().get_description())
        for lang in ('ko', 'en'):
            self.picker.localizer.set_language(lang)
            self.hud.update(1000, 300)
            self.pump()
            width, height = self.hud.window.get_size()
            self.assertLess(width, 300)
            self.assertLess(height, 70)
            for label in (self.hud.clock, self.hud.count):
                self.assertGreaterEqual(label.get_allocated_width(), label.get_preferred_width()[0])
        self.hud.update(1, 0)
        self.assertEqual(self.hud.count.get_text(), '1 action')
        self.picker.localizer.set_language('ko')
        self.assertEqual(self.hud.count.get_text(), '동작 1개')
        self.assertEqual(self.hud.window.get_accessible().get_name(), '녹화 진행 상황')

    def test_latest_action_is_localized_retained_when_idle_and_reset_next_take(self):
        self.hud.show(self.game)
        self.assertEqual(self.hud.latest.get_text(), 'Waiting for input')
        self.hud.update(2, 1, recorded_action_hint('button:Left,1'))
        self.assertEqual(self.hud.latest.get_text(), 'Left mouse pressed')
        self.hud.update(2, 2)
        self.assertEqual(self.hud.latest.get_text(), 'Left mouse pressed')
        self.picker.localizer.set_language('ko')
        self.assertEqual(self.hud.latest.get_text(), '왼쪽 마우스 누름')
        self.assertIn('왼쪽 마우스 누름', self.hud.window.get_accessible().get_description())
        self.hud.hide()
        self.hud.show(self.game)
        self.assertEqual(self.hud.latest.get_text(), '입력 대기 중')

    def test_latest_action_remains_compact_for_long_coordinates_and_key_labels(self):
        self.hud.show(self.game)
        for lang in ('en', 'ko'):
            self.picker.localizer.set_language(lang)
            for action in ('move:10000,10000', 'up:BACKSPACE', 'wheel:-120,120'):
                self.hud.update(1000, 300, recorded_action_hint(action))
                self.pump()
                self.assertLess(self.hud.window.get_size().width, 300)
                self.assertLess(self.hud.window.get_size().height, 70)
                self.assertGreater(self.hud.latest.get_allocated_width(), 100)

    def test_all_mouse_button_names_are_translated_in_korean(self):
        self.picker.localizer.set_language('ko')
        for button in BUTTONS:
            self.hud.update(1, 1, recorded_action_hint(f'button:{button},1'))
            self.assertNotIn(button, self.hud.latest.get_text())
        self.hud.update(1, 1, recorded_action_hint('button:Back,1'))
        self.assertEqual(self.hud.latest.get_text(), '뒤로 마우스 누름')

    def test_latest_action_is_not_written_to_shared_status(self):
        self.picker.recorder = Mock()
        hint = recorded_action_hint('move:640,360')
        with patch.object(self.picker, 'publish_playback'):
            self.picker.record_progress(3, 1, hint)
        self.assertEqual(self.hud.latest.get_text(), 'Move to 640, 360')
        self.assertNotIn('hint', self.picker.playback)
        self.assertNotIn('640', repr(self.picker.playback))

    def test_surface_is_passive_before_map_and_after_reopen(self):
        self.hud.window.realize()
        self.assertFalse(self.hud.window.get_accept_focus())
        self.assertFalse(self.hud.window.get_focus_on_map())
        self.assertTrue(self.hud.window.get_window().get_pass_through())
        for _ in range(2):
            self.hud.show(self.game)
            self.pump()
            self.assertTrue(self.hud.window.get_window().get_pass_through())
            self.assertEqual(self.hud.window.get_window().get_children(), [])
            self.hud.hide()

    def test_new_recording_resets_progress_and_late_updates_do_not_reopen(self):
        self.hud.show(self.game)
        self.hud.update(99, 40)
        self.hud.hide()
        self.picker.recorder = None
        self.picker.record_progress(999, 60)
        self.assertFalse(self.hud.window.get_visible())
        self.hud.show(self.game)
        self.assertEqual(self.hud.clock.get_text(), '00:00')
        self.assertEqual(self.hud.count.get_text(), '0 actions')

    def test_start_progress_and_all_stop_reasons_follow_recorder_lifecycle(self):
        manager = Manager(self.picker, offscreen=True, width=360, height=420)
        self.addCleanup(manager.window.destroy)
        macro = new_macro('normal')
        macro.update(name='Demo', game='Demo', title_re='Demo', steps=['key:Enter'])
        manager.edit(macro)
        for explicit, reason in ((True, 'Recording stopped.'),
                                 (False, 'Recording stopped: game focus changed or input device unavailable.'),
                                 (False, 'Action limit reached.')):
            with self.subTest(reason=reason):
                manager.record_setup()
                manager.arm_recording()
                self.assertFalse(self.hud.window.get_visible())
                with patch.object(self.picker, 'verify_target', return_value=self.game), \
                     patch('macro_recorder.Recorder.start'):
                    self.picker.start_recording(manager, (self.game, self.profile))
                self.assertTrue(self.hud.window.get_visible())
                self.assertFalse(self.picker.window.get_visible())
                self.picker.record_progress(12, 8)
                self.assertEqual(self.hud.count.get_text(), '12 actions')
                if explicit:
                    self.picker.recorder.stop.set()
                with patch.object(manager, 'show') as show:
                    self.picker.recorded(['down:A', 'up:A'], reason)
                    self.assertEqual(show.called, explicit)
                self.assertFalse(self.hud.window.get_visible())
                self.assertFalse(self.picker.playback['recording'])
                self.picker.record_progress(55, 9)
                self.assertFalse(self.hud.window.get_visible())

    def test_failed_recorder_start_hides_hud_and_releases_busy_state(self):
        manager = Mock()
        manager.record_request = dict(keyboard=True, mouse=True, capacity=1000)
        with patch.object(self.picker, 'verify_target', return_value=self.game), \
             patch('macro_recorder.Recorder.start', side_effect=RuntimeError('thread unavailable')), \
             self.assertRaisesRegex(RuntimeError, 'thread unavailable'):
            self.picker.start_recording(manager, (self.game, self.profile))
        self.assertFalse(self.picker.busy)
        self.assertIsNone(self.picker.recorder)
        self.assertFalse(self.hud.window.get_visible())
        self.assertFalse(self.picker.playback['recording'])

    def test_empty_region_uses_only_passive_children(self):
        window = Mock()
        child = Mock()
        window.get_window.return_value.get_children.return_value = [child]
        self.hud.make_passive(window)
        child.set_pass_through.assert_called_once_with(True)
        window.get_window.return_value.set_child_input_shapes.assert_called_once_with()
        window.get_window.return_value.set_pass_through.assert_called_once_with(True)

    def test_placement_respects_bars_and_scaled_monitor_geometry(self):
        geometry = SimpleNamespace(x=1920, y=0, width=1280, height=720)
        monitor = dict(x=1920, y=0, width=1920, height=1080, scale=1.5,
                       reserved=[0, 40, 30, 0])
        self.assertEqual(recording_margins(geometry, geometry, [monitor]), (48, 38))
        self.assertEqual(recording_margins(geometry, geometry, []), (12, 12))
        workarea = SimpleNamespace(x=1920, y=50, width=1260, height=670)
        self.assertEqual(recording_margins(geometry, workarea, [monitor]), (58, 38))

    def test_elapsed_format(self):
        self.assertEqual([elapsed_text(s) for s in (-1, 0, 59, 60, 300)],
                         ['00:00', '00:00', '00:59', '01:00', '05:00'])


if __name__ == '__main__':
    unittest.main()
