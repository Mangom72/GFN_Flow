"""Structured GTK inputs, compatibility and reliable selected-action editing."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro, parse_step
from macro_manager import Manager


class InputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = login.Profile('Demo', r'Demo|Example', 'dummy-account', 'dummy-key', 1920, 1080, ['enter'])
        self.library = MacroLibrary(Path(temporary.name) / 'library.json', [self.profile])
        with patch.object(login, 'load_profiles', return_value=({}, [self.profile])):
            self.picker = Picker(preview=True, library=self.library)
        self.picker.scan = lambda: None
        self.manager = Manager(self.picker, offscreen=True, width=360, height=480)
        self.manager.show()
        self.addCleanup(self.picker.window.destroy)
        self.addCleanup(self.manager.window.destroy)
        macro = new_macro('normal')
        macro.update(name='Menu', game='Demo', title_re=r'Demo|Example', steps=['wait:500', 'key:Enter'])
        self.manager.edit(macro)

    def pump(self):
        while self.picker.Gtk.events_pending():
            self.picker.Gtk.main_iteration_do(False)

    def select(self, index):
        self.manager.steps_view.get_selection().select_path(index)

    def test_all_existing_action_kinds_can_load_and_generate_equivalent_steps(self):
        inputs = self.manager.inputs
        for step in ('wait:30', 'click:12,34', 'key:Ctrl+Alt+Delete', 'key:{Ctrl}+{A}',
                     'key:Ctrl', 'key:Shift+Ctrl', 'key:RIGHTALT+K', 'key:A+B', 'key:Alt+Ctrl+K', 'key:A+Ctrl',
                     'text:  hello:world\nsecond line  ', 'text:', 'clear', 'tab', 'enter',
                     'shifttab', 'id', 'pw'):
            with self.subTest(step=step):
                inputs.load(step)
                self.assertEqual(parse_step(inputs.value()), parse_step(step))

    def test_add_shortcut_uses_key_dropdown_and_modifier_checkboxes(self):
        inputs = self.manager.inputs
        inputs.action.set_active_id('key')
        inputs.key.set_active_id('A')
        inputs.modifiers['Ctrl'].set_active(True)
        self.manager.add_step()
        self.assertEqual(self.manager.steps[-1][0], 'key:Ctrl+A')
        self.assertEqual(self.manager.action_title.get_text(), 'Edit action 3')

    def test_selecting_an_imported_action_preserves_original_spelling(self):
        original = ['wait:0005', 'click:001, 02', 'key:{Control}+{a}', 'text: x\ny ']
        self.manager.steps.clear()
        for step in original:
            self.manager.steps.append([step])
        for index in range(len(original)):
            self.select(index)
        self.assertEqual([row[0] for row in self.manager.steps], original)

    def test_edit_selected_delay_and_save_without_separate_apply_step(self):
        self.select(0)
        self.manager.inputs.delay.set_value(1250)
        self.manager.save_draft()
        self.assertEqual(self.library.macros[-1]['steps'], ['wait:1250', 'key:Enter'])

    def test_invalid_shortcut_blocks_save_and_keeps_the_selected_action(self):
        self.select(1)
        self.manager.inputs.key.get_child().set_text('not a real key')
        self.assertFalse(self.manager.action_valid)
        self.assertFalse(self.manager.save_button.get_sensitive())
        self.assertFalse(self.manager.add_action_button.get_sensitive())
        self.assertTrue(self.manager.action_error.get_visible())
        self.select(0)
        self.assertEqual(self.manager.steps_view.get_selection().get_selected()[0].get_path(
            self.manager.steps_view.get_selection().get_selected()[1]).to_string(), '1')
        with self.assertRaises(ValueError):
            self.manager.save_draft()
        self.assertEqual([row[0] for row in self.manager.steps], ['wait:500', 'key:Enter'])
        self.manager.inputs.key.set_active_id('ESC')
        self.assertTrue(self.manager.action_valid)
        self.assertTrue(self.manager.save_button.get_sensitive())
        self.assertEqual(self.manager.steps[1][0], 'key:ESC')

    def test_invalid_numeric_text_is_not_silently_saved_as_previous_value(self):
        self.select(0)
        self.manager.inputs.delay.set_text('')
        self.assertFalse(self.manager.action_valid)
        with self.assertRaises(ValueError):
            self.manager.save_draft()
        self.manager.inputs.delay.set_text('700000')
        self.assertFalse(self.manager.action_valid)
        self.manager.inputs.delay.set_text('250')
        self.assertEqual(self.manager.steps[0][0], 'wait:250')
        self.assertTrue(self.manager.action_valid)

    def test_coordinates_validate_against_the_reference_resolution(self):
        self.manager.inputs.action.set_active_id('click')
        self.manager.inputs.x.set_value(1921)
        self.assertFalse(self.manager.action_valid)
        self.manager.inputs.x.set_value(1920)
        self.manager.inputs.y.set_value(1080)
        self.manager.add_step()
        self.assertEqual(self.manager.steps[-1][0], 'click:1920,1080')
        self.manager.fields['ref_width'].set_value(1280)
        self.assertFalse(self.manager.action_valid)
        self.manager.inputs.x.set_value(1280)
        self.assertTrue(self.manager.action_valid)

    def test_typed_reference_dimensions_are_used_without_waiting_for_focus_out(self):
        self.manager.fields['ref_width'].set_text('2560')
        self.manager.inputs.action.set_active_id('click')
        self.manager.inputs.x.set_value(2400)
        self.manager.inputs.y.set_value(1000)
        self.assertTrue(self.manager.action_valid)
        self.manager.add_step()
        self.manager.fields['ref_width'].set_value(1920)
        self.assertFalse(self.manager.action_valid)
        self.assertTrue(self.manager.notice.get_visible())
        self.manager.fields['ref_width'].set_text('2560')
        self.assertTrue(self.manager.action_valid)
        self.assertFalse(self.manager.notice.get_visible())
        self.manager.save_draft()
        self.assertEqual(self.library.macros[-1]['ref_width'], 2560)
        self.assertEqual(self.library.macros[-1]['steps'][-1], 'click:2400,1000')

    def test_reference_edits_do_not_rewrite_the_selected_legacy_shortcut(self):
        self.manager.steps[1][0] = 'key:A+Ctrl'
        self.select(1)
        self.manager.fields['ref_width'].set_text('2560')
        self.assertEqual(self.manager.steps[1][0], 'key:A+Ctrl')
        self.manager.add_step()
        self.assertEqual(self.manager.steps[-1][0], 'key:A+Ctrl')

    def test_reordering_keeps_the_editor_attached_to_the_same_action(self):
        self.select(1)
        self.manager.move_step(-1)
        self.manager.inputs.modifiers['Ctrl'].set_active(True)
        self.assertEqual([row[0] for row in self.manager.steps], ['key:Ctrl+ENTER', 'wait:500'])
        self.assertEqual(self.manager.action_title.get_text(), 'Edit action 1')

    def test_invalid_action_can_be_removed_or_its_edits_cleared(self):
        self.select(1)
        self.manager.inputs.key.get_child().set_text('bogus')
        self.manager.remove_step()
        self.assertEqual([row[0] for row in self.manager.steps], ['wait:500'])
        self.assertTrue(self.manager.action_valid)
        self.select(0)
        self.manager.inputs.delay.set_text('')
        self.manager.new_action()
        self.assertEqual(self.manager.steps[0][0], 'wait:500')
        self.assertTrue(self.manager.action_valid)
        self.assertEqual(self.manager.action_title.get_text(), 'New action')

    def test_text_input_preserves_newlines_colons_and_surrounding_spaces(self):
        text = '  hello:world\nline two  '
        self.manager.inputs.action.set_active_id('text')
        self.manager.inputs.text.get_buffer().set_text(text)
        self.manager.add_step()
        self.manager.save_draft()
        self.assertEqual(self.library.macros[-1]['steps'][-1], 'text:' + text)

    def test_saved_password_action_never_reads_or_displays_the_password(self):
        with patch.object(login, 'get_secret') as secret:
            self.manager.inputs.action.set_active_id('pw')
            self.manager.add_step()
            secret.assert_not_called()
        self.assertEqual(self.manager.steps[-1][0], 'pw')

    def test_known_game_dropdown_fills_the_existing_detection_rule(self):
        self.manager.edit(new_macro('normal'))
        target = self.manager.target
        identity = next(key for key, value in target.options.items() if value == ('Demo', r'Demo|Example'))
        target.choice.set_active_id(identity)
        self.assertFalse(target.custom.get_visible())
        self.manager.save_draft()
        self.assertEqual((self.library.macros[-1]['game'], self.library.macros[-1]['title_re']), ('Demo', r'Demo|Example'))

    def test_custom_game_title_contains_escapes_regex_metacharacters(self):
        self.manager.edit(new_macro('normal'))
        target = self.manager.target
        target.choice.set_active_id('custom')
        target.game.set_text('Custom game')
        title = 'Demo [EU] (DX12).+'
        target.contains.set_text(title)
        self.assertTrue(target.game.get_visible())
        self.assertTrue(target.match.get_visible())
        self.manager.save_draft()
        pattern = self.library.macros[-1]['title_re']
        self.assertIsNotNone(re.search(pattern, 'GFN ' + title))
        self.assertIsNone(re.search(pattern, 'Demo E DX12xyz'))

    def test_custom_advanced_detection_keeps_existing_regex_unchanged(self):
        target = self.manager.target
        target.choice.set_active_id('custom')
        self.assertEqual(target.match.get_active_id(), 'pattern')
        self.manager.save_draft()
        self.assertEqual(self.library.macros[-1]['title_re'], r'Demo|Example')

    def test_switching_from_preset_to_new_custom_game_requires_a_title(self):
        self.manager.edit(new_macro('normal'))
        target = self.manager.target
        target.choice.set_active_id('0')
        target.choice.set_active_id('custom')
        self.assertEqual(target.pattern.get_text(), '')
        with self.assertRaises(ValueError):
            self.manager.save_draft()

    def test_clearing_game_choice_cannot_keep_a_hidden_previous_target(self):
        self.manager.target.choice.set_active_id('none')
        self.assertEqual(self.manager.fields['game'].get_text(), '')
        self.assertEqual(self.manager.fields['title_re'].get_text(), '')
        with self.assertRaises(ValueError):
            self.manager.save_draft()

    def test_action_forms_do_not_expand_small_windows(self):
        self.manager.window.set_size_request(360, 420)
        for action in ('wait', 'click', 'key', 'text', 'pw'):
            with self.subTest(action=action):
                self.manager.inputs.action.set_active_id(action)
                self.pump()
                self.assertEqual(self.manager.window.get_pixbuf().get_width(), 360)
                self.assertEqual(self.manager.window.get_pixbuf().get_height(), 420)
                self.assertLessEqual(self.manager.inputs.root.get_allocated_width(), 328)

    def test_settings_numeric_input_is_saved_even_before_focus_leaves_the_field(self):
        self.manager.fields['repeat'].set_text('3')
        self.manager.save_draft()
        self.assertEqual(self.library.macros[-1]['repeat'], 3)
        before = self.library.path.read_bytes()
        self.manager.fields['repeat_delay'].set_text('bad')
        with self.assertRaises(ValueError):
            self.manager.save_draft()
        self.assertEqual(self.library.path.read_bytes(), before)

    def test_short_window_inspector_scrolls_to_validation_without_hiding_save(self):
        self.manager.window.set_size_request(360, 420)
        self.select(1)
        self.manager.inputs.key.get_child().set_text('Unknown key')
        self.pump()
        scroll = self.manager.inspector_scroll
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        self.pump()
        error_bottom = self.manager.action_error.translate_coordinates(self.manager.window, 0,
            self.manager.action_error.get_allocated_height())[1]
        scroll_bottom = scroll.translate_coordinates(self.manager.window, 0, scroll.get_allocated_height())[1]
        self.assertLessEqual(error_bottom, scroll_bottom + 1)
        save_bottom = self.manager.save_button.translate_coordinates(self.manager.window, 0,
            self.manager.save_button.get_allocated_height())[1]
        self.assertLessEqual(save_bottom, 420)
        self.assertEqual(self.manager.window.get_pixbuf().get_height(), 420)


if __name__ == '__main__':
    unittest.main()
