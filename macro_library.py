"""Private, versioned macro library. Legacy profiles are references, not copies."""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

from detection import login, game_name, game_title

DEFAULT_PATH = Path.home() / '.config/auto-macro/library.json'
if sys.platform == 'win32':
    from windows_host import CONFIG
    DEFAULT_PATH = CONFIG / 'library.json'
TYPES = (('login', 'Login Macros'), ('normal', 'Normal Macros'))
VERSION = 2
BUTTONS = ('Left', 'Right', 'Middle', 'Side', 'Extra', 'Forward', 'Back', 'Task')


def read_library(data):
    """Migrate stored types, never names, and validate before changing any file."""
    data = copy.deepcopy(data)
    if not isinstance(data, dict) or data.get('version') not in (1, VERSION):
        raise ValueError('Unsupported macro library version. Original file was preserved.')
    data.setdefault('trash', [])  # Early exports only required a macros list.
    if not all(isinstance(data.get(key), list) for key in ('macros', 'trash')):
        raise ValueError('A macro library needs macros and trash lists.')
    for macro in data['macros'] + data['trash']:
        if data['version'] == 1 and isinstance(macro, dict) and macro.get('kind') == 'sequence':
            macro['kind'] = 'normal'
        validate(macro)
    for key in ('games', 'blacklist'):
        validate_game_entries(data.get(key, []))
    forgotten = data.get('forgotten_game_rules', [])
    if not isinstance(forgotten, list) or any(not isinstance(rule, str) for rule in forgotten):
        raise ValueError('Forgotten game rules must be a list of strings.')
    data['version'] = VERSION
    return data


def validate_game_entries(games):
    if not isinstance(games, list):
        raise ValueError('Saved games must be a list.')
    for game in games:
        if (not isinstance(game, dict) or set(game) != {'name', 'title_re'}
                or not isinstance(game['name'], str) or not 1 <= len(game['name']) <= 100
                or not isinstance(game['title_re'], str) or not 1 <= len(game['title_re']) <= 300):
            raise ValueError('Saved games require a name and title pattern.')
        try:
            re.compile(game['title_re'].replace('(?i)', ''), re.I)
        except re.error:
            raise ValueError('Invalid saved game title pattern.') from None


def key_definitions():
    definitions = {}
    header = (Path(__file__).with_name('assets') / 'input-event-codes.h' if sys.platform == 'win32'
              else Path('/usr/include/linux/input-event-codes.h'))
    for line in header.read_text(encoding='utf-8').splitlines():
        match = re.match(r'#define\s+KEY_(\w+)\s+(0x[\da-fA-F]+|\d+)\b', line)
        if match:
            definitions[match[1]] = int(match[2], 0)
    return definitions


def key_codes(chord):
    aliases = {'CTRL': 'LEFTCTRL', 'CONTROL': 'LEFTCTRL', 'SHIFT': 'LEFTSHIFT',
               'ALT': 'LEFTALT', 'SUPER': 'LEFTMETA', 'WIN': 'LEFTMETA',
               'ESC': 'ESC', 'ESCAPE': 'ESC', 'RETURN': 'ENTER', 'DEL': 'DELETE'}
    definitions = key_definitions()
    result = []
    for part in chord.upper().replace('{', '').replace('}', '').split('+'):
        part = aliases.get(part.strip(), part.strip())
        if part not in definitions:
            raise ValueError(f'Unknown key: {part}. Use names such as Ctrl+A, Enter, Space.')
        result.append(definitions[part])
    if not result or len(result) > 5 or len(set(result)) != len(result):
        raise ValueError('Use one key or a chord of up to five distinct keys.')
    return result


