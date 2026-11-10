"""Playback HUD with native offscreen GTK and no game input or credentials."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_macro import Picker
from detection import login
from macro_engine import Cancelled
from macro_library import MacroLibrary, new_macro
from ui_localization import KO
from ui_playback import ACTION_LABELS, PlaybackWidget


class PlaybackWidgetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        profile = login.Profile('Demo', 'Demo', '', '', 1920, 1080, [])
        library = MacroLibrary(Path(directory.name) / 'library.json')
        with patch.object(login, 'load_profiles', return_value=({}, [profile])):
            self.picker = Picker(preview=True, library=library)
        self.addCleanup(self.picker.window.destroy)
        self.game = login.Window('demo', 'Demo', 'GeForceNOW', 0, 0, 1280, 720)
        self.picker.game = (self.game, profile)
        self.hud = PlaybackWidget(self.picker)
        self.picker.playback_widget = self.hud

    def pump(self, milliseconds=80):
        loop = self.picker.GLib.MainLoop()
        self.picker.GLib.timeout_add(milliseconds, lambda: (loop.quit(), False)[1])
        loop.run()

    def test_clock_keeps_advancing_during_wait_and_repeats_count_all_actions(self):
        with patch('ui_playback.time.monotonic', return_value=100):
            self.hud.show(self.game, 'Daily quest', 4)
        with patch('ui_playback.time.monotonic', return_value=101):
            self.hud.set_progress(2, 4, 2, 3, 'wait')
        self.assertEqual(self.hud.count.get_text(), '41%')
        self.assertEqual(self.hud.repeat_stat.get_text(), '↻ 2/3')
        self.assertEqual(self.hud.action_stat.get_text(), '# 2/4')
        self.assertEqual(self.hud.total_stat.get_text(), 'Σ 5/12')
        self.assertEqual(self.hud.clock.get_text(), '00:01')
        self.assertEqual(self.hud.latest.get_text(), 'Waiting · Daily quest')
        with patch('ui_playback.time.monotonic', return_value=165):
            self.pump(300)  # A real GLib timer, with no further action callbacks.
        self.assertEqual(self.hud.clock.get_text(), '01:05')
        self.assertIn('Repeat 2 of 3.', self.hud.window.get_accessible().get_description())

    def test_completed_counts_update_during_delay_between_repeats(self):
        self.hud.show(self.game, 'Demo', 4, 3)
        self.picker.runner = Mock(completed=3)
        self.hud.set_progress(4, 4, 1, 3, 'wait')
        self.assertEqual(self.hud.total_stat.get_text(), 'Σ 3/12')
        self.picker.runner.completed = 4
        self.hud.tick()
        self.assertEqual(self.hud.total_stat.get_text(), 'Σ 4/12')
        self.assertEqual(self.hud.count.get_text(), '33%')
        self.assertIn('1 repeats complete.', self.hud.window.get_accessible().get_description())

    def test_translation_and_long_name_layout_remain_compact(self):
        self.hud.show(self.game, 'Long macro name ' * 7, 1000)
        self.hud.set_progress(1000, 1000, 100, 100, 'pw')
        for language in ('ko', 'en'):
            self.picker.localizer.set_language(language)
            self.pump()
            width, height = self.hud.window.get_size()
            self.assertLess(width, 300)
            self.assertLess(height, 90)
            self.assertGreater(self.hud.latest.get_allocated_width(), 100)
            for label in (self.hud.clock, self.hud.count):
                self.assertGreaterEqual(label.get_allocated_width(), label.get_preferred_width()[0])
            self.assertEqual(self.hud.count.get_text(), '99%')
            self.assertEqual(self.hud.total_stat.get_text(), 'Σ 99999/100000')
        self.picker.localizer.set_language('ko')
        self.assertTrue(self.hud.latest.get_text().startswith('저장된 비밀번호 입력'))
        self.assertEqual(self.hud.window.get_accessible().get_name(), '재생 진행 상황')
        self.assertIn('F9', self.hud.window.get_accessible().get_description())
        self.assertTrue(all(key in KO for key in ACTION_LABELS.values()))

    def test_action_payloads_are_never_displayed(self):
        self.hud.show(self.game, 'Demo', 5)
        for action in ('id', 'pw', 'text', 'key', 'unknown'):
            self.hud.set_progress(1, 5, 1, 1, action + ':sensitive-test-value')
            rendered = (self.hud.latest.get_text(), self.hud.window.get_tooltip_text(),
                        self.hud.window.get_accessible().get_description())
            self.assertNotIn('sensitive-test-value', repr(rendered))

    def test_surface_stays_click_through_and_timer_is_removed_on_hide_or_destroy(self):
        for _ in range(2):
            self.hud.show(self.game, 'Demo', 5)
            self.pump()
            self.assertFalse(self.hud.window.get_accept_focus())
            self.assertFalse(self.hud.window.get_focus_on_map())
            self.assertTrue(self.hud.window.get_window().get_pass_through())
            self.assertEqual(self.hud.window.get_window().get_children(), [])
            source = self.hud.timer
            self.hud.hide()
            self.assertIsNone(self.hud.timer)
            self.assertIsNone(self.picker.GLib.MainContext.default().find_source_by_id(source))
        self.hud.show(self.game, 'Another macro', 2)
        self.assertEqual(self.hud.count.get_text(), '0%')
        self.assertTrue(self.hud.latest.get_text().startswith('Starting playback'))
        source = self.hud.timer
        self.hud.window.destroy()
        self.assertIsNone(self.picker.GLib.MainContext.default().find_source_by_id(source))

    def test_real_launch_and_finish_cover_login_normal_cancel_and_error(self):
        original_show = self.hud.show
        def preview_show(*args):
            with patch.object(self.picker, 'preview', True):
                original_show(*args)
        layer = SimpleNamespace(KeyboardMode=SimpleNamespace(NONE='none'), set_keyboard_mode=Mock())
        cases = (('login', None), ('normal', None),
                 ('normal', Cancelled('Playback cancelled.')), ('login', ValueError('Test failure')))
        for kind, failure in cases:
            with self.subTest(kind=kind, failure=type(failure).__name__):
                self.picker.hide()
                macro = new_macro(kind)
                macro.update(name='Demo macro', game='Demo', title_re='Demo', steps=['wait:2000'])
                self.picker.library.put(macro)
                def play(*_args, **_kwargs):
                    self.picker.progress(1, 1, 1, 1, 'wait')
                    if failure:
                        raise failure
                with patch.object(self.picker, 'preview', False), \
                     patch.object(self.picker, 'Layer', layer), \
                     patch.object(self.picker, 'publish_playback'), \
                     patch.object(self.picker, 'persist'), patch.object(self.picker, 'scan'), \
                     patch.object(self.picker, 'verify_target', return_value=self.game), \
                     patch.object(self.picker.window, 'get_display', return_value=SimpleNamespace(sync=Mock())), \
                     patch.object(self.hud, 'show', side_effect=preview_show), \
                     patch('auto_macro.MacroRunner.run', side_effect=play), \
                     patch('auto_macro.threading.Thread') as thread, \
                     patch.object(self.picker.GLib, 'idle_add') as idle:
                    self.picker.run_macro(macro)
                    self.assertTrue(self.hud.window.get_visible())
                    self.assertFalse(self.picker.window.get_visible())
                    if isinstance(failure, Cancelled):
                        self.picker.open()  # F9 stops the active runner, not the HUD.
                        self.assertTrue(self.picker.runner.cancel.is_set())
                    thread.call_args.kwargs['target']()
                    callback, *args = idle.call_args.args
                    # Rendering in offscreen mode avoids any real layer/focus call.
                    with patch.object(self.picker, 'preview', True), patch('ui_playback.RESULT_HOLD_MS', 30):
                        callback(*args)
                self.assertTrue(self.hud.window.get_visible())
                self.assertIsNone(self.hud.timer)
                self.assertIsNotNone(self.hud.dismiss_timer)
                self.assertFalse(self.hud.shortcut.get_visible())
                self.assertEqual(self.hud.outcome, 'cancelled' if isinstance(failure, Cancelled) else 'failed' if failure else 'completed')
                self.pump()
                self.assertFalse(self.hud.window.get_visible())
                self.assertIsNone(self.hud.dismiss_timer)
                self.assertFalse(self.picker.busy)
                self.assertEqual(self.picker.window.get_visible(), kind == 'normal' or failure is not None)
                self.picker.progress(99, 100, 1, 1, 'text')
                self.assertFalse(self.hud.window.get_visible())
                self.assertEqual(self.picker.playback['step'], 1)

    def test_thread_start_failure_cleans_hud_and_utilities_do_not_show_it(self):
        layer = SimpleNamespace(KeyboardMode=SimpleNamespace(NONE='none'), set_keyboard_mode=Mock())
        original_show = self.hud.show
        def preview_show(*args):
            with patch.object(self.picker, 'preview', True):
                original_show(*args)
        with patch.object(self.picker, 'preview', False), \
             patch.object(self.picker, 'Layer', layer), \
             patch.object(self.picker.window, 'get_display', return_value=SimpleNamespace(sync=Mock())), \
             patch.object(self.picker, 'publish_playback'), patch.object(self.picker, 'scan'), \
             patch.object(self.picker, 'render'), \
             patch.object(self.hud, 'show', side_effect=preview_show) as show, \
             patch('auto_macro.threading.Thread') as thread:
            self.picker.launch(Mock(), 'Utility complete')
            show.assert_not_called()
            self.picker.busy = False
            self.picker.runner = Mock()
            self.picker.playback.update(running=True, name='Demo', total=2)
            thread.return_value.start.side_effect = RuntimeError('Thread unavailable')
            self.picker.launch(Mock(), 'Complete')
            self.assertTrue(self.hud.window.get_visible())
            self.assertEqual(self.hud.outcome, 'failed')
            self.assertIsNone(self.hud.timer)
            self.assertFalse(self.picker.busy)
            self.assertFalse(self.picker.playback['running'])
            self.assertIsNone(self.picker.runner)

    def test_unavailable_hud_does_not_prevent_playback(self):
        layer = SimpleNamespace(KeyboardMode=SimpleNamespace(NONE='none'), set_keyboard_mode=Mock())
        for failure in ('constructor', 'show'):
            with self.subTest(failure=failure):
                self.picker.playback_widget = None if failure == 'constructor' else self.hud
                self.picker.runner = Mock()
                self.picker.playback.update(running=True, name='Demo', total=2)
                work = Mock()
                with patch.object(self.picker, 'preview', False), \
                     patch.object(self.picker, 'Layer', layer), \
                     patch.object(self.picker.window, 'get_display', return_value=SimpleNamespace(sync=Mock())), \
                     patch.object(self.picker, 'publish_playback'), patch.object(self.picker, 'scan'), \
                     patch.object(self.picker, 'render'), \
                     patch('ui_playback.PlaybackWidget', side_effect=RuntimeError('Unavailable')), \
                     patch.object(self.hud, 'show', side_effect=RuntimeError('Unavailable')), \
                     patch('auto_macro.threading.Thread') as thread, \
                     patch.object(self.picker.GLib, 'idle_add') as idle:
                    self.picker.launch(work, 'Complete', reopen_on_success=False)
                    self.assertTrue(self.picker.busy)
                    self.assertIsNone(self.picker.playback_widget)
                    thread.return_value.start.assert_called_once()
                    thread.call_args.kwargs['target']()
                    work.assert_called_once()
                    callback, *args = idle.call_args.args
                    callback(*args)
                self.assertFalse(self.picker.busy)
                self.assertFalse(self.picker.playback['running'])

    def test_result_holds_for_three_seconds_with_frozen_time_and_no_stop_hint(self):
        with patch('ui_playback.time.monotonic', return_value=100):
            self.hud.show(self.game, 'Demo', 4, 3)
            self.pump()
        initial_width = self.hud.window.get_size().width
        self.hud.set_progress(4, 4, 3, 3, 'wait')
        timeout = self.picker.GLib.timeout_add
        with patch.object(self.picker.GLib, 'timeout_add', wraps=timeout) as schedule, \
             patch('ui_playback.time.monotonic', return_value=175):
            self.hud.finish()
        self.assertEqual(schedule.call_args.args[0], 3000)
        self.pump()
        self.assertEqual(self.hud.window.get_size().width, initial_width)
        self.assertTrue(self.hud.window.get_visible())
        self.assertEqual(self.hud.clock.get_text(), '01:15')
        self.assertEqual(self.hud.count.get_text(), '100%')
        self.assertEqual(self.hud.repeat_stat.get_text(), '↻ 3/3')
        self.assertEqual(self.hud.total_stat.get_text(), 'Σ 12/12')
        self.assertTrue(self.hud.latest.get_text().startswith('Completed'))
        self.assertFalse(self.hud.shortcut.get_visible())
        self.assertNotIn('F9', self.hud.window.get_accessible().get_description())
        self.assertFalse(self.hud.tick())
        self.hud.set_progress(1, 4, 1, 3, 'text')
        self.assertEqual(self.hud.clock.get_text(), '01:15')
        self.assertEqual(self.hud.count.get_text(), '100%')
        self.picker.localizer.set_language('ko')
        self.assertTrue(self.hud.latest.get_text().startswith('완료'))

    def test_new_playback_cancels_old_result_dismissal(self):
        self.hud.show(self.game, 'First', 4, 3)
        self.hud.set_progress(2, 4, 2, 3, 'wait')
        with patch('ui_playback.RESULT_HOLD_MS', 30):
            self.hud.finish('cancelled', completed=5)
        source = self.hud.dismiss_timer
        self.assertEqual(self.hud.total_stat.get_text(), 'Σ 5/12')
        self.hud.show(self.game, 'Second', 2, 5)
        self.pump()
        self.assertIsNone(self.picker.GLib.MainContext.default().find_source_by_id(source))
        self.assertTrue(self.hud.window.get_visible())
        self.assertIsNone(self.hud.outcome)
        self.assertTrue(self.hud.shortcut.get_visible())
        self.assertEqual(self.hud.repeat_stat.get_text(), '↻ 1/5')
        self.assertEqual(self.hud.total_stat.get_text(), 'Σ 0/10')

    def test_summary_destruction_removes_dismissal_timer(self):
        self.hud.show(self.game, 'Demo', 4)
        self.hud.finish('failed', completed=2)
        self.assertEqual(self.hud.count.get_text(), '50%')
        source = self.hud.dismiss_timer
        self.hud.window.destroy()
        self.assertIsNone(self.picker.GLib.MainContext.default().find_source_by_id(source))

    def test_failed_result_display_still_releases_playback_state(self):
        self.hud.show(self.game, 'Demo', 4)
        self.picker.runner = Mock(completed=2)
        self.picker.playback['running'] = True
        self.picker.busy = True
        with patch.object(self.hud, 'finish', side_effect=RuntimeError('Unavailable')), \
             patch.object(self.picker, 'scan'):
            self.picker.finished('Complete', reopen=False)
        self.assertFalse(self.picker.busy)
        self.assertFalse(self.picker.playback['running'])
        self.assertIsNone(self.picker.runner)
        self.assertIsNone(self.picker.playback_widget)

    def test_recording_replaces_lingering_playback_result(self):
        self.hud.show(self.game, 'Demo', 4)
        self.hud.finish()
        manager = Mock(record_request=dict(keyboard=True, mouse=True, capacity=1000))
        with patch.object(self.picker, 'verify_target', return_value=self.game), \
             patch('macro_recorder.Recorder.start'):
            self.picker.start_recording(manager, self.picker.game)
        self.assertFalse(self.hud.window.get_visible())
        self.assertIsNone(self.hud.dismiss_timer)
        self.assertTrue(self.picker.recording_widget.window.get_visible())
        self.picker.recorded([], 'Recording stopped.')

    def test_start_has_repeat_and_login_cutoff_totals_before_first_action(self):
        macro = new_macro('login')
        macro.update(game='Demo', title_re='Demo', repeat=3, login_limit=2)
        with patch.object(self.picker, 'persist'), patch.object(self.picker, 'launch'):
            self.picker.run_macro(macro)
            self.assertEqual(self.picker.playback['repeats'], 3)
            self.assertEqual(self.picker.playback['total'], 2)
            # Older compatible macros can omit the optional login cutoff.
            macro.pop('login_limit')
            self.picker.run_macro(macro)
            self.assertEqual(self.picker.playback['total'], 4)


if __name__ == '__main__':
    unittest.main()
