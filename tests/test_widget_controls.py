"""Synthetic controller devices and offscreen GTK; no real input reads/injection."""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from widget_controls import Controller, GamepadInput, GUIDE
from auto_macro import Picker


class Code:
    def __init__(self, value): self.value = value


def event(kind, code, value):
    return NS(type=Code(kind), code=Code(code), value=value)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.reader = Controller.__new__(Controller)
        self.reader.lib = NS(EV_KEY='keys', EV_SYN=NS(SYN_DROPPED=object()))
        self.reader.device = Mock()
        self.reader.device.evbits = {'keys': []}
        self.reader.device.absinfo = {}
        self.reader.axes = {}
        self.reader.rest_axes = []
        self.reader.grabbed = False

    def read(self, events, visible=True, focused=True):
        self.reader.device.events.return_value = events
        return self.reader.read(visible, focused)

    def test_held_unused_stick_or_trigger_also_prevents_grab(self):
        right_stick = Code(3)
        trigger = Code(5)
        self.reader.rest_axes = [(right_stick, 0, 10), (trigger, -32768, 1000)]
        self.reader.device.value = {right_stick: 500, trigger: -32768}
        self.assertFalse(self.reader.neutral())
        self.reader.device.value[right_stick] = 0
        self.assertTrue(self.reader.neutral())
        self.reader.device.value[trigger] = 1000
        self.assertFalse(self.reader.neutral())

    def test_hidden_controller_only_guide_opens(self):
        self.assertEqual(self.read([event(1,304,1),event(1,GUIDE,1)], False), ['toggle'])
        self.reader.device.grab.assert_not_called()

    def test_grab_waits_for_release_and_discards_pre_grab_input(self):
        with patch.object(self.reader, 'neutral', return_value=False):
            self.assertEqual(self.read([event(1,304,1)]), [])
            self.assertFalse(self.reader.grabbed)
        self.assertEqual(self.read([event(1,304,0)]), [])
        self.reader.device.grab.assert_called_once()
        self.assertEqual(self.read([event(1,304,1)]), ['Return'])

    def test_focus_loss_stops_actions_and_releases_when_neutral(self):
        self.reader.grabbed = True
        self.assertEqual(self.read([event(1,304,1)], focused=False), [])
        self.reader.device.ungrab.assert_called_once()
        self.assertFalse(self.reader.grabbed)

    def test_hide_holds_grab_until_button_release(self):
        self.reader.grabbed = True
        with patch.object(self.reader, 'neutral', return_value=False):
            self.assertEqual(self.read([event(1,305,1)], False), [])
            self.reader.device.ungrab.assert_not_called()
        self.assertEqual(self.read([event(1,305,0)], False), [])
        self.reader.device.ungrab.assert_called_once()

    def test_axes_deadzone_and_one_action_per_direction(self):
        class AbsInfo:
            def __getitem__(self, code): return NS(minimum=-32768, maximum=32767)
        self.reader.device.absinfo = AbsInfo()
        self.reader.grabbed = True
        self.assertEqual(self.read([event(3,1,100)]), [])
        self.assertEqual(self.read([event(3,1,-32000)]), ['Up'])
        self.assertEqual(self.read([event(3,1,-31000)]), [])
        self.assertEqual(self.read([event(3,1,31000)]), ['Down'])

    def test_held_stick_prevents_grab_until_centered(self):
        class AbsInfo:
            def __getitem__(self, code): return NS(minimum=-1, maximum=1)
        self.reader.device.absinfo = AbsInfo()
        self.assertEqual(self.read([event(3,17,-1)]), [])
        self.reader.device.grab.assert_not_called()
        self.assertEqual(self.read([event(3,17,0)]), [])
        self.reader.device.grab.assert_called_once()

    def test_dpad_hat_uses_both_extremes(self):
        class AbsInfo:
            def __getitem__(self, code): return NS(minimum=-1, maximum=1)
        self.reader.device.absinfo = AbsInfo()
        self.reader.grabbed = True
        self.assertEqual(self.read([event(3,17,-1)]), ['Up'])
        self.assertEqual(self.read([event(3,17,0)]), [])
        self.assertEqual(self.read([event(3,17,1)]), ['Down'])

    def test_gamepad_actions_require_visible_focused_popup(self):
        picker = Mock()
        picker.window.get_visible.return_value = False
        picker.window.has_toplevel_focus.return_value = False
        picker.Gtk.grab_get_current.return_value = None
        controls = GamepadInput(picker)
        reader = Mock()
        reader.read.return_value = ['Return', 'toggle', 'Down']
        path = Path('/fake/controller')
        controls.controllers = {path: reader}
        with patch('widget_controls.joystick_paths', return_value=[path]), patch.object(controls, 'dispatch') as dispatch:
            controls.poll()
            picker.toggle.assert_called_once_with()
            dispatch.assert_not_called()
        controls.close()
        reader.close.assert_called_once()

    def test_dispatch_error_releases_controller(self):
        picker = Mock()
        picker.window.get_visible.return_value = True
        picker.window.has_toplevel_focus.return_value = True
        controls = GamepadInput(picker)
        reader = Mock()
        reader.read.return_value = ['Return']
        path = Path('/fake/controller')
        controls.controllers = {path: reader}
        with patch('widget_controls.joystick_paths', return_value=[path]), patch.object(controls, 'dispatch', side_effect=RuntimeError('destroyed widget')):
            controls.poll()
        reader.close.assert_called_once()
        self.assertFalse(controls.controllers)

    def test_failed_grab_cannot_control_popup(self):
        self.reader.device.grab.side_effect = PermissionError()
        with self.assertRaises(PermissionError): self.read([event(1,304,1)])
        self.assertFalse(self.reader.grabbed)


