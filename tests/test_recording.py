"""Recording uses synthetic input only; never read the user's live keystrokes."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from auto_macro import Picker
from detection import login
from macro_engine import MacroRunner, Cancelled
from macro_library import MacroLibrary, new_macro, parse_step, validate
from macro_manager import Manager
from macro_recorder import Timeline, Recorder, RecordingLimit, physical_input_path, recorded_action_hint


class RecordedActionHintTests(unittest.TestCase):
    def test_hints_distinguish_mouse_navigation_and_keyboard_states(self):
        cases = {'button:Right,0': ('{button} mouse released', {'button': 'Right'}),
                 'down:ENTER': ('{key} pressed', {'key': 'Enter'}),
                 'up:LEFTCTRL': ('{key} released', {'key': 'Ctrl'}),
                 'move:640,360': ('Move to {x}, {y}', {'x': 640, 'y': 360}),
                 'wheel:-1,2': ('Scroll ↔ {x}, ↕ {y}', {'x': -1, 'y': 2})}
        for step, expected in cases.items():
            self.assertEqual(recorded_action_hint(step), expected)
        self.assertIsNone(recorded_action_hint('wait:100'))

    def test_typed_characters_and_arbitrary_text_never_enter_hints(self):
        for kind in ('down', 'up'):
            for key in ('A', '1', 'KPA', 'KP1', 'SLASH', 'SPACE', 'PASSWORD_SAMPLE'):
                hint = recorded_action_hint(kind + ':' + key)
                self.assertEqual(hint, ('Key ' + ('pressed' if kind == 'down' else 'released'), {}))
        self.assertEqual(recorded_action_hint('text:private-password'), ('Text entered', {}))
        for malformed in ('button:private-password,1', 'move:private-password', 'unknown:private-password'):
            self.assertEqual(recorded_action_hint(malformed), ('Action recorded', {}))

    def test_progress_reports_latest_actual_action_without_changing_capture(self):
        source, progress, done = Mock(), Mock(), Mock()
        recorder = Recorder(lambda: object(), lambda _: None, done, progress, mouse=False,
                            source=lambda *_: source)
        def stop():
            recorder.stop.set()
            return []
        source.read.side_effect = [[(100.1, 'keyboard', 1, 30, 1), (100.2, 'keyboard', 1, 30, 0)], [], stop]
        with patch('macro_recorder.time.monotonic', side_effect=[100, 100.3, 100.6, 100.7, 100.7]):
            recorder.run()
        self.assertEqual(progress.call_count, 2)
        self.assertTrue(all(call.args[2] == ('Key released', {}) for call in progress.call_args_list))
        self.assertEqual(done.call_args.args[0], ['wait:100', 'down:A', 'wait:100', 'up:A'])


class TimelineTests(unittest.TestCase):
    def test_holds_timing_repeat_filter_and_reserved_shortcuts(self):
        timeline = Timeline(10)
        for code in (67, 68, 88):
            timeline.key(code, 1, 10.01)
            timeline.key(code, 0, 10.02)
        timeline.key(17, 1, 10.1)  # W
        timeline.key(17, 2, 10.2)  # Kernel autorepeat is not another press.
        timeline.key(17, 0, 10.5)
        self.assertEqual(timeline.finish(11), ['wait:100', 'down:W', 'wait:400', 'up:W'])

    def test_escape_and_editor_shortcuts_are_recorded_as_game_input(self):
        timeline = Timeline(0)
        for code in (1, 29, 31):  # Escape, Ctrl, S are game input while hidden.
            timeline.key(code, 1, .1)
            timeline.key(code, 0, .2)
        self.assertIn('down:ESC', timeline.finish(.3))
        self.assertIn('down:S', timeline.steps)

    def test_button_drag_scroll_and_unreleased_inputs_are_balanced(self):
        timeline = Timeline(0)
        timeline.key(272, 1, .1, point=(10, 20))
        timeline.move((40, 50), .2)
        timeline.key(272, 0, .3, point=(60, 70))
        timeline.wheel(0, -1, (60, 70), .4)
        timeline.key(29, 1, .5)
        steps = timeline.finish(.6)
        self.assertEqual(steps, ['wait:100', 'move:10,20', 'button:Left,1',
                                'wait:100', 'move:40,50', 'wait:100', 'move:60,70',
                                'button:Left,0', 'wait:100', 'wheel:0,-1',
                                'wait:100', 'down:LEFTCTRL', 'wait:100', 'up:LEFTCTRL'])
        for step in steps:
            parse_step(step)

    def test_same_key_on_two_devices_releases_only_after_both(self):
        timeline = Timeline(0)
        timeline.key(30, 1, 0, 'one')
        timeline.key(30, 1, .1, 'two')
        timeline.key(30, 0, .2, 'one')
        timeline.key(30, 0, .3, 'two')
        self.assertEqual(timeline.finish(.4), ['down:A', 'wait:300', 'up:A'])

    def test_unmatched_release_and_outside_click_do_not_capture(self):
        timeline = Timeline(0)
        timeline.key(30, 0, .1)
        timeline.key(272, 1, .2, point=None)
        timeline.key(272, 0, .3, point=(10, 10))
        self.assertEqual(timeline.finish(.4), [])

    def test_limit_reserves_room_for_releases(self):
        timeline = Timeline(0, capacity=10)
        with self.assertRaises(RecordingLimit):
            for index in range(20):
                timeline.key(30 + index, 1, index * .01)
        steps = timeline.finish(.5)
        self.assertLessEqual(len(steps), 10)
        self.assertEqual(sum(s.startswith('down:') for s in steps), sum(s.startswith('up:') for s in steps))


class RecorderTests(unittest.TestCase):
    def test_bluetooth_uhid_hardware_is_allowed_while_uinput_is_excluded(self):
        self.assertTrue(physical_input_path('/sys/devices/virtual/misc/uhid/0005:1234/input/input1/event1'))
        self.assertTrue(physical_input_path('/sys/devices/pci0000/usb1/input/input1/event1'))
        self.assertFalse(physical_input_path('/sys/devices/virtual/input/input1/event1'))

    def test_monotonic_device_timestamps_preserve_key_hold_duration(self):
        source, done = Mock(), Mock()
        recorder = Recorder(lambda: object(), lambda _: None, done, mouse=False, source=lambda *_: source)
        def stop():
            recorder.stop.set()
            return []
        source.read.side_effect = [[(100.1, 'keyboard', 1, 17, 1), (100.6, 'keyboard', 1, 17, 0)], stop]
        with patch('macro_recorder.time.monotonic', side_effect=[100, 100.6, 100.7, 100.7]):
            recorder.run()
        self.assertEqual(done.call_args.args[0], ['wait:100', 'down:W', 'wait:500', 'up:W'])

    def test_fast_drag_within_one_batch_keeps_start_and_end_positions(self):
        source, done = Mock(), Mock()
        point = Mock(side_effect=[(10, 100), (100, 100)])
        recorder = Recorder(lambda: object(), point, done, keyboard=False, source=lambda *_: source)
        def stop():
            recorder.stop.set()
            return []
        source.read.side_effect = [[(100.1, 'mouse', 1, 272, 1), (100.2, 'mouse', 2, 0, 90),
                                    (100.3, 'mouse', 1, 272, 0)], stop]
        with patch('macro_recorder.time.monotonic', side_effect=[100, 100.3, 100.4, 100.4]):
            recorder.run()
        self.assertEqual(done.call_args.args[0], ['wait:100', 'move:10,100', 'button:Left,1',
                                                'wait:200', 'move:100,100', 'button:Left,0'])

    def test_input_backend_filters_preheld_keys_and_unsupported_events(self):
        import libevdev
        from macro_recorder import InputDevices
        source = object.__new__(InputDevices)
        source.lib = libevdev
        fd, device = Mock(), Mock()
        source.readers = [(fd, device, 'keyboard', True, False)]
        source.ignored = {('keyboard', 30)}
        device.events.return_value = iter([
            libevdev.InputEvent(libevdev.EV_KEY.KEY_A, 0, 1, 100),
            libevdev.InputEvent(libevdev.EV_KEY.BTN_LEFT, 1, 1, 200),
            libevdev.InputEvent(libevdev.EV_KEY.KEY_A, 1, 1, 300),
            libevdev.InputEvent(libevdev.EV_SYN.SYN_REPORT, 0, 1, 400)])
        with patch('macro_recorder.select.select', return_value=([fd], [], [])):
            self.assertEqual(source.read(), [(1.0003, 'keyboard', 1, 30, 1)])
        source.close()
        fd.close.assert_called_once()

    def test_dropped_events_stop_instead_of_silently_desynchronizing(self):
        import libevdev
        from macro_recorder import InputDevices
        source = object.__new__(InputDevices)
        source.lib, source.ignored = libevdev, set()
        fd, device = Mock(), Mock()
        source.readers = [(fd, device, 'keyboard', True, False)]
        device.events.return_value = iter([libevdev.InputEvent(libevdev.EV_SYN.SYN_DROPPED, 0, 1, 0)])
        with patch('macro_recorder.select.select', return_value=([fd], [], [])), self.assertRaises(RuntimeError):
            source.read()

    def test_focus_loss_discards_untrusted_batch_and_closes_device(self):
        source = Mock()
        now = time.monotonic()
        source.read.side_effect = [[(now, 'one', 1, 30, 1)], [(now, 'one', 1, 48, 1)]]
        verify = Mock(side_effect=[object(), object(), RuntimeError('other app')])
        done = Mock()
        recorder = Recorder(verify, lambda _: None, done, source=lambda *_: source)
        recorder.run()
        source.close.assert_called_once()
        steps, reason = done.call_args.args
        self.assertIn('down:A', steps)
        self.assertIn('up:A', steps)
        self.assertNotIn('down:B', steps)
        self.assertIn('stopped', reason)

    def test_stop_discards_last_batch_and_discard_keeps_no_payload(self):
        source = Mock()
        done = Mock()
        recorder = Recorder(lambda: object(), lambda _: None, done, source=lambda *_: source)
        def read():
            recorder.stop.set()
            return [(time.monotonic(), 'one', 1, 30, 1)]
        source.read.side_effect = read
        recorder.discard = True
        recorder.run()
        self.assertEqual(done.call_args.args[0], [])
        source.close.assert_called_once()

    def test_guard_failure_does_not_open_devices(self):
        source, done = Mock(), Mock()
        recorder = Recorder(Mock(side_effect=RuntimeError('focus')), lambda _: None, done, source=source)
        recorder.run()
        source.assert_not_called()
        self.assertEqual(done.call_args.args[0], [])


class RecordingIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.library = MacroLibrary(Path(temp.name) / 'library.json')
        self.profile = login.Profile('Demo', 'Demo', '', '', 1920, 1080, [])
        with patch.object(login, 'load_profiles', return_value=({}, [self.profile])):
            self.picker = Picker(preview=True, library=self.library)
        self.picker.scan = lambda: None
        self.manager = Manager(self.picker, offscreen=True, width=360, height=420)
        self.manager.show()
        self.addCleanup(self.manager.window.destroy)
        self.addCleanup(self.picker.window.destroy)
        self.macro = new_macro('normal')
        self.macro.update(name='Recorded', game='Demo', title_re='Demo', steps=['key:Enter'])
        self.manager.edit(self.macro)
        self.window = login.Window('demo', 'Demo', 'GeForceNOW', 100, 200, 960, 540)
        self.game = (self.window, self.profile)
        self.picker.sessions.games = {'demo': self.game}

    def test_back_escape_and_close_cancel_armed_recording(self):
        from types import SimpleNamespace
        from gi.repository import Gdk
        for action in ('back', 'escape', 'close', 'library', 'history'):
            with self.subTest(action=action):
                self.manager.record_setup()
                self.manager.arm_recording()
                self.manager.show()
                if action == 'back': self.manager.back()
                elif action == 'escape':
                    self.manager.keyboard(self.manager.window, SimpleNamespace(keyval=Gdk.KEY_Escape, state=0))
                elif action == 'close': self.picker.close()
                elif action == 'library': self.manager.show_library()
                else: self.manager.show_history()
                self.assertIsNone(self.picker.pending_record)
                self.assertFalse(self.picker.playback['armed'])
                self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter'])

    def test_armed_reopen_returns_to_recording_controls(self):
        self.manager.record_setup()
        self.manager.arm_recording()
        with patch.object(login, 'active_window', return_value=self.window), patch.object(self.manager, 'show') as show:
            self.picker.open(auto=False)
            show.assert_called_once_with(refresh=False)
        self.assertIs(self.picker.pending_record, self.manager)
        self.assertEqual(self.manager.action_stack.get_visible_child_name(), 'record')

    def test_interrupted_recording_does_not_open_over_another_app(self):
        self.manager.arm_recording()
        self.picker.pending_record = None
        self.picker.record_manager = self.manager
        self.picker.recorder = Mock()
        self.picker.recorder.stop.is_set.return_value = False
        self.picker.busy = True
        with patch.object(self.manager, 'show') as show:
            self.picker.recorded(['down:ESC', 'up:ESC'], 'Recording stopped: game focus changed or input device unavailable.')
            show.assert_not_called()
        self.assertFalse(self.manager.window.get_visible())
        with patch.object(login, 'active_window', return_value=self.window), patch.object(self.manager, 'show') as show, patch.object(self.picker, 'run_login') as auto:
            self.picker.open(auto=True)
            show.assert_called_once_with(refresh=False)
            auto.assert_not_called()

    def test_recording_cannot_arm_during_an_inflight_vpn_change(self):
        from tool_status import vpn_lock
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', XDG_RUNTIME_DIR=directory):
            self.picker.preview = False
            try:
                with vpn_lock(), self.assertRaisesRegex(ValueError, 'Another VPN action'):
                    self.manager.arm_recording()
                self.assertIsNone(self.picker.pending_record)
                self.assertFalse(self.picker.playback['armed'])
            finally:
                self.picker.preview = True

    def test_arm_is_explicit_f9_start_and_stop_bypass_auto_login(self):
        self.manager.record_setup()
        self.manager.arm_recording()
        self.assertIs(self.picker.pending_record, self.manager)
        with patch.object(login, 'active_window', return_value=self.window), \
             patch('macro_recorder.Recorder') as factory, patch.object(self.picker, 'run_login') as auto:
            self.picker.open(auto=True)
            factory.return_value.start.assert_called_once()
            self.assertTrue(self.picker.playback['recording'])
            self.picker.open(auto=True)
            factory.return_value.stop.set.assert_called_once()
            auto.assert_not_called()
        self.assertEqual(self.picker.sessions.attempted, set())

    def test_wrong_game_does_not_start_or_read_devices(self):
        self.manager.arm_recording()
        other = login.Window('other', 'Mail', 'mail', 0, 0, 800, 600)
        with patch.object(login, 'active_window', return_value=other), patch('macro_recorder.Recorder') as recorder:
            self.picker.open(auto=True)
            recorder.assert_not_called()
        self.assertIs(self.picker.pending_record, self.manager)

    def test_cancelling_armed_recording_does_not_change_the_draft(self):
        self.manager.arm_recording()
        self.picker.cancel_pending()
        self.assertIsNone(self.picker.pending_record)
        self.assertFalse(self.picker.playback['armed'])
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter'])

    def test_recorded_actions_replace_only_in_memory_and_save_round_trip(self):
        self.manager.arm_recording()
        steps = ['down:W', 'wait:100', 'up:W', 'move:20,30', 'button:Right,1', 'button:Right,0', 'wheel:0,-1']
        self.manager.accept_recording(steps, 'Stopped.')
        self.assertEqual(self.library.macros, [])
        self.assertEqual([row[0] for row in self.manager.steps], steps)
        self.assertEqual(self.manager.fields['step_delay'].get_value_as_int(), 0)
        for index, step in enumerate(steps):
            self.manager.steps_view.get_selection().select_path(index)
            self.assertEqual(self.manager.inputs.value(), step)
        self.manager.save_draft()
        saved = self.library.macros[-1]
        validate(saved)
        export = self.library.path.with_name('export.json')
        self.library.export_file(export)
        other = MacroLibrary(self.library.path.with_name('other.json'))
        other.import_file(export)
        self.assertEqual(other.macros[0]['steps'], steps)
        self.assertEqual(self.library.duplicate(saved['id'])['steps'], steps)

    def test_append_and_no_input_preserve_existing_actions(self):
        self.manager.record_mode.set_active_id('append')
        self.manager.arm_recording()
        self.manager.accept_recording([], 'Stopped.')
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter'])
        self.manager.accept_recording(['down:A', 'up:A'], 'Stopped.')
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter', 'wait:15', 'down:A', 'up:A'])

    def test_append_preserves_existing_action_delay_and_undo(self):
        self.manager.fields['step_delay'].set_value(42)
        self.manager.record_mode.set_active_id('append')
        self.manager.arm_recording()
        self.assertEqual(self.manager.record_request['capacity'], 998)
        self.manager.accept_recording(['down:A', 'wait:70', 'up:A'], 'Stopped.')
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter', 'wait:42', 'down:A', 'wait:70', 'up:A'])
        self.assertEqual(self.manager.fields['step_delay'].get_value_as_int(), 0)
        self.manager.discard_recording()
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter'])
        self.assertEqual(self.manager.fields['step_delay'].get_value_as_int(), 42)

    def test_switching_to_game_settings_cancels_armed_snapshot(self):
        self.manager.record_setup()
        self.manager.arm_recording()
        self.manager.editor.get_children()[0].set_current_page(0)
        self.assertIsNone(self.picker.pending_record)
        self.assertEqual(self.manager.action_stack.get_visible_child_name(), 'edit')

    def test_armed_controls_can_cancel_without_hiding_or_discarding_draft(self):
        self.manager.record_setup()
        self.manager.arm_recording()
        self.assertFalse(self.manager.record_mouse.get_sensitive())
        self.assertEqual(self.manager.record_arm_button.get_label(), 'Cancel recording')
        self.manager.arm_recording()
        self.assertIsNone(self.picker.pending_record)
        self.assertTrue(self.manager.record_mouse.get_sensitive())
        self.assertEqual(self.manager.record_arm_button.get_label(), 'Arm recording')

    def test_append_preserves_login_cutoff_after_delay_conversion(self):
        self.manager.kind.set_active_id('login')
        self.manager.fields['login_limit'].set_value(1)
        self.manager.record_mode.set_active_id('append')
        self.manager.arm_recording()
        self.manager.accept_recording(['down:A', 'up:A'], 'Recording stopped.')
        self.assertEqual(self.manager.fields['login_limit'].get_value_as_int(), 2)
        self.assertEqual([row[0] for row in self.manager.steps][:2], ['key:Enter', 'wait:15'])

    def test_recording_notice_translates_live_and_does_not_override_later_notices(self):
        self.manager.arm_recording()
        self.manager.accept_recording(['down:A', 'up:A'], 'Recording stopped.')
        self.picker.localizer.set_language('ko')
        self.assertIn('동작 2개', self.manager.notice.get_text())
        self.manager.say('Saved automatically')
        self.picker.localizer.set_language('en')
        self.assertEqual(self.manager.notice.get_text(), 'Saved automatically')

    def test_discard_take_restores_previous_unsaved_actions_and_timing(self):
        self.manager.fields['step_delay'].set_value(42)
        self.manager.arm_recording()
        self.manager.accept_recording(['down:A', 'up:A'], 'Stopped.')
        self.assertTrue(self.manager.discard_record_button.get_visible())
        self.manager.discard_recording()
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Enter'])
        self.assertEqual(self.manager.fields['step_delay'].get_value_as_int(), 42)
        self.assertFalse(self.manager.discard_record_button.get_visible())
        self.assertEqual(self.library.macros, [])

    def test_f9_dismisses_review_when_layer_focus_hides_active_game_metadata(self):
        self.picker.render()
        with patch.object(login, 'active_window', side_effect=login.LoginError('Layer owns focus')), \
             patch.object(self.picker, 'run_login') as auto:
            self.picker.open(auto=True)
        self.assertFalse(self.picker.window.get_visible())
        auto.assert_not_called()

    def test_late_f9_after_recording_never_runs_auto_login(self):
        self.picker.record_stopped_at = time.monotonic()
        with patch.object(self.picker, 'run_login') as auto, patch.object(login, 'active_window') as active:
            self.picker.open(auto=True)
            auto.assert_not_called()
            active.assert_not_called()

    def test_record_setup_fits_small_size_with_controls_visible(self):
        self.manager.record_setup()
        loop = self.picker.GLib.MainLoop()
        self.picker.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
        loop.run()
        self.assertEqual(tuple(self.manager.window.get_size()), (360, 420))
        page = self.manager.action_stack.get_visible_child()
        scroll, controls = page.get_children()
        y = controls.translate_coordinates(self.manager.window, 0, 0)[1]
        self.assertLessEqual(y + controls.get_allocated_height(), 420)
        self.assertFalse(self.manager.save_button.get_visible())
        sy = scroll.translate_coordinates(self.manager.window, 0, 0)[1]
        self.assertLessEqual(sy + scroll.get_allocated_height(), y)
        with self.assertRaises(ValueError):
            self.manager.save_draft()

    def test_unsaved_custom_browser_game_uses_temporary_detection_profile(self):
        scan = Picker.scan.__get__(self.picker, Picker)
        custom = login.Window('chrome', 'Unlisted Game', 'chrome', 0, 0, 1000, 800)
        self.manager.fields['title_re'].set_text('Unlisted Game')
        self.manager.arm_recording()
        with patch.object(login, 'load_profiles', return_value=({}, [])), patch.object(login, 'all_windows', return_value=[custom]):
            scan()
        self.assertIn('chrome', self.picker.sessions.games)
        self.assertEqual(self.library.macros, [])

    def test_recorded_action_forms_support_edits_without_raw_syntax(self):
        inputs = self.manager.inputs
        for step in ('down:W', 'up:W', 'button:Right,1', 'button:Right,0', 'wheel:0,-2', 'move:30,40'):
            inputs.load(step)
            self.assertEqual(inputs.value(), step)
        inputs.load('down:W')
        inputs.key.set_active_id('S')
        self.assertEqual(inputs.value(), 'down:S')
        self.assertFalse(inputs.modifier_box.get_visible())
        inputs.load('button:Right,1')
        inputs.button_state.set_active_id('0')
        self.assertEqual(inputs.value(), 'button:Right,0')
        inputs.load('wheel:0,-2')
        inputs.wheel_y.set_value(3)
        self.assertEqual(inputs.value(), 'wheel:0,3')

    def test_point_capture_is_reference_scaled_without_moving_pointer(self):
        request = dict(width=1920, height=1080)
        with patch.object(login, 'cursor_position', return_value=(580, 470)), patch.object(login, 'move_cursor') as move:
            self.assertEqual(self.manager.record_point(self.window, request), (960, 540))
            move.assert_not_called()

    def test_recorded_holds_release_on_cancel_and_focus_loss(self):
        macro = self.macro.copy()
        macro.update(steps=['down:W', 'button:Left,1', 'wait:5000'], step_delay=0)
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                runner = MacroRunner(self.library, lambda: self.window)
                def progress(step, *_):
                    if step == 2:
                        if cancelled:
                            runner.cancel.set()
                        else:
                            runner.verify = Mock(side_effect=RuntimeError('focus lost'))
                runner.progress = progress
                with patch.object(login, 'run') as run:
                    with self.assertRaises((Cancelled, RuntimeError)):
                        runner.run(macro, [], {})
                commands = [call.args[0] for call in run.call_args_list]
                self.assertIn(['ydotool', 'key', '--key-delay', '0', '17:0'], commands)
                self.assertIn(['ydotool', 'click', '0x80'], commands)
                self.assertFalse(runner.held_keys or runner.held_buttons)

    def test_recorded_mouse_commands_and_completion_cleanup(self):
        macro = self.macro.copy()
        macro.update(steps=['move:20,30', 'button:Right,1', 'wheel:1,-2', 'down:A'], step_delay=0)
        runner = MacroRunner(self.library, lambda: self.window)
        with patch.object(login, 'run') as run, patch.object(login, 'move_cursor') as move:
            runner.run(macro, [], {})
            move.assert_called_once_with(110, 215)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['ydotool', 'mousemove', '--wheel', '--', '1', '-2'], commands)
        self.assertIn(['ydotool', 'click', '0x81'], commands)
        self.assertIn(['ydotool', 'key', '--key-delay', '0', '30:0'], commands)

    def test_cleanup_attempts_all_inputs_even_if_one_release_fails(self):
        runner = MacroRunner(self.library, lambda: self.window)
        runner.held_keys.add(17)
        runner.held_buttons.update((0, 1))
        with patch.object(login, 'run', side_effect=[OSError('unavailable'), None, None]) as run:
            with self.assertRaises(OSError):
                runner.release_inputs()
        self.assertEqual(run.call_count, 3)
        self.assertFalse(runner.held_keys or runner.held_buttons)


if __name__ == '__main__':
    unittest.main()
