"""Dismiss/resume interaction checks with no global input injection."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro


class DismissTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        library = MacroLibrary(Path(temp.name) / 'library.json')
        self.picker = Picker(preview=True, library=library)
        self.addCleanup(self.picker.window.destroy)
        self.picker.scan = lambda: None
        self.active = login.Window('other', 'Other app', 'kitty', 0, 0, 1000, 700)
        self.picker.manage()
        self.manager = self.picker.manager
        self.bounds = dict(address='0xabc', x=100, y=50, w=430, h=600)
        self.picker.popup_surface = Mock(return_value=self.bounds)
        self.picker.pointer_in_child_popup = Mock(return_value=False)

    def resume(self, auto=True):
        with patch.object(login, 'active_window', return_value=self.active):
            self.picker.open(auto=auto)

    def dismiss(self):
        self.assertTrue(self.picker.outside_click(20, 50, '0xabc'))
        self.assertFalse(self.picker.window.get_visible())

    def edit(self):
        macro = new_macro('normal')
        macro.update(game='Demo', title_re='Demo')
        self.manager.edit(macro)
        return macro

    def test_outside_click_and_f9_preserve_editor_draft_and_subpage(self):
        self.edit()
        field = self.manager.fields['name']
        field.set_text('Unsaved draft')
        self.manager.editor.get_children()[0].set_current_page(1)
        self.manager.record_setup()
        self.dismiss()
        self.resume()
        self.assertTrue(self.picker.window.get_visible())
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
        self.assertEqual(self.manager.views.get_visible_child_name(), 'editor')
        self.assertIs(self.manager.fields['name'], field)
        self.assertEqual(field.get_text(), 'Unsaved draft')
        self.assertEqual(self.manager.editor.get_children()[0].get_current_page(), 1)
        self.assertEqual(self.manager.action_stack.get_visible_child_name(), 'record')

    def test_history_and_files_resume_without_rebuilding_controls(self):
        self.manager.show_history()
        self.dismiss()
        self.resume(auto=False)
        self.assertEqual(self.manager.tabs.get_current_page(), 1)
        self.manager.choose_file(True)
        entry = next(child for child in self.manager.files_page.get_children() if isinstance(child, self.picker.Gtk.Entry))
        entry.set_text('/tmp/unsaved-export-path.json')
        self.dismiss()
        self.resume()
        self.assertEqual(self.manager.views.get_visible_child_name(), 'files')
        self.assertEqual(entry.get_text(), '/tmp/unsaved-export-path.json')

    def test_f9_toggle_preserves_page(self):
        self.manager.show_history()
        self.resume()
        self.assertFalse(self.picker.window.get_visible())
        self.resume()
        self.assertEqual(self.manager.tabs.get_current_page(), 1)
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')

    def test_outside_dismiss_then_bar_reopens_exact_page(self):
        self.edit()
        field = self.manager.fields['name']
        field.set_text('Unsaved draft')
        self.manager.editor.get_children()[0].set_current_page(1)
        self.manager.record_setup()
        self.dismiss()
        with patch.object(login, 'active_window', return_value=self.active):
            self.picker.toggle()
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
        self.assertEqual(self.manager.views.get_visible_child_name(), 'editor')
        self.assertIs(self.manager.fields['name'], field)
        self.assertEqual(field.get_text(), 'Unsaved draft')
        self.assertEqual(self.manager.action_stack.get_visible_child_name(), 'record')

    def test_back_to_main_resets_manager_to_library(self):
        for page in ('editor', 'history', 'files'):
            with self.subTest(page=page):
                if page == 'editor':
                    self.edit()
                elif page == 'history':
                    self.manager.show_history()
                else:
                    self.manager.choose_file(True)
                self.manager.close()
                self.assertTrue(self.picker.window.get_visible())
                self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')
                self.picker.manage()
                self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
                self.assertEqual(self.manager.views.get_visible_child_name(), 'browse')
                self.assertEqual(self.manager.tabs.get_current_page(), 0)

    def test_close_resets_navigation_and_next_open_starts_at_picker(self):
        self.edit()
        self.manager.close_popup()
        self.assertFalse(self.picker.window.get_visible())
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')
        self.assertEqual(self.manager.views.get_visible_child_name(), 'browse')
        self.assertEqual(self.manager.tabs.get_current_page(), 0)
        self.resume(auto=False)
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')

    def test_bar_toggle_closes_visible_and_resumes_hidden_page(self):
        self.edit()
        self.picker.toggle()
        self.assertFalse(self.picker.window.get_visible())
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')
        self.picker.manage()
        self.manager.show_history()
        self.dismiss()
        with patch.object(login, 'active_window', return_value=self.active):
            self.picker.toggle()
        self.assertTrue(self.picker.window.get_visible())
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
        self.assertEqual(self.manager.tabs.get_current_page(), 1)

    def test_bar_reopens_files_and_settings_after_outside_dismiss(self):
        self.manager.choose_file(True)
        entry = next(child for child in self.manager.files_page.get_children() if isinstance(child, self.picker.Gtk.Entry))
        entry.set_text('/tmp/draft-path.json')
        for page in ('manager', 'settings'):
            with self.subTest(page=page):
                if page == 'settings':
                    self.picker.show_settings()
                self.dismiss()
                with patch.object(login, 'active_window', return_value=self.active):
                    self.picker.toggle()
                self.assertEqual(self.picker.stack.get_visible_child_name(), page)
                self.assertEqual(entry.get_text(), '/tmp/draft-path.json')
        self.picker.leave_settings()
        self.assertEqual(self.manager.views.get_visible_child_name(), 'files')

    def test_single_header_back_follows_the_current_page(self):
        def assert_one_back(title):
            while self.picker.Gtk.events_pending():
                self.picker.Gtk.main_iteration_do(False)
            def buttons(widget):
                if isinstance(widget, self.picker.Gtk.Button) and widget.get_mapped():
                    if (widget.get_tooltip_text() or '').startswith('Back'):
                        yield widget
                if isinstance(widget, self.picker.Gtk.Container):
                    for child in widget.get_children():
                        yield from buttons(child)
            self.assertEqual(list(buttons(self.manager.root)), [self.manager.back_button])
            self.assertEqual(self.manager.back_button.get_tooltip_text(), title)
        self.edit()
        assert_one_back('Back to library')
        self.manager.editor.get_children()[0].set_current_page(1)
        self.manager.record_setup()
        assert_one_back('Back to actions')
        self.manager.back_button.clicked()
        self.assertEqual(self.manager.action_stack.get_visible_child_name(), 'edit')
        assert_one_back('Back to library')
        self.manager.back_button.clicked()
        assert_one_back('Back to game macros')
        self.manager.choose_file(True)
        assert_one_back('Back to library')
        self.manager.back_button.clicked()
        self.manager.show_history()
        assert_one_back('Back to library')
        self.manager.back_button.clicked()
        self.manager.back_button.clicked()
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')
        self.picker.manage()
        self.assertEqual(self.manager.views.get_visible_child_name(), 'browse')
        self.assertEqual(self.manager.tabs.get_current_page(), 0)

    def test_click_inside_widget_or_child_popup_does_not_dismiss(self):
        self.assertFalse(self.picker.outside_click(101, 51, '0xabc'))
        self.picker.pointer_in_child_popup.return_value = True
        self.assertFalse(self.picker.outside_click(600, 51, '0xabc'))
        self.assertTrue(self.picker.window.get_visible())

    def test_child_menu_press_remains_safe_after_menu_action_changes_page(self):
        self.picker.last_popup_press = (time.monotonic(), (600, 51))
        self.assertFalse(self.picker.outside_click(600, 51, '0xabc'))
        self.assertTrue(self.picker.window.get_visible())
        self.assertTrue(self.picker.outside_click(20, 51, '0xabc'))

    def test_stale_surface_hidden_and_playback_events_are_ignored(self):
        self.assertFalse(self.picker.outside_click(20, 50, 'old-surface'))
        self.picker.busy = True
        self.assertFalse(self.picker.outside_click(20, 50, '0xabc'))
        self.picker.busy = False
        self.picker.hide()
        self.assertFalse(self.picker.outside_click(20, 50, '0xabc'))

    def test_bar_press_waits_for_bar_toggle_instead_of_hiding_early(self):
        with tempfile.TemporaryDirectory() as directory:
            self.picker.popup_monitor = dict(name='Display', x=0, y=0)
            (Path(directory) / 'anchor-Display.json').write_text(json.dumps(dict(x=580, y=0, width=32, height=40)))
            with patch('auto_macro.STATE', Path(directory)):
                self.assertFalse(self.picker.outside_click(590, 20, '0xabc'))
                self.assertTrue(self.picker.window.get_visible())

    def test_stop_button_still_cancels_playback_and_recording(self):
        self.picker.busy = True
        self.picker.runner = Mock()
        self.picker.toggle()
        self.picker.runner.cancel.set.assert_called_once()
        self.picker.recorder = Mock()
        self.picker.toggle()
        self.picker.recorder.stop.set.assert_called_once()

    def test_playback_return_keeps_last_manager_page(self):
        self.manager.show_history()
        self.picker.hide()
        self.picker.busy = True
        self.picker.finished('Completed')
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
        self.assertEqual(self.manager.tabs.get_current_page(), 1)
        self.assertTrue(self.picker.window.get_visible())

    def test_gtk_child_popup_observation_always_delegates_normal_event_dispatch(self):
        from gi.repository import Gdk
        event = Mock(type=Gdk.EventType.BUTTON_PRESS)
        event.get_window.return_value.get_toplevel.return_value = object()
        self.picker.pointer_in_child_popup.return_value = True
        with patch.object(login, 'cursor_position', return_value=(700, 80)), \
             patch.object(self.picker.Gtk, 'main_do_event') as dispatch:
            self.picker.route_event(event)
        dispatch.assert_called_once_with(event)
        self.assertEqual(self.picker.last_popup_press[1], (700, 80))


class CompositorHookTests(unittest.TestCase):
    def test_non_consuming_hook_only_dispatches_outside_mapped_popup(self):
        code = '''
local callbacks, commands = {}, {}
local point = {x=10,y=10}
local layer = {namespace="gfn-macro-picker",mapped=true,x=100,y=50,w=430,h=600,address="0xabc"}
hl = {
 get_layers=function(filter) assert(filter.namespace=="gfn-macro-picker"); return {layer} end,
 get_cursor_pos=function() return point end,
 exec_cmd=function(command) table.insert(commands,command) end,
 bind=function(key, callback, flags)
   assert(flags.non_consuming and flags.ignore_mods and flags.transparent)
   table.insert(callbacks,callback)
   return {remove=function() end}
 end
}
dofile("popup_click.lua")
assert(#callbacks==3)
point={x=120,y=70};callbacks[1]();assert(#commands==0)
point={x=10,y=10};callbacks[1]();assert(#commands==1)
assert(commands[1]:find("pointer --point 10 10 --surface 0xabc",1,true))
layer.mapped=false;callbacks[1]();assert(#commands==1)
'''
        subprocess.run(['lua', '-'], input=code, text=True, check=True, cwd=Path(__file__).resolve().parent.parent)


if __name__ == '__main__':
    unittest.main()
