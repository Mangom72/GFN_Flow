"""Reuse the installed login detector; never duplicate profiles or credentials."""
import importlib.util
import sys
import re
from pathlib import Path

LOGIN_ROOT = Path(__file__).with_name('login')
LEGACY_LOGIN_ROOT = Path.home() / '.local/share/gfn-autologin-linux'
if not (LOGIN_ROOT / 'gfn_login.py').is_file():
    LOGIN_ROOT = LEGACY_LOGIN_ROOT
spec = importlib.util.spec_from_file_location('gfn_login', LOGIN_ROOT / 'gfn_login.py')
login = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = login
spec.loader.exec_module(login)
if sys.platform == 'win32':
    import windows_input
    from windows_host import CONFIG
    login.CONFIG = CONFIG / 'profiles.ini'
    windows_input.configure()


LEGACY_GAME_NAMES = {
    '스타레일': 'Honkai: Star Rail',
    '젠존제': 'Zenless Zone Zero',
    '명조': 'Wuthering Waves',
    '이환': 'Neverness to Everness',
    'super raft boat classic': 'Salsa Now',
}


def game_title(title):
    """Extract the real game title independently of display aliases."""
    name = re.sub(r'^geforce\s*now\b\s*', '', title.strip(), flags=re.I)
    name = re.sub(r'^[-–—:]\s*', '', name)
    name = re.sub(r'\s+[-–—]\s+(?:GeForce\s*NOW|Chromium|Google Chrome|Mozilla Firefox)$', '', name, flags=re.I)
    name = name.strip()
    if name.casefold() in ('', 'chromium', 'google chrome', 'mozilla firefox'):
        return ''
    return name


def game_name(title):
    name = game_title(title)
    return LEGACY_GAME_NAMES.get(name.casefold(), name)


def is_gfn_window(window):
    if not window.address:
        return False
    if window.window_class.casefold() in ('geforcenow', 'geforce now', 'gfn'):
        return True
    title = window.title.strip()
    return bool(login.GFN_APP_CLASSES.search(window.window_class) and
                re.search(r'^geforce\s*now\b|[-–—]\s*geforce\s*now$', title, re.I))


def gfn_app_running(windows):
    if any(is_gfn_window(window) for window in windows):
        return True
    return sys.platform == 'win32' and windows_input.gfn_process_running()


def candidates(windows, profiles):
    result = {}
    for window in windows:
        if not login.GFN_APP_CLASSES.search(window.window_class):
            continue
        profile = login.detect_profile(profiles, window.title)
        # The original session predicate includes every Chrome/Electron page.
        # Unknown titles only qualify in an actual GFN app, avoiding mail etc.
        native = window.window_class.casefold() in ('geforcenow', 'geforce now', 'gfn')
        if game_name(window.title) and (profile or (native and login.is_gfn_session(window))):
            result[window.address] = (window, profile)
    return result


def session_key(window, profile):
    return window.address + ':' + ('rule:' + profile.title_re if profile else window.title)


class Sessions:
    def __init__(self, attempted=()):
        self.games = {}
        self.attempted = set(attempted)
        self.last_address = None

    def update(self, windows, profiles, blocked=None):
        observed = candidates(windows, profiles)
        for window, profile in observed.values():
            # Older releases keyed claims by display name. Preserve those
            # claims while allowing names to follow the observed game title.
            prefix = window.address + ':'
            if profile and any(claim.startswith(prefix)
                               and game_name(claim[len(prefix):]) == game_name(profile.name)
                               for claim in self.attempted):
                self.attempted.add(session_key(window, profile))
        live = {session_key(*game) for game in observed.values()}
        self.attempted.intersection_update(live)
        self.games = {address: game for address, game in observed.items() if not blocked or not blocked(*game)}

    def select(self, active):
        if active.address in self.games:
            self.last_address = active.address
            return self.games[active.address]
        if self.last_address in self.games:
            return self.games[self.last_address]
        if len(self.games) == 1:
            return next(iter(self.games.values()))
        return None

    def claim_first(self, game):
        if not game or not game[1]:
            return False
        key = session_key(*game)
        if key in self.attempted:
            return False
        self.attempted.add(key)
        return True
