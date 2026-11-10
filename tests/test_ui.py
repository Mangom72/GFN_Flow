"""Interaction, layout and accessibility checks against actual GTK widgets."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro
from macro_manager import Manager
from ui_theme import TOKENS


class UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secrets = {}
        for name, side_effect in [('store_secret', self.secrets.__setitem__), ('get_secret', self.secrets.__getitem__)]:
            mock = patch.object(login, name, side_effect=side_effect)
            mock.start()
            self.addCleanup(mock.stop)
        self.profile = login.Profile('Demo', 'Demo', 'test-account', 'demo-key', 1920, 1080, ['id', 'tab', 'pw', 'enter'])
        library = MacroLibrary(Path(self.tmp.name) / 'library.json', [self.profile])
        self.picker = Picker(preview=True, library=library)
        self.picker.legacy_profiles = [self.profile]
        self.picker.scan = lambda: None
        self.manager = Manager(self.picker, offscreen=True, width=360, height=480)
        self.manager.show()

    def tearDown(self):
        self.manager.window.destroy()
        self.picker.window.destroy()
        self.tmp.cleanup()

    def pump(self):
        while self.picker.Gtk.events_pending():
            self.picker.Gtk.main_iteration_do(False)

    def test_actions_follow_selection_and_trash_context(self):
        self.assertFalse(self.manager.context.get_visible())
        self.manager.tree.get_selection().select_path('0:0')
        self.assertTrue(self.manager.context.get_visible())
        self.assertTrue(self.manager.context_buttons['delete'].get_visible())
        self.assertFalse(self.manager.context_buttons['restore'].get_visible())
        self.manager.act('delete')
        self.assertFalse(self.manager.context.get_visible())
        self.manager.filter.set_active(2)
        self.manager.tree.get_selection().select_path('0:0')
        self.assertTrue(self.manager.context_buttons['restore'].get_visible())
        self.assertFalse(self.manager.context_buttons['delete'].get_visible())
        self.assertFalse(self.manager.context_buttons['queue'].get_visible())

    def test_row_tags_are_only_game_and_action_count_in_both_interfaces(self):
        macro = self.manager.library.macros[0]
        self.picker.render()
        label = self.picker.quick_rows.get_row_at_index(0).get_child().get_children()[0]
        self.assertEqual(label.get_text(), 'Demo\n4 actions')
        self.assertNotIn('login', label.get_text())
        self.assertNotIn('F9', label.get_text())
        self.assertEqual(label.get_tooltip_text(), macro['name'])
        column = self.manager.tree.get_column(0)
        column.cell_set_cell_data(self.manager.rows, self.manager.rows.get_iter('0:0'), False, False)
        self.assertEqual(column.get_cells()[1].get_property('text'), label.get_text())
        self.assertEqual(macro['name'], 'Demo login')
        self.assertTrue(macro['auto'])
        self.assertEqual(macro['kind'], 'login')

    def test_disabled_tag_still_exposes_state_in_tooltip(self):
        from unittest.mock import Mock
        self.manager.library.macros[0]['enabled'] = False
        self.manager.refresh()
        model = self.manager.rows
        path = self.picker.Gtk.TreePath.new_from_string('0:0')
        tree = Mock()
        tree.get_tooltip_context.return_value = (True, 0, 0, model, path, model.get_iter(path))
        tooltip = Mock()
        self.assertTrue(self.manager.macro_tooltip(tree, 0, 0, False, tooltip))
        self.assertEqual(tooltip.set_text.call_args.args[0], 'Demo login\nDisabled')

    def test_search_empty_state_and_filter(self):
        self.manager.search.set_text('does not exist')
        self.manager.search.emit('search-changed')
        self.assertEqual(self.manager.shown_count, 0)
        self.assertTrue(all(group[3] == '0' and next(group.iterchildren())[2] == 'empty'
                            for group in self.manager.rows))
        self.assertFalse(self.manager.context.get_visible())
        self.manager.search.set_text('')
        self.manager.search.emit('search-changed')
        self.assertEqual(self.manager.shown_count, 1)

    def test_keyboard_shortcuts_and_escape_stay_inline(self):
        from gi.repository import Gdk
        event = lambda key, ctrl=False: SimpleNamespace(keyval=Gdk.keyval_from_name(key), state=Gdk.ModifierType.CONTROL_MASK if ctrl else 0)
        self.assertTrue(self.manager.keyboard(self.manager.window, event('n', True)))
        self.assertEqual(self.manager.views.get_visible_child_name(), "editor")
        self.assertTrue(self.manager.keyboard(self.manager.window, event('Escape')))
        self.assertEqual(self.manager.views.get_visible_child_name(), "browse")
        self.assertEqual(self.manager.tabs.get_current_page(), 0)
        self.assertTrue(self.manager.window.get_visible())
        self.assertTrue(self.manager.keyboard(self.manager.window, event('f', True)))
        self.assertIs(self.manager.window.get_focus(), self.manager.search)

    def test_small_window_and_long_names_do_not_expand_controls(self):
        macro = self.manager.library.macros[0]
        macro['game'] = 'A long game name with <markup> & characters ' * 2
        self.manager.refresh()
        self.manager.tree.get_selection().select_path('0:0')
        self.pump()
        self.assertEqual(self.manager.window.get_pixbuf().get_width(), 360)
        self.assertLessEqual(self.manager.context.get_allocated_width(), 328)
        self.assertLessEqual(self.manager.tree.get_allocated_width(), 328)
        self.assertTrue(self.manager.context_buttons['delete'].get_style_context().has_class('danger'))
        self.assertEqual(self.manager.context_buttons['delete'].get_accessible().get_name(), 'Delete')

    def test_editor_small_window_and_structured_action_editing(self):
        self.manager.edit(self.manager.library.macros[0])
        self.pump()
        self.assertEqual(self.manager.window.get_pixbuf().get_width(), 360)
        self.assertLessEqual(self.manager.steps_view.get_allocated_width(), 328)
        self.assertEqual(list(self.manager.steps[0]), ['id'])
        self.assertEqual(self.manager.fields['user'].get_accessible().get_name(), 'Account / email')

    def test_theme_text_contrast(self):
        def luminance(hex_color):
            values = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            values = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in values]
            return sum(c * weight for c, weight in zip(values, (.2126, .7152, .0722)))
        for foreground in ('text', 'muted'):
            for background in ('background', 'surface', 'selected'):
                a, b = sorted([luminance(TOKENS[foreground]), luminance(TOKENS[background])])
                self.assertGreaterEqual((b + .05) / (a + .05), 4.5, (foreground, background))

    def test_manage_is_left_of_close_with_and_without_a_queued_action(self):
        for pending in (None, 'queued'):
            with self.subTest(pending=pending):
                self.picker.pending = pending
                self.picker.render()
                loop = self.picker.GLib.MainLoop()
                self.picker.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
                loop.run()
                header = self.picker.panel.get_children()[0]
                buttons = {child.get_accessible().get_name(): child for child in header.get_children()
                           if isinstance(child, self.picker.Gtk.Button)}
                manage, close = buttons['Manage macros'], buttons['Close']
                mx = manage.translate_coordinates(self.picker.window, 0, 0)[0]
                cx = close.translate_coordinates(self.picker.window, 0, 0)[0]
                self.assertLessEqual(mx + manage.get_allocated_width(), cx)

    def test_game_picker_selection_never_starts_playback(self):
        self.picker.game = (login.Window('a', 'Demo', 'GeForceNOW', 0, 0, 1920, 1080), self.profile)
        self.picker.render()
        self.assertFalse(self.picker.quick_rows.get_activate_on_single_click())
        with patch.object(self.picker, 'run_macro') as run:
            self.picker.quick_rows.select_row(self.picker.quick_rows.get_row_at_index(0))
            run.assert_not_called()

    def test_ctrl_s_saves_the_selected_structured_action(self):
        from gi.repository import Gdk
        self.manager.edit(self.manager.library.macros[0])
        self.pump()
        self.manager.steps_view.get_selection().select_path(0)
        self.manager.inputs.action.set_active_id('key')
        self.manager.inputs.key.set_active_id('ESC')
        event = SimpleNamespace(keyval=Gdk.keyval_from_name('s'), state=Gdk.ModifierType.CONTROL_MASK)
        self.manager.keyboard(self.manager.window, event)
        self.assertEqual(self.manager.library.macros[0]['steps'][0], 'key:ESC')

    def test_capture_refreshes_move_down_for_previously_last_step(self):
        self.manager.edit(self.manager.library.macros[0])
        self.manager.steps_view.get_selection().select_path(len(self.manager.steps) - 1)
        self.assertFalse(self.manager.step_buttons[1].get_sensitive())
        with patch.object(login, 'cursor_position', return_value=(100, 100)):
            self.assertTrue(self.manager.capture_from(login.Window('a', 'Demo', 'GeForceNOW', 0, 0, 1920, 1080)))
        self.assertTrue(self.manager.step_buttons[1].get_sensitive())

    def test_playback_releases_widget_focus_before_worker_starts(self):
        calls = []
        self.picker.preview = False
        layer = SimpleNamespace(KeyboardMode=SimpleNamespace(NONE='none'),
                                set_keyboard_mode=lambda *_: calls.append('release'))
        display = SimpleNamespace(sync=lambda: calls.append('sync'))
        with patch.object(self.picker, 'Layer', layer), \
             patch.object(self.picker.window, 'get_display', return_value=display), \
             patch('auto_macro.threading.Thread') as thread:
            thread.return_value.start.side_effect = lambda: calls.append('start')
            self.picker.launch(lambda: None, 'done')
        self.assertEqual(calls, ['release', 'sync', 'start'])
        self.assertFalse(self.picker.window.get_visible())
        self.picker.preview = True

    def test_empty_category_header_toggles_without_selecting_or_executing(self):
        self.pump()
        tree = self.manager.tree
        path = self.picker.Gtk.TreePath.new_from_string('1')
        self.assertTrue(tree.row_expanded(path))
        self.assertEqual(self.manager.rows['1:0'][2], 'empty')
        tree.get_selection().select_path('1:0')
        self.assertFalse(self.manager.context.get_visible())
        area = tree.get_cell_area(path, tree.get_column(0))
        event = SimpleNamespace(button=1, x=area.x + 8, y=area.y + area.height / 2)
        with patch.object(self.picker, 'run_macro') as run:
            self.assertTrue(self.manager.group_pressed(tree, event))
            self.assertFalse(tree.row_expanded(path))
            self.manager.refresh()
            self.assertFalse(tree.row_expanded(path))
            self.pump()
            self.manager.group_pressed(tree, event)
            self.assertTrue(tree.row_expanded(path))
            run.assert_not_called()
        self.assertFalse(self.manager.context.get_visible())

    def test_category_keyboard_navigation_still_expands_and_collapses(self):
        from gi.repository import Gdk
        tree = self.manager.tree
        path = self.picker.Gtk.TreePath.new_from_string('0')
        tree.set_cursor(path)
        tree.grab_focus()
        self.pump()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Left
        tree.emit('key-press-event', event)
        self.assertFalse(tree.row_expanded(path))
        event.keyval = Gdk.KEY_Right
        tree.emit('key-press-event', event)
        self.assertTrue(tree.row_expanded(path))
        tree.emit('key-press-event', event)
        self.assertEqual(tree.get_cursor()[0].to_string(), '0:0')
        event.keyval = Gdk.KEY_Left
        tree.emit('key-press-event', event)
        self.assertEqual(tree.get_cursor()[0].to_string(), '0')
        self.assertFalse(self.manager.context.get_visible())

    def test_list_scrolls_to_last_macro_and_footer_stays_visible_at_multiple_sizes(self):
        for i in range(30):
            macro = new_macro('login' if i < 15 else 'normal')
            macro.update(name=f'Menu action {i}', game='Demo', title_re='Demo', steps=['key:Enter'])
            self.manager.library.put(macro)
        for width, height in ((360, 420), (430, 520), (920, 640)):
            with self.subTest(width=width, height=height):
                picker = Picker(preview=True, width=width, height=height, library=self.manager.library)
                manager = Manager(picker, offscreen=True, width=width, height=height)
                try:
                    picker.render()
                    manager.show()
                    self.pump()
                    for window, scroll, footer, last_row in (
                        (picker.window, picker.quick_scroll, picker.quick_footer,
                         picker.quick_lists['normal'].get_row_at_index(14)),
                        (manager.window, manager.library_scroll, manager.summary, None),
                    ):
                        self.assertEqual((window.get_pixbuf().get_width(), window.get_pixbuf().get_height()), (width, height))
                        footer_y = footer.translate_coordinates(window, 0, 0)[1]
                        self.assertLessEqual(footer_y + footer.get_allocated_height(), height - 12)
                        self.assertGreater(footer_y + footer.get_allocated_height(), height - 24)
                        adjustment = scroll.get_vadjustment()
                        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
                        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                        self.pump()
                        self.assertAlmostEqual(adjustment.get_value(), adjustment.get_upper() - adjustment.get_page_size(), delta=1)
                        self.assertEqual(footer.translate_coordinates(window, 0, 0)[1], footer_y)
                        if last_row:
                            bottom = last_row.translate_coordinates(window, 0, last_row.get_allocated_height())[1]
                            scroll_bottom = scroll.translate_coordinates(window, 0, scroll.get_allocated_height())[1]
                            self.assertLessEqual(bottom, scroll_bottom + 1)
                            self.assertGreater(bottom, scroll_bottom - 4)
                        else:
                            self.assertEqual(manager.tree.get_visible_range()[1].to_string(), '1:14')
                finally:
                    manager.window.destroy()
                    picker.window.destroy()

    def test_collapsing_both_groups_keeps_footer_and_window_height(self):
        self.picker.render()
        self.pump()
        height = self.picker.window.get_pixbuf().get_height()
        footer_y = self.picker.quick_footer.translate_coordinates(self.picker.window, 0, 0)[1]
        for section in self.picker.quick_sections.values():
            section.set_expanded(False)
        self.pump()
        self.assertEqual(self.picker.window.get_pixbuf().get_height(), height)
        self.assertEqual(self.picker.quick_footer.translate_coordinates(self.picker.window, 0, 0)[1], footer_y)

    def test_compact_and_library_macro_names_share_left_alignment(self):
        self.picker.render()
        self.pump()
        compact_name = self.picker.quick_rows.get_row_at_index(0).get_child().get_children()[0]
        compact_x = compact_name.translate_coordinates(self.picker.window, 0, 0)[0]
        column = self.manager.tree.get_column(0)
        self.assertFalse(self.manager.tree.get_show_expanders())
        column.cell_set_cell_data(self.manager.rows, self.manager.rows.get_iter('0:0'), False, False)
        text_cell = column.get_cells()[1]
        library_x = self.manager.tree.translate_coordinates(self.manager.window, 0, 0)[0] + text_cell.get_property('xpad')
        self.assertEqual(compact_x, library_x)
        self.assertLess(compact_x, 40)

    def test_native_view_requests_bounded_height_below_reserved_desktop_bar(self):
        monitor = SimpleNamespace(get_workarea=lambda: SimpleNamespace(x=0, y=40, width=1000, height=560),
                                  get_geometry=lambda: SimpleNamespace(x=0, y=0, width=1000, height=600))
        display = SimpleNamespace(get_primary_monitor=lambda: monitor,
                                  get_n_monitors=lambda: 1, get_monitor=lambda _: monitor)
        from unittest.mock import Mock
        layer = Mock(Edge=SimpleNamespace(LEFT='left', TOP='top'))
        self.picker.preview = False
        try:
            with patch.object(self.picker.window, 'get_display', return_value=display), \
                 patch.object(self.picker, 'Layer', layer), \
                 patch('auto_macro.subprocess.check_output', return_value=b'[{"name":"TestDisplay","x":0,"y":0,"width":1000,"height":600,"reserved":[0,40,0,0]}]'):
                self.picker.resize_view(600)
            # Bar counted once: 40px + 8px gap, with 12px bottom breathing room.
            self.assertEqual(tuple(self.picker.window.get_size_request()), (self.picker.width, 540))
            self.assertEqual(self.picker.popup_geometry.y, 48)
            layer.set_margin.assert_any_call(self.picker.window, 'top', 48)
        finally:
            self.picker.preview = True


if __name__ == '__main__':
    unittest.main()
