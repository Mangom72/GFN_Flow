"""Persistent game discovery and display-only migration, without desktop input."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import Sessions, candidates, game_name, login, session_key
from macro_library import MacroLibrary, new_macro
from macro_manager import Manager


def window(title, address='game', cls='GeForceNOW'):
    return login.Window(address, title, cls, 0, 0, 1920, 1080)


class GameCatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'library.json'

    def test_title_names_strip_branding_and_apply_only_the_requested_alias(self):
        for title, expected in [
            ('GeForce NOW Zenless Zone Zero', 'Zenless Zone Zero'),
            ('Geforce Now: Game [EU] (DX12)', 'Game [EU] (DX12)'),
            ('Game name - GeForce NOW', 'Game name'),
            ('GeForce NOW Super Raft Boat Classic', 'Salsa Now'),
            ('super raft boat classic', 'Salsa Now'),
            ('GeForce NOW Other Game - Chromium', 'Other Game'),
            ('GeForce NOW', ''), ('GeForce NOW - Chromium', ''),
        ]:
            with self.subTest(title=title):
                self.assertEqual(game_name(title), expected)

    def test_migrates_existing_games_and_trash_but_preserves_source_keys_actions_and_custom_names(self):
        profile = login.Profile('젠존제', 'Zenless|젠레스', 'private-account', 'original-key', 1920, 1080, ['id', 'pw'])
        library = MacroLibrary(self.path, [profile])
        macro = library.macros[0]
        self.assertEqual(macro['game'], 'Zenless Zone Zero')
        self.assertEqual(macro['name'], 'Zenless Zone Zero login')
        self.assertEqual(macro['source_profile'], '젠존제')
        self.assertEqual(macro['credential_key'], 'original-key')
        self.assertEqual(macro['steps'], ['id', 'pw'])
        with patch.object(login, 'get_secret') as secret:
            self.assertEqual(library.resolved_profile(macro, [profile]).user, 'private-account')
            secret.assert_not_called()
        deleted = copy.deepcopy(macro)
        deleted.update(id='deleted', game='젠존제', name='My custom login')
        library.data['trash'].append(deleted)
        library.save()
        loaded = MacroLibrary(self.path, [profile])
        self.assertEqual(loaded.data['trash'][0]['game'], 'Zenless Zone Zero')
        self.assertEqual(loaded.data['trash'][0]['name'], 'My custom login')
        self.assertNotIn('private-account', self.path.read_text())

    def test_unknown_games_persist_after_closing_and_restart_without_creating_macros(self):
        library = MacroLibrary(self.path)
        windows = [window('GeForce NOW Super Raft Boat Classic'),
                   window('GeForce NOW Game [EU] (DX12)', 'other')]
        self.assertTrue(library.remember_detected(candidates(windows, []).values()))
        self.assertEqual([game['name'] for game in library.games], ['Salsa Now', 'Game [EU] (DX12)'])
        self.assertFalse(library.macros)
        loaded = MacroLibrary(self.path)
        self.assertEqual(loaded.games, library.games)
        profiles = loaded.detection_profiles([])
        self.assertEqual(login.detect_profile(profiles, windows[0].title).name, 'Salsa Now')
        self.assertIsNone(login.detect_profile(profiles, 'GeForce NOW Game E (DX12)'))
        self.assertIsNone(loaded.auto_for(windows[0].title))
        self.assertIn(('Salsa Now', loaded.games[0]['title_re']), loaded.game_options())
        with patch.object(loaded, 'save') as save:
            self.assertFalse(loaded.remember_detected(candidates(windows, profiles).values()))
            save.assert_not_called()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_saved_match_rules_use_real_game_title_not_display_alias(self):
        pattern = MacroLibrary.game_pattern('GeForce NOW Super Raft Boat Classic - Chromium')
        for title in ['Super Raft Boat Classic', 'GeForce NOW Super Raft Boat Classic',
                      'Super Raft Boat Classic - GeForce NOW', 'GeForce NOW Super Raft Boat Classic - Chromium']:
            self.assertRegex(title, re.compile(pattern, re.I))
        self.assertIsNone(re.search(pattern, 'GeForce NOW Salsa Now', re.I))

    def test_scan_ignores_blocked_windows_after_restart_and_restore_does_not_rearm_login(self):
        profile = login.Profile('Demo', '^GeForce NOW Demo$', '', '', 1920, 1080, ['id', 'pw'])
        library = MacroLibrary(self.path, [profile])
        game = window('GeForce NOW Demo')
        other = window('GeForce NOW Other', address='other')
        windows = [game, other]
        with patch.object(login, 'load_profiles', return_value=({}, [profile])), \
                patch.object(login, 'all_windows', return_value=windows):
            picker = Picker(preview=True, library=library)
            self.addCleanup(picker.window.destroy)
            picker.persist = lambda: None
            picker.scan()
            self.assertTrue(picker.sessions.claim_first(picker.sessions.games[game.address]))
            library.move_game('Demo', from_blacklist=False)
            picker.library = library = MacroLibrary(self.path, [profile])
            picker.scan()
            self.assertEqual(list(picker.sessions.games), [other.address])
            self.assertEqual([entry['name'] for entry in library.games], ['Other'])
            self.assertEqual(library.game_options([profile])[0][0], 'Other')
            self.assertEqual(len(library.macros), 1)
            library.move_game('Demo', from_blacklist=True, restore=True)
            picker.scan()
            self.assertIn(game.address, picker.sessions.games)
            self.assertFalse(picker.sessions.claim_first(picker.sessions.games[game.address]))

    def test_idle_browser_and_unrelated_windows_are_not_saved(self):
        library = MacroLibrary(self.path)
        windows = [window('GeForce NOW'), window('GeForce NOW - Chromium'),
                   window('Inbox', 'mail', 'chrome'), window('GeForce NOW Fake game', 'terminal', 'kitty')]
        self.assertFalse(library.remember_detected(candidates(windows, []).values()))
        self.assertFalse(library.games)

    def test_unbranded_browser_headings_do_not_rename_existing_games(self):
        profile = login.Profile('젠존제', 'Zenless', '', '', 1920, 1080, ['enter'])
        library = MacroLibrary(self.path, [profile])
        w = window('Zenless Zone Zero wiki - Google Chrome', cls='chrome')
        library.remember_detected(candidates([w], [profile]).values())
        self.assertEqual(library.games[0]['name'], 'Zenless Zone Zero')

    def test_actual_detected_name_updates_matching_presets_without_rearming_login(self):
        profile = login.Profile('젠존제', 'Zenless', '', 'key', 1920, 1080, ['enter'])
        library = MacroLibrary(self.path, [profile])
        w = window('GeForce NOW Zenless Zone Zero')
        sessions = Sessions()
        sessions.update([w], library.detection_profiles([profile]))
        key = session_key(*sessions.games[w.address])
        self.assertTrue(sessions.claim_first(sessions.games[w.address]))
        library.remember_detected(sessions.games.values())
        sessions.update([w], library.detection_profiles([profile]))
        self.assertEqual(session_key(*sessions.games[w.address]), key)
        self.assertFalse(sessions.claim_first(sessions.games[w.address]))

    def test_catalog_name_changes_do_not_rearm_and_old_claims_migrate(self):
        w = window('GeForce NOW Demo')
        profile = login.Profile('Old name', 'Demo', '', '', 1920, 1080, [])
        sessions = Sessions([w.address + ':Old name'])
        sessions.update([w], [profile])
        self.assertFalse(sessions.claim_first(sessions.games[w.address]))
        renamed = login.Profile('New name', 'Demo', '', '', 1920, 1080, [])
        sessions.update([w], [renamed])
        self.assertFalse(sessions.claim_first(sessions.games[w.address]))

    def test_macro_only_legacy_claim_survives_display_name_migration(self):
        for old_name in ['젠존제', 'GeForce NOW Zenless Zone Zero']:
            with self.subTest(old_name=old_name):
                macro = new_macro('login')
                macro.update(name='My login', game=old_name, title_re='Zenless', auto=True)
                self.path.write_text(json.dumps(dict(version=2, macros=[macro], trash=[])))
                library = MacroLibrary(self.path)
                w = window('GeForce NOW Zenless Zone Zero')
                sessions = Sessions([w.address + ':' + old_name])
                sessions.update([w], library.detection_profiles([]))
                self.assertFalse(sessions.claim_first(sessions.games[w.address]))
                self.assertEqual(len(sessions.attempted), 1)

    def test_changing_saved_game_name_keeps_only_one_automatic_login(self):
        library = MacroLibrary(self.path)
        first = new_macro('login')
        first.update(name='First', game='Demo', title_re='Demo', auto=True)
        library.put(first)
        second = new_macro('login')
        second.update(name='Second', game='New display name', title_re='Demo', auto=True)
        library.put(second)
        self.assertFalse(library.get(first['id'])['auto'])
        self.assertTrue(library.get(second['id'])['auto'])
        self.assertEqual(library.game_options(), [('New display name', 'Demo')])

    def test_catalog_export_import_and_legacy_import_stay_compatible(self):
        library = MacroLibrary(self.path)
        w = window('GeForce NOW Super Raft Boat Classic')
        library.remember_detected(candidates([w], []).values())
        export = self.path.with_name('export.json')
        library.export_file(export)
        other = MacroLibrary(self.path.with_name('other.json'))
        self.assertEqual(other.import_file(export), 0)
        self.assertEqual(other.games, library.games)
        macro = new_macro('normal')
        macro.update(name='Menu', game='GeForce NOW Demo', title_re='Demo')
        export.write_text(json.dumps(dict(version=2, macros=[macro], trash=[])))
        self.assertEqual(other.import_file(export), 1)
        self.assertEqual(other.macros[0]['game'], 'Demo')
        self.assertEqual(other.macros[0]['name'], 'Menu')

    def test_invalid_catalog_import_preserves_existing_library(self):
        library = MacroLibrary(self.path)
        before = self.path.read_text()
        export = self.path.with_name('invalid.json')
        for games in ['bad', [dict(name='Game', title_re='[')],
                      [dict(name='Game', title_re='Game', password='forbidden')]]:
            export.write_text(json.dumps(dict(version=2, macros=[], games=games)))
            with self.assertRaises(ValueError):
                library.import_file(export)
            self.assertEqual(self.path.read_text(), before)

    def test_real_scan_saves_all_games_and_exposes_them_in_game_dropdown(self):
        library = MacroLibrary(self.path)
        with patch.object(login, 'load_profiles', return_value=({}, [])):
            picker = Picker(preview=True, library=library)
            self.addCleanup(picker.window.destroy)
            w = window('GeForce NOW Super Raft Boat Classic')
            with patch.object(login, 'all_windows', return_value=[w]):
                picker.scan()
            self.assertEqual(library.games[0]['name'], 'Salsa Now')
            picker.game = picker.sessions.games[w.address]
            picker.render()
            header = picker.panel.get_children()[0]
            labels = [child.get_text() for child in header.get_children() if isinstance(child, picker.Gtk.Label)]
            self.assertIn('Salsa Now macros', labels)
            self.assertFalse(picker.tool_buttons['calibrate'].get_sensitive())
            manager = Manager(picker, offscreen=True, width=360)
            self.addCleanup(manager.window.destroy)
            manager.edit(new_macro('normal'))
            self.assertIn(('Salsa Now', library.games[0]['title_re']), manager.target.options.values())
            with patch.object(login, 'all_windows', return_value=[]):
                picker.scan()
            self.assertTrue(library.games)

