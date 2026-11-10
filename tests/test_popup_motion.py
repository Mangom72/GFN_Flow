"""Placement and native motion checks, without moving focus or pointer."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_macro import Picker, request
from ui_motion import Motion, PageFade
from ui_popup import place_popup, monitor_size


class PlacementTests(unittest.TestCase):
    def monitor(self, **updates):
        value = dict(name='Display', width=1920, height=1080, x=0, y=0, reserved=[0, 40, 0, 0])
        value.update(updates)
        return value

    def anchor(self, **updates):
        value = dict(screen='Display', edge='top', x=1489, y=0, width=32, height=40)
        value.update(updates)
        return value

    def test_centers_below_widget_with_bar_counted_once(self):
        rect = place_popup(self.monitor(), 430, 520, self.anchor())
        self.assertEqual((rect.x, rect.y, rect.width, rect.height), (1290, 48, 430, 520))

    def test_compact_and_library_keep_same_anchor(self):
        compact = place_popup(self.monitor(), 430, 520, self.anchor())
        library = place_popup(self.monitor(), 430, 600, self.anchor())
        self.assertEqual((compact.x, compact.y), (library.x, library.y))

    def test_clamps_icons_near_both_screen_edges(self):
        for x, expected in ((0, 12), (1888, 1478)):
            rect = place_popup(self.monitor(), 430, 520, self.anchor(x=x))
            self.assertEqual(rect.x, expected)
            self.assertLessEqual(rect.x + rect.width, 1908)

    def test_small_scaled_and_rotated_monitors(self):
        for monitor in (self.monitor(width=360, height=600),
                        self.monitor(width=2160, height=3840, scale=2, transform=1),
                        self.monitor(width=1280, height=720, scale=1.5)):
            with self.subTest(monitor=monitor):
                width, height = monitor_size(monitor)
                rect = place_popup(monitor, 430, 600)
                self.assertGreaterEqual(rect.x, 12)
                self.assertGreaterEqual(rect.y, 48)
                self.assertLessEqual(rect.x + rect.width, width - 12)
                self.assertLessEqual(rect.y + rect.height, height - 12)
        self.assertEqual(monitor_size(self.monitor(width=2160, height=3840, scale=2, transform=1)), (1920, 1080))

    def test_bottom_and_vertical_bars_do_not_cover_the_icon(self):
        for anchor, reserved in ((self.anchor(edge='bottom', y=1040), [0, 0, 0, 40]),
                                 (self.anchor(edge='left', x=0, y=500, width=40, height=32), [40, 0, 0, 0]),
                                 (self.anchor(edge='right', x=1880, y=500, width=40, height=32), [0, 0, 40, 0])):
            with self.subTest(anchor=anchor):
                rect = place_popup(self.monitor(reserved=reserved), 430, 520, anchor)
                if anchor['edge'] == 'bottom':
                    self.assertLessEqual(rect.y + rect.height, anchor['y'] - 8)
                elif anchor['edge'] == 'left':
                    self.assertGreaterEqual(rect.x, anchor['x'] + anchor['width'] + 8)
                else:
                    self.assertLessEqual(rect.x + rect.width, anchor['x'] - 8)

    def test_bad_or_other_monitor_anchor_falls_back_safely(self):
        expected = place_popup(self.monitor(), 430, 520)
        for anchor in (None, [], self.anchor(screen='Disconnected'), self.anchor(x=-1),
                       self.anchor(x=float('nan')), self.anchor(width=10000), self.anchor(edge='broken')):
            with self.subTest(anchor=anchor):
                self.assertEqual(place_popup(self.monitor(), 430, 520, anchor), expected)

    def test_bar_request_keeps_monitor_and_legacy_clients_unchanged(self):
        with patch('auto_macro.send') as send:
            request('show', 'Display')
            self.assertEqual(json.loads(send.call_args.args[0]), dict(action='show', screen='Display'))
            request('f9')
            send.assert_called_with('f9')


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.picker = Picker(preview=True)
        self.addCleanup(self.picker.window.destroy)
        self.Gtk = self.picker.Gtk

    def test_previews_remain_static_and_live_stacks_use_native_transitions(self):
        stack = self.Gtk.Stack()
        self.picker.motion.stack(stack, slide=True)
        self.assertEqual(stack.get_transition_duration(), 0)
        Motion(self.Gtk).stack(stack, slide=True)
        self.assertEqual(stack.get_transition_type(), self.Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.assertEqual(stack.get_transition_duration(), 160)

    def test_widget_monitor_selection_and_f9_focused_monitor_fallback(self):
        monitors = [dict(name='First', x=0, y=0, width=1920, height=1080, reserved=[0,40,0,0], focused=True),
                    dict(name='Second', x=1920, y=0, width=1920, height=1080, reserved=[0,40,0,0])]
        first, second = [SimpleNamespace(get_geometry=lambda x=x: SimpleNamespace(x=x, y=0)) for x in (0,1920)]
        display = SimpleNamespace(get_n_monitors=lambda: 2, get_monitor=lambda index: (first, second)[index])
        layer = Mock(Edge=SimpleNamespace(LEFT='left', TOP='top'))
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'anchor-Second.json').write_text(json.dumps(dict(screen='Second', x=1000, y=0, width=32, height=40, edge='top')))
            self.picker.preview = False
            try:
                with patch('auto_macro.STATE', Path(directory)), patch.object(self.picker, 'Layer', layer), \
                     patch.object(self.picker.window, 'get_display', return_value=display), \
                     patch('auto_macro.subprocess.check_output', return_value=json.dumps(monitors).encode()):
                    self.picker.popup_screen = 'Second'
                    self.picker.resize_view(520)
                    layer.set_monitor.assert_called_with(self.picker.window, second)
                    self.assertEqual((self.picker.popup_geometry.x, self.picker.popup_geometry.y), (801, 48))
                    self.picker.popup_screen = None
                    self.picker.resize_view(520)
                    layer.set_monitor.assert_called_with(self.picker.window, first)
                    self.assertEqual(self.picker.popup_geometry.x, 1478)
            finally:
                self.picker.preview = True

    def test_socket_accepts_monitor_context_and_preserves_plain_f9(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        self.picker.server = Mock()
        self.picker.server.accept.return_value = (connection, None)
        connection.recv.return_value = b'{"action":"manage","screen":"Second"}'
        with patch.object(self.picker, 'manage') as manage:
            self.assertTrue(self.picker.receive())
            self.assertEqual(self.picker.popup_screen, 'Second')
            manage.assert_called_once()
        connection.recv.return_value = b'f9'
        with patch.object(self.picker, 'open') as open_picker:
            self.assertTrue(self.picker.receive())
            self.assertIsNone(self.picker.popup_screen)
            open_picker.assert_called_once_with(auto=True)

    def test_fade_is_smooth_completes_and_does_not_hide_widgets(self):
        widget = self.Gtk.Label(label='Editor')
        widget.show()
        motion = Mock(allowed=lambda: True)
        fade = PageFade(motion, 160)
        fade.start(widget)
        self.assertAlmostEqual(widget.get_opacity(), .4, delta=.01)
        clock = Mock()
        clock.get_frame_time.return_value = 1000000
        self.assertTrue(fade.advance(widget, clock))
        clock.get_frame_time.return_value = 1080000
        self.assertTrue(fade.advance(widget, clock))
        self.assertGreater(widget.get_opacity(), .8)
        clock.get_frame_time.return_value = 1160000
        self.assertFalse(fade.advance(widget, clock))
        self.assertEqual(widget.get_opacity(), 1)
        self.assertTrue(widget.get_visible())
        widget.destroy()

    def test_rapid_navigation_cancels_old_fade_and_reduced_motion_is_immediate(self):
        motion = Mock(allowed=Mock(return_value=True))
        fade = PageFade(motion, 160)
        first, second = self.Gtk.Label(), self.Gtk.Label()
        fade.start(first)
        fade.start(second)
        self.assertEqual(first.get_opacity(), 1)
        motion.allowed.return_value = False
        fade.start(first)
        self.assertEqual(second.get_opacity(), 1)
        self.assertEqual(first.get_opacity(), 1)
        self.assertIsNone(fade.tick)
        first.destroy(); second.destroy()

    def test_gtk_reduced_motion_setting_is_honored(self):
        motion = Motion(self.Gtk)
        settings = self.Gtk.Settings.get_default()
        original = settings.get_property('gtk-enable-animations')
        try:
            settings.set_property('gtk-enable-animations', False)
            self.assertFalse(motion.allowed())
        finally:
            settings.set_property('gtk-enable-animations', original)

    def test_real_notebook_navigation_finishes_with_full_opacity(self):
        from macro_manager import Manager
        settings = self.Gtk.Settings.get_default()
        original = settings.get_property('gtk-enable-animations')
        settings.set_property('gtk-enable-animations', True)
        self.picker.motion.enabled = True
        manager = Manager(self.picker, offscreen=True, width=360, height=420)
        def settle(milliseconds):
            loop = self.picker.GLib.MainLoop()
            self.picker.GLib.timeout_add(milliseconds, lambda: (loop.quit(), False)[1])
            loop.run()
        try:
            manager.show()
            settle(80)
            manager.tabs.set_current_page(1)
            page = manager.tabs.get_nth_page(1)
            self.assertLess(page.get_opacity(), 1)
            settle(250)
            self.assertEqual(page.get_opacity(), 1)
            manager.tabs.set_current_page(0)
            manager.tabs.set_current_page(1)
            self.assertLess(page.get_opacity(), 1)
            self.picker.motion.set_enabled(False)
            self.assertEqual(page.get_opacity(), 1)
            self.assertTrue(manager.tabs.get_tab_label(page).title_reveal.get_child_revealed())
            self.assertFalse(manager.tabs.get_tab_label(manager.tabs.get_nth_page(0)).title.get_child_visible())
            manager.tabs.set_current_page(0)
            self.assertEqual(manager.tabs.get_nth_page(0).get_opacity(), 1)
        finally:
            manager.window.destroy()
            settings.set_property('gtk-enable-animations', original)


if __name__ == '__main__':
    unittest.main()