def parse_step(step):
    if not isinstance(step, str) or not step:
        raise ValueError('Every action must have a value.')
    if step in ('id', 'pw', 'clear', 'tab', 'enter', 'shifttab'):
        return step, None
    action, separator, value = step.partition(':')
    if not separator:
        raise ValueError(f'Unknown action: {action}')
    if action == 'wait':
        milliseconds = int(value)
        if not 0 <= milliseconds <= 600000:
            raise ValueError('Wait must be between 0 and 600000 milliseconds.')
        return action, milliseconds
    if action in ('click', 'move'):
        x, y = map(int, value.split(','))
        if x < 0 or y < 0:
            raise ValueError('Click coordinates must be nonnegative.')
        return action, (x, y)
    if action == 'key':
        return action, key_codes(value)
    if action in ('down', 'up'):
        codes = key_codes(value)
        if len(codes) != 1:
            raise ValueError('Press or release one key at a time.')
        return action, codes[0]
    if action == 'button':
        name, state = value.split(',')
        if name not in BUTTONS or state not in ('0', '1'):
            raise ValueError('Choose a mouse button and press or release.')
        return action, (BUTTONS.index(name), int(state))
    if action == 'wheel':
        x, y = map(int, value.split(','))
        if not -120 <= x <= 120 or not -120 <= y <= 120 or (x, y) == (0, 0):
            raise ValueError('Scroll must use -120 to 120 ticks and a nonzero axis.')
        return action, (x, y)
    if action == 'text':
        if len(value) > 10000:
            raise ValueError('Text actions are limited to 10000 characters.')
        return action, value
    raise ValueError(f'Unknown action: {action}')


def validate(macro):
    if not isinstance(macro, dict):
        raise ValueError('Expected a macro object.')
    required = ('id', 'name', 'game', 'title_re', 'kind')
    if not all(isinstance(macro.get(k), str) and macro[k].strip() for k in required):
        raise ValueError('Name, game, title pattern, and macro type are required.')
    if len(macro['name']) > 100 or len(macro['game']) > 100 or len(macro['title_re']) > 300:
        raise ValueError('Name/game must fit 100 characters; title pattern 300.')
    if macro['kind'] not in ('login', 'normal'):
        raise ValueError('Macro type must be login or normal.')
    try:
        re.compile(macro['title_re'].replace('(?i)', ''), re.I)
    except re.error as exc:
        raise ValueError(f'Invalid game title pattern: {exc}') from None
    for field in ('hidden', 'auto', 'enabled'):
        if not isinstance(macro.get(field, False), bool):
            raise ValueError(f'{field} must be true or false.')
    for field in ('repeat', 'ref_width', 'ref_height', 'repeat_delay', 'step_delay'):
        if not isinstance(macro.get(field), int) or isinstance(macro[field], bool):
            raise ValueError(f'{field} must be an integer.')
    if not 1 <= macro['repeat'] <= 100 or not 1 <= macro['ref_width'] <= 10000 or not 1 <= macro['ref_height'] <= 10000:
        raise ValueError('Repeat must be 1–100; reference dimensions 1–10000.')
    if not 0 <= macro['repeat_delay'] <= 600000 or not 0 <= macro['step_delay'] <= 10000:
        raise ValueError('Repeat delay must be 0–600000ms; step delay 0–10000ms.')
    if not isinstance(macro.get('steps'), list) or not 1 <= len(macro['steps']) <= 1000:
        raise ValueError('Add between 1 and 1000 actions.')
    for step in macro['steps']:
        action, value = parse_step(step)
        # Type controls automatic execution, not the contents of a manual run.
        # A converted login keeps its credential actions without ever auto-running.
        if action in ('click', 'move') and (value[0] > macro['ref_width'] or value[1] > macro['ref_height']):
            raise ValueError('Click coordinates must fit the reference dimensions.')
    for field in ('source_profile', 'user', 'credential_key', 'account_key', 'description'):
        if not isinstance(macro.get(field, ''), str):
            raise ValueError(f'{field} must be text.')
    if 'password' in macro:
        raise ValueError('Passwords belong in the keyring, never in macro files.')
    if macro.get('auto') and (macro['kind'] != 'login' or macro['repeat'] != 1):
        raise ValueError('First-F9 auto login requires a login macro with one repeat.')
    limit = macro.get('login_limit', 0)
    if not isinstance(limit, int) or not 0 <= limit <= len(macro['steps']):
        raise ValueError('Login-only action count must fit the action sequence; 0 runs all actions.')
    return macro


def new_macro(kind='normal'):
    if kind == 'sequence':  # Compatibility for callers of the original factory.
        kind = 'normal'
    if kind not in dict(TYPES):
        raise ValueError('Macro type must be login or normal.')
    identity = uuid.uuid4().hex
    return dict(id=identity, name='New login' if kind == 'login' else 'New macro',
                game='', title_re='', kind=kind, description='', hidden=False,
                enabled=True, auto=False, repeat=1, repeat_delay=500, step_delay=15,
                ref_width=1920, ref_height=1080, source_profile='', user='',
                credential_key='auto-macro-' + identity, account_key='', login_limit=0,
                steps=['id', 'tab', 'pw', 'enter'] if kind == 'login' else ['wait:500'])


