import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from detection import login
from macro_library import MacroLibrary, new_macro, validate
from macro_engine import MacroRunner, Cancelled


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'library.json'
        self.profile = login.Profile('Demo', 'Demo', 'private-account', 'key', 1920, 1080, ['id', 'tab', 'pw', 'enter'])
        self.library = MacroLibrary(self.path, [self.profile])

    def tearDown(self):
        self.tmp.cleanup()

    def test_hide_and_delete_remove_auto_and_survive_restart(self):
        macro = self.library.macros[0]
        self.assertIsNotNone(self.library.auto_for('Demo'))
        self.library.hide(macro['id'])
        self.assertIsNone(self.library.auto_for('Demo'))
        restored = MacroLibrary(self.path, [self.profile])
        self.assertTrue(restored.macros[0]['hidden'])
        restored.delete(macro['id'])
        self.assertEqual(restored.import_legacy([self.profile]), 0)
        self.assertFalse(MacroLibrary(self.path, [self.profile]).macros)
        restored.restore(macro['id'])
        self.assertFalse(restored.macros[0]['auto'])

    def test_add_edit_duplicate_and_single_auto_trigger(self):
        macro = new_macro('login')
        macro.update(name='Second login', game='Demo', title_re='Demo', user='new-account', auto=True)
        self.library.put(macro)
        self.assertEqual(self.library.auto_for('Demo')['id'], macro['id'])
        duplicate = self.library.duplicate(macro['id'])
        self.assertFalse(duplicate['auto'])
        macro['name'] = 'Renamed'
        self.library.put(macro)
        self.assertEqual(self.library.get(macro['id'])['name'], 'Renamed')

    def test_export_excludes_accounts_import_no_auto(self):
        macro = new_macro('login')
        macro.update(game='Other', title_re='Other', user='secret-account', name='Other login')
        self.library.put(macro)
        export = self.path.with_name('export.json')
        self.library.export_file(export)
        self.assertNotIn('secret-account', export.read_text())
        other = MacroLibrary(self.path.with_name('other.json'))
        self.assertEqual(other.import_file(export), 2)
        self.assertTrue(all(not m['source_profile'] for m in other.macros))
        self.assertEqual(other.macros[0]['steps'], self.profile.steps)
        self.assertTrue(all(not m['auto'] for m in other.macros))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.library.export_file(self.path)

    def test_invalid_import_atomic_and_password_rejected(self):
        incoming = copy.deepcopy(self.library.data)
        incoming['macros'][0]['steps'] = ['shell:rm -rf /']
        invalid = self.path.with_name('invalid.json')
        invalid.write_text(json.dumps(incoming))
        with self.assertRaises(ValueError):
            self.library.import_file(invalid)
        self.assertEqual(len(self.library.macros), 1)
        incoming['macros'][0]['password'] = 'must-not-save'
        with self.assertRaises(ValueError):
            validate(incoming['macros'][0])

    def test_legacy_account_is_reference_only(self):
        self.assertNotIn('private-account', self.path.read_text())
        macro = self.library.macros[0]
        updated = login.Profile('Demo', 'Demo', 'updated', 'key', 1920, 1080, ['enter'])
        self.assertEqual(self.library.resolved_profile(macro, [updated]).steps, ['enter'])

    def test_invalid_regex_does_not_save(self):
        macro = copy.deepcopy(self.library.macros[0])
        macro['title_re'] = '['
        with self.assertRaises(ValueError):
            self.library.put(macro)
        self.assertEqual(self.library.get(macro['id'])['title_re'], 'Demo')


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = MacroLibrary(Path(self.tmp.name) / 'library.json')
        self.window = login.Window('a', 'Demo', 'GeForceNOW', 0, 0, 1920, 1080)
        self.macro = new_macro('sequence')
        self.macro.update(game='Demo', title_re='Demo', name='Menu', steps=['key:Ctrl+A', 'key:Enter'], step_delay=0, repeat_delay=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_repeats_and_key_release_order(self):
        self.macro['repeat'] = 2
        runner = MacroRunner(self.library, lambda: self.window)
        with patch.object(login, 'run') as run:
            runner.run(self.macro, [], {})
            self.assertEqual(run.call_count, 4)
            self.assertEqual(run.call_args_list[0].args[0][-4:], ['29:1', '30:1', '30:0', '29:0'])
            self.assertEqual(runner.completed, 4)

    def test_cancellation_sends_no_more_input(self):
        runner = MacroRunner(self.library, lambda: self.window)
        def first(*_):
            runner.cancel.set()
        with patch.object(login, 'run', side_effect=first) as run:
            with self.assertRaises(Cancelled):
                runner.run(self.macro, [], {})
            self.assertEqual(run.call_count, 1)

    def test_focus_loss_stops_before_input(self):
        def changed():
            raise login.LoginError('Focus changed')
        runner = MacroRunner(self.library, changed)
        with patch.object(login, 'run') as run:
            with self.assertRaises(login.LoginError):
                runner.run(self.macro, [], {})
            run.assert_not_called()

    def test_account_keyring_text_and_reference_click(self):
        self.macro.update(kind='login', user='account', credential_key='test-key',
                          steps=['click:100,200', 'id', 'tab', 'pw', 'enter'])
        runner = MacroRunner(self.library, lambda: self.window)
        with patch.object(login, 'click') as click, patch.object(login, 'clear_field'), \
             patch.object(login, 'type_text') as typed, patch.object(login, 'get_secret', return_value='private-password') as secret, \
             patch.object(login, 'run'):
            runner.run(self.macro, [], {})
            self.assertEqual(click.call_args.args[-2:], (100, 200))
            self.assertEqual([call.args[0] for call in typed.call_args_list], ['account', 'private-password'])
            secret.assert_called_once_with('test-key')

    def test_focus_checked_during_wait(self):
        self.macro['steps'] = ['wait:5000', 'key:Enter']
        calls = 0
        def focused():
            nonlocal calls
            calls += 1
            if calls > 3:
                raise login.LoginError('Focus changed')
            return self.window
        runner = MacroRunner(self.library, focused)
        with patch.object(login, 'run') as run:
            with self.assertRaises(login.LoginError):
                runner.run(self.macro, [], {})
            run.assert_not_called()

    def test_login_cutoff_and_full_playback(self):
        self.macro.update(kind='login', steps=['key:Enter', 'key:Escape'], login_limit=1)
        runner = MacroRunner(self.library, lambda: self.window)
        with patch.object(login, 'run') as run:
            runner.run(self.macro, [], {})
            self.assertEqual(run.call_count, 1)
        with patch.object(login, 'run') as run:
            runner.run(self.macro, [], {}, full=True)
            self.assertEqual(run.call_count, 2)

    def test_legacy_progress_matches_real_login_cutoff_and_repeat(self):
        profile = login.Profile('Demo', 'Demo', 'test-account', 'test-key', 1920, 1080,
                                ['enter', 'wait:2500', 'enter'])
        library = MacroLibrary(Path(self.tmp.name) / 'legacy.json', [profile])
        macro = library.macros[0]
        macro.update(auto=False, repeat=2, repeat_delay=0)
        for full, total in ((False, 1), (True, 3)):
            progress = Mock()
            runner = MacroRunner(library, lambda: self.window, progress)
            with patch.object(login, 'active_window', return_value=self.window), \
                 patch.object(login, 'get_secret', return_value='test-password'), \
                 patch.object(login, 'verify_window', return_value=self.window), \
                 patch.object(login, 'send_key') as send, patch.object(login, 'notify'), \
                 patch.object(login, 'log'), patch.object(login.time, 'sleep'):
                runner.run(macro, [profile], {}, full=full)
            self.assertEqual(send.call_count, (2 if full else 1) * 2)
            self.assertEqual(runner.completed, total * 2)
            self.assertEqual([call.args[:4] for call in progress.call_args_list],
                             [(step, total, iteration, 2)
                              for iteration in (1, 2) for step in range(1, total + 1)])


if __name__ == '__main__':
    unittest.main()
