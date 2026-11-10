import unittest
from unittest.mock import patch
from detection import Sessions, candidates, login
from auto_macro import Picker


def window(address='a', title='Zenless Zone Zero', cls='GeForceNOW'):
    return login.Window(address, title, cls, 0, 0, 1920, 1080)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.profile = login.Profile('젠존제', '(?i)Zenless|젠레스', '', '', 1920, 1080, [])
        self.other = login.Profile('스타레일', 'Star Rail', '', '', 1920, 1080, [])
        self.profiles = [self.profile, self.other]
        self.sessions = Sessions()

    def update(self, windows):
        self.sessions.update(windows, self.profiles)

    def test_first_f9_claimed_only_once(self):
        w = window()
        self.update([w])
        game = self.sessions.select(w)
        self.assertTrue(self.sessions.claim_first(game))
        self.assertFalse(self.sessions.claim_first(game))
        self.update([window(title='Zenless Zone Zero - GeForce NOW')])
        self.assertFalse(self.sessions.claim_first(self.sessions.select(w)))

    def test_game_switch_rearms_same_window(self):
        self.update([window()])
        self.sessions.claim_first(self.sessions.select(window()))
        w = window(title='Star Rail')
        self.update([w])
        self.assertTrue(self.sessions.claim_first(self.sessions.select(w)))

    def test_idle_and_close_rearm(self):
        for ending in ([window(title='GeForce NOW')], []):
            self.update([window()])
            self.assertTrue(self.sessions.claim_first(self.sessions.select(window())))
            self.update(ending)
            self.assertFalse(self.sessions.games)

    def test_browser_not_misidentified(self):
        self.assertFalse(candidates([window(title='Inbox', cls='chrome')], self.profiles))
        self.assertFalse(candidates([window(cls='kitty')], self.profiles))
        self.assertTrue(candidates([window(cls='chrome')], self.profiles))

    def test_unknown_game_no_auto_login(self):
        w = window(title='Unknown game')
        self.update([w])
        self.assertFalse(self.sessions.claim_first(self.sessions.select(w)))

    def test_multiple_windows_require_selection(self):
        self.update([window(), window('b', 'Star Rail')])
        self.assertIsNone(self.sessions.select(window('c', 'Terminal', 'kitty')))
        self.assertEqual(self.sessions.select(window('b'))[1], self.other)

    def test_no_focus_switch_before_input(self):
        picker = object.__new__(Picker)
        picker.profiles = self.profiles
        game = (window(), self.profile)
        with patch.object(login, 'active_window', return_value=window('b')):
            with self.assertRaises(login.LoginError):
                picker.verify_target(game)
        with patch.object(login, 'active_window', return_value=window()):
            self.assertEqual(picker.verify_target(game).address, 'a')

    def test_changed_unknown_title_rejected(self):
        picker = object.__new__(Picker)
        picker.profiles = self.profiles
        with patch.object(login, 'active_window', return_value=window(title='Other game')):
            with self.assertRaises(login.LoginError):
                picker.verify_target((window(title='Unknown game'), None))

    def test_attempts_survive_restart(self):
        self.update([window()])
        self.sessions.claim_first(self.sessions.select(window()))
        restored = Sessions(self.sessions.attempted)
        restored.update([window()], self.profiles)
        self.assertFalse(restored.claim_first(restored.select(window())))


if __name__ == '__main__':
    unittest.main()