def store_login_secret(kind, value):
    key = f'auto-macro-{uuid.uuid4().hex}-{kind}'
    login.store_secret(key, value)
    return key


class MacroLibrary:
    def __init__(self, path=DEFAULT_PATH, profiles=()):
        self.source_profiles = list(profiles)
        self.path = Path(path)
        self.history_path = self.path.with_name('history.json')
        if self.path.exists():
            original = json.loads(self.path.read_text())
            self.data = read_library(original)
            if original['version'] != VERSION:
                backup = self.path.with_suffix('.v1.bak')
                if not backup.exists():
                    shutil.copy2(self.path, backup)
                    os.chmod(backup, 0o600)
                self.save()
        else:
            self.data = dict(version=VERSION, macros=[], trash=[])
            self.import_legacy(profiles)
        if self.update_game_names(profiles):
            self.save()
        try:
            self.history = json.loads(self.history_path.read_text())
        except (OSError, ValueError):
            self.history = []

    @property
    def macros(self):
        return self.data['macros']

    @property
    def games(self):
        return self.data.get('games', [])

    @property
    def blacklist(self):
        return self.data.get('blacklist', [])

    def game_is_blocked(self, name, pattern=None):
        name = name.casefold()
        return any(game['name'].casefold() == name or game['title_re'] == pattern
                   for game in self.blacklist)

    def window_is_blocked(self, window, profile):
        if self.game_is_blocked(game_name(window.title)):
            return True
        if profile:
            return self.game_is_blocked(game_name(profile.name), profile.title_re)
        return any(re.search(game['title_re'].replace('(?i)', ''), window.title, re.I) for game in self.blacklist)

    def move_game(self, name, *, from_blacklist, restore=False):
        source_key = 'blacklist' if from_blacklist else 'games'
        source = self.data.get(source_key, [])
        name = name.casefold()
        moved = [game for game in source if game['name'].casefold() == name]
        if not moved:
            raise ValueError('Choose a game first.')
        previous = self.data
        self.data = dict(previous)
        self.data[source_key] = [game for game in source if game['name'].casefold() != name]
        if from_blacklist and restore:
            rules = {game['title_re'] for game in self.games}
            self.data['games'] = self.games + [game for game in moved if game['title_re'] not in rules]
        elif from_blacklist:
            # Loading presets at startup must not undo a removal without a restore.
            rules = self.data.get('forgotten_game_rules', [])
            self.data['forgotten_game_rules'] = list(dict.fromkeys(rules + [game['title_re'] for game in moved]))
        else:
            self.data['blacklist'] = self.blacklist + moved
        try:
            self.save()
        except OSError:
            self.data = previous
            raise

    def remember_game(self, name, pattern):
        name = game_name(name)
        if not name or len(name) > 100 or not pattern or len(pattern) > 300 or self.game_is_blocked(name, pattern):
            return False
        entry = next((game for game in self.games if game['title_re'] == pattern), None)
        changed = not entry or entry['name'] != name
        forgotten = self.data.get('forgotten_game_rules', [])
        if pattern in forgotten:
            self.data['forgotten_game_rules'] = [rule for rule in forgotten if rule != pattern]
            changed = True
        if not entry:
            self.data.setdefault('games', []).append(dict(name=name, title_re=pattern))
        elif changed:
            entry['name'] = name
        for macro in self.macros + self.data['trash']:
            if macro['title_re'] == pattern and macro['game'] != name:
                old = macro['game']
                macro['game'] = name
                if macro['name'] == old + ' login':
                    macro['name'] = (name + ' login')[:100]
                changed = True
        return changed

    def update_game_names(self, profiles=()):
        """Migrate display names without touching legacy source/credential identities."""
        changed = False
        pairs = [(game['name'], game['title_re']) for game in self.games]
        pairs += [(profile.name, profile.title_re) for profile in profiles]
        pairs += [(macro['game'], macro['title_re']) for macro in self.macros + self.data['trash']]
        for name, pattern in pairs:
            # A name observed in the actual title takes precedence over a preset.
            entry = next((game for game in self.games if game['title_re'] == pattern), None)
            if not entry and pattern in self.data.get('forgotten_game_rules', []):
                continue
            changed = self.remember_game(entry['name'] if entry else name, pattern) or changed
        return changed

    def remember_detected(self, games):
        changed = False
        for window, profile in games:
            if self.window_is_blocked(window, profile):
                continue
            name = game_name(window.title)
            branded = (re.match(r'^geforce\s*now\b', window.title.strip(), re.I)
                       or re.search(r'[-–—]\s*geforce\s*now$', window.title.strip(), re.I))
            if profile and not branded and window.window_class.casefold() not in ('geforcenow', 'geforce now', 'gfn'):
                # The legacy matcher also recognizes unbranded browser titles.
                # Do not replace a preset name with a browser page's heading.
                name = game_name(profile.name)
            pattern = profile.title_re if profile else self.game_pattern(window.title)
            changed = self.remember_game(name, pattern) or changed
        if changed:
            self.save()
        return changed

    @staticmethod
    def game_pattern(title):
        # Use the real title, not a display alias, to keep Salsa Now detectable.
        name = game_title(title)
        return (r'^(?:GeForce\s*NOW\s*(?:[-–—:]\s*)?)?' + re.escape(name)
                + r'(?:\s+[-–—]\s+(?:GeForce\s*NOW|Chromium|Google Chrome|Mozilla Firefox))?$')

    def game_options(self, profiles=()):
        options = {}
        forgotten = set(self.data.get('forgotten_game_rules', []))
        for name, pattern in ([(game['name'], game['title_re']) for game in self.games]
                              + [(game_name(p.name), p.title_re) for p in profiles]
                              + [(m['game'], m['title_re']) for m in self.macros]):
            if name and pattern and pattern not in forgotten and not self.game_is_blocked(game_name(name), pattern):
                options.setdefault(pattern, name)
        return [(name, pattern) for pattern, name in options.items()]

    @staticmethod
    def atomic_write(path, data):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
        with open(temporary, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        os.chmod(path, 0o600)

    def save(self):
        if self.path.exists():
            backup = self.path.with_suffix('.json.bak')
            shutil.copy2(self.path, backup)
            os.chmod(backup, 0o600)
        self.atomic_write(self.path, self.data)

    def import_legacy(self, profiles):
        known = {m['id'] for m in self.macros + self.data['trash']}
        count = 0
        for profile in profiles:
            identity = 'legacy-' + hashlib.sha256(profile.name.encode()).hexdigest()[:16]
            if identity in known:
                continue
            macro = new_macro('login')
            macro.update(id=identity, name=profile.name + ' login', game=profile.name,
                         title_re=profile.title_re, source_profile=profile.name,
                         credential_key=profile.credential_key, steps=profile.steps[:],
                         ref_width=profile.ref_width, ref_height=profile.ref_height, auto=True,
                         description='Imported login. Account and actions follow the original profile until edited.')
            macro['login_limit'] = next((i for i, step in enumerate(profile.steps) if step.startswith('wait:') and int(step.split(':', 1)[1]) >= 2000), 0)
            validate(macro)
            self.macros.append(macro)
            count += 1
        self.save()
        return count

    def get(self, identity):
        return next((m for m in self.macros if m['id'] == identity), None)

    def put(self, macro):
        macro = copy.deepcopy(validate(macro))
        macro['game'] = game_name(macro['game']) or macro['game']
        if macro['auto']:
            # Only one auto-login choice for each named game.
            for other in self.macros:
                if (other['game'] == macro['game'] or other['title_re'] == macro['title_re']) and other['id'] != macro['id']:
                    other['auto'] = False
        old = self.get(macro['id'])
        if old:
            self.macros[self.macros.index(old)] = macro
        else:
            self.macros.append(macro)
        self.remember_game(macro['game'], macro['title_re'])
        self.update_game_names()
        self.save()
        return macro

    def hide(self, identity):
        macro = self.get(identity)
        macro['hidden'] = not macro['hidden']
        self.save()

    def delete(self, identity):
        macro = self.get(identity)
        self.macros.remove(macro)
        self.data['trash'].append(macro)
        self.save()

    def restore(self, identity):
        macro = next(m for m in self.data['trash'] if m['id'] == identity)
        self.data['trash'].remove(macro)
        macro = copy.deepcopy(macro)
        macro['auto'] = False
        self.put(macro)

    def duplicate(self, identity):
        macro = copy.deepcopy(self.get(identity))
        macro.update(id=uuid.uuid4().hex, name=macro['name'] + ' copy', auto=False, hidden=False)
        return self.put(macro)

    def matching(self, title, include_hidden=False):
        return [m for m in self.macros if m.get('enabled', True)
                and (include_hidden or not m['hidden'])
                and re.search(m['title_re'].replace('(?i)', ''), title, re.I)]

    def auto_for(self, title):
        return next((m for m in self.matching(title) if m['auto'] and m['kind'] == 'login'), None)

    def save_login_credentials(self, identity, profiles, account, password):
        macro = self.get(identity)
        if not macro or macro['kind'] != 'login':
            raise ValueError('Choose a login macro.')
        if not account:
            raise ValueError('Enter a login ID or email.')
        profile = self.resolved_profile(macro, profiles, with_account=False)
        if password:
            password_key = store_login_secret('password', password)
        else:
            password_key = profile.credential_key
            try:
                if not password_key or not login.get_secret(password_key):
                    raise login.LoginError()
            except (login.LoginError, KeyError):
                raise ValueError('Enter a password to register these credentials.') from None
        updated = dict(macro, user='', credential_key=password_key,
                       account_key=store_login_secret('account', account))
        index = self.macros.index(macro)
        self.macros[index] = updated
        try:
            self.save()
        except OSError:
            self.macros[index] = macro
            raise

    def resolved_profile(self, macro, profiles, with_account=True):
        source = next((p for p in profiles if p.name == macro['source_profile']), None)
        if macro['source_profile']:
            if not source:
                raise ValueError('Original profile is missing. Edit this macro to make it independent.')
            user = login.get_secret(macro['account_key']) if with_account and macro.get('account_key') else source.user
            return replace(source, title_re=macro['title_re'], user=user,
                           credential_key=macro.get('credential_key') or source.credential_key)
        user = login.get_secret(macro['account_key']) if with_account and macro.get('account_key') else macro['user']
        return login.Profile(macro['game'], macro['title_re'], user, macro['credential_key'],
                             macro['ref_width'], macro['ref_height'], macro['steps'][:])

    def detection_profiles(self, profiles):
        result = list(profiles)
        known = {(p.name, p.title_re) for p in result}
        for macro in self.macros:
            if (macro['game'], macro['title_re']) not in known:
                result.append(login.Profile(macro['game'], macro['title_re'], '', '',
                                            macro['ref_width'], macro['ref_height'], []))
                known.add((macro['game'], macro['title_re']))
        patterns = {profile.title_re for profile in result}
        for game in self.games:
            if game['title_re'] not in patterns:
                result.append(login.Profile(game['name'], game['title_re'], '', '', 1920, 1080, []))
                patterns.add(game['title_re'])
        return result

    def record(self, identity, name, status, duration, completed):
        self.history.insert(0, dict(id=identity, name=name, status=status, duration=round(duration, 2),
                                   completed=completed, at=time.time()))
        self.history = self.history[:100]
        self.atomic_write(self.history_path, self.history)

    def export_file(self, path):
        if Path(path).resolve() in (self.path.resolve(), self.history_path.resolve()):
            raise ValueError('Choose a separate export file; do not overwrite the active library.')
        macros = copy.deepcopy(self.macros)
        for macro in macros:
            if macro['source_profile']:
                source = self.resolved_profile(macro, self.source_profiles, with_account=False)
                macro.update(source_profile='', steps=source.steps[:], ref_width=source.ref_width,
                             ref_height=source.ref_height, credential_key=source.credential_key)
                macro['login_limit'] = next((i for i, step in enumerate(source.steps) if step.startswith('wait:') and int(step.split(':', 1)[1]) >= 2000), 0)
            # Export actions and keyring references, never account names.
            macro['user'] = ''
            macro['account_key'] = ''
        self.atomic_write(Path(path), dict(version=VERSION, macros=macros, trash=[], games=self.games))

    def import_file(self, path):
        data = read_library(json.loads(Path(path).read_text()))
        incoming = copy.deepcopy(data['macros'])
        for macro in incoming:
            validate(macro)
        for macro in incoming:
            macro.update(id=uuid.uuid4().hex, auto=False)
            self.macros.append(macro)
        for game in data.get('games', []):
            self.remember_game(game['name'], game['title_re'])
        self.update_game_names()
        self.save()
        return len(incoming)