class GtkControllerTests(unittest.TestCase):
    def test_local_gtk_dispatch_selects_and_escape_hides(self):
        picker = Picker(preview=True)
        try:
            picker.render()
            from gi.repository import Gdk
            focus_event = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
            focus_event.window = picker.window.get_window()
            focus_event.in_ = True
            focus_event.set_device(picker.window.get_display().get_default_seat().get_keyboard())
            picker.Gtk.main_do_event(focus_event)  # Focus only this offscreen fixture.
            controls = GamepadInput(picker)
            with patch.object(picker, 'run_macro') as run:
                controls.dispatch('Down')
                self.assertIsNotNone(picker.quick_selected)
                run.assert_not_called()
                # Native button activation stays local and fires only once.
                button = picker.Gtk.Button(label='Synthetic action')
                clicked = Mock()
                button.connect('clicked', clicked)
                picker.panel.pack_start(button, False, False, 0)
                button.show()
                button.grab_focus()
                controls.dispatch('Return')
                # GTK's keyboard activation is scheduled by the button.
                import time
                deadline = time.monotonic() + .3
                while time.monotonic() < deadline:
                    while picker.Gtk.events_pending(): picker.Gtk.main_iteration_do(False)
                    time.sleep(.01)
                clicked.assert_called_once()
                run.assert_not_called()
                controls.dispatch('Escape')
                self.assertFalse(picker.window.get_visible())
        finally:
            picker.window.destroy()
            picker.preview_directory.cleanup()

    def test_native_library_cursor_and_tab_navigation(self):
        picker = Picker(preview=True)
        from macro_manager import Manager
        from gi.repository import Gdk
        manager = Manager(picker, offscreen=True, width=360, height=480)
        original = picker.window
        try:
            manager.show()
            picker.window = manager.window
            tree = manager.tree
            tree.set_cursor('0')
            tree.grab_focus()
            focus = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
            focus.window = manager.window.get_window()
            focus.in_ = True
            focus.set_device(manager.window.get_display().get_default_seat().get_keyboard())
            picker.Gtk.main_do_event(focus)
            controls = GamepadInput(picker)
            controls.dispatch('Down')
            self.assertEqual(tree.get_cursor()[0].to_string(), '0:0')
            controls.dispatch('Tab')
            self.assertIsNot(manager.window.get_focus(), tree)
            controls.dispatch('ISO_Left_Tab')
            self.assertIs(manager.window.get_focus(), tree)
        finally:
            manager.window.destroy()
            original.destroy()
            picker.preview_directory.cleanup()


if __name__ == '__main__': unittest.main()
