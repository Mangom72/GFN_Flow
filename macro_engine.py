"""Cancellable, focus-pinned playback for authored and imported macros."""
from dataclasses import replace
import subprocess
import sys
import threading
from detection import login
from macro_library import parse_step, validate


class Cancelled(RuntimeError):
    pass


class MacroRunner:
    def __init__(self, library, verify, progress=lambda *_: None):
        self.library, self.verify, self.progress = library, verify, progress
        self.cancel = threading.Event()
        self.completed = 0
        self.held_keys = set()
        self.held_buttons = set()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('Playback cancelled.')
        return self.verify()

    def wait(self, milliseconds):
        # Check focus throughout long delays, rather than only after the wait.
        remaining = milliseconds / 1000
        while remaining > 0:
            self.check()
            chunk = min(remaining, .1)
            if self.cancel.wait(chunk):
                raise Cancelled('Playback cancelled.')
            remaining -= chunk

    def run(self, macro, profiles, settings, full=False, account_id=None):
        try:
            return self.execute(macro, profiles, settings, full, account_id)
        finally:
            self.release_inputs()

    def release_inputs(self):
        # Release our own held inputs even after focus loss or cancellation.
        failure = None
        try:
            if self.held_keys:
                login.run(['ydotool', 'key', '--key-delay', '0',
                           *[f'{code}:0' for code in sorted(self.held_keys)]])
        except Exception as exc:
            failure = exc
        finally:
            self.held_keys.clear()
            for button in sorted(self.held_buttons):
                try:
                    login.run(['ydotool', 'click', hex(0x80 + button)])
                except Exception as exc:
                    failure = failure or exc
            self.held_buttons.clear()
        if failure:
            raise failure

    def execute(self, macro, profiles, settings, full=False, account_id=None):
        validate(macro)
        steps = macro['steps']
        if macro['kind'] == 'login' and not full and macro.get('login_limit', 0):
            steps = steps[:macro['login_limit']]
        options = {'account_id': account_id} if account_id else {}
        profile = self.library.resolved_profile(macro, profiles,
                                                with_account=bool(macro['source_profile']) or 'id' in steps,
                                                **options)
        self.check()
        password = None
        if sys.platform == 'win32' and not macro['source_profile']:
            if 'id' in steps and not profile.user:
                raise login.LoginError('Save the game account in the editor first.')
            if 'pw' in steps:
                password = login.get_secret(profile.credential_key)
        if macro['source_profile']:
            return self.legacy(macro, profile, settings, full or macro['kind'] == 'normal')
        parsed = [parse_step(step) for step in steps]
        for iteration in range(macro['repeat']):
            cleared = False
            for index, (action, value) in enumerate(parsed):
                window = self.check()
                self.progress(index + 1, len(parsed), iteration + 1, macro['repeat'], action)
                if action == 'wait':
                    self.wait(value)
                elif action == 'click':
                    login.click(window, profile, *value)
                    cleared = False
                elif action == 'move':
                    login.move_cursor(*login.map_point(window, profile, *value))
                elif action in ('down', 'up'):
                    if action == 'down':
                        self.held_keys.add(value)
                    login.run(['ydotool', 'key', '--key-delay', '0', f'{value}:{int(action == "down")}'])
                    if action == 'up':
                        self.held_keys.discard(value)
                elif action == 'button':
                    button, pressed = value
                    if pressed:
                        self.held_buttons.add(button)
                    login.run(['ydotool', 'click', hex((0x40 if pressed else 0x80) + button)])
                    if not pressed:
                        self.held_buttons.discard(button)
                elif action == 'wheel':
                    login.run(['ydotool', 'mousemove', '--wheel', '--', str(value[0]), str(value[1])])
                elif action in ('id', 'pw'):
                    if not cleared:
                        login.clear_field()
                    self.check()
                    text = profile.user if action == 'id' else (
                        password if password is not None else login.get_secret(profile.credential_key))
                    if not text:
                        raise ValueError('Set an account and keyring credential in the editor.')
                    # Keep each input burst short enough for prompt cancellation.
                    for offset in range(0, len(text), 16):
                        self.check()
                        login.type_text(text[offset:offset + 16], int(settings.get('TypeDelay', '20')))
                    text = ''
                    cleared = False
                elif action == 'text':
                    for offset in range(0, len(value), 16):
                        self.check()
                        login.type_text(value[offset:offset + 16], int(settings.get('TypeDelay', '20')))
                    cleared = False
                elif action == 'clear':
                    login.clear_field()
                    cleared = True
                else:
                    if action != 'key':
                        from macro_library import key_codes
                        value = key_codes('Shift+Tab' if action == 'shifttab' else action)
                    self.keys(value)
                    cleared = False
                self.completed += 1
                self.wait(macro['step_delay'])
            self.release_inputs()
            if iteration + 1 < macro['repeat']:
                self.wait(macro['repeat_delay'])

    def keys(self, codes):
        events = [f'{code}:1' for code in codes] + [f'{code}:0' for code in reversed(codes)]
        # One command always releases every key before checking cancellation.
        login.run(['ydotool', 'key', '--key-delay', '10', *events])

    def legacy(self, macro, profile, settings, full):
        runner = self
        # The original worker stops before its first long wait in login-only
        # mode. Report only executed actions, without changing that worker.
        total = len(profile.steps) if full else next(
            (i for i, step in enumerate(profile.steps)
             if step.startswith('wait:') and int(step.split(':', 1)[1]) >= 2000), len(profile.steps))
        class Steps:
            def __iter__(self):
                for index, step in enumerate(profile.steps):
                    runner.check()
                    if index < total:
                        runner.progress(index + 1, total, iteration + 1, macro['repeat'], step.split(':', 1)[0])
                    yield step
                    if index < total:
                        runner.completed += 1
        original = login.active_window
        playback_thread = threading.get_ident()
        def guarded():
            # UI/session queries must not inherit the worker's pause/cancel
            # guard. In particular, opening workflow controls while a legacy
            # login is paused must not block GTK waiting for that same UI.
            if threading.get_ident() != playback_thread:
                return original()
            return runner.check()
        # verify() calls active_window itself; don't recurse through the guard.
        original_verify = self.verify
        def verified():
            login.active_window = original
            try:
                return original_verify()
            finally:
                login.active_window = guarded
        self.verify = verified
        login.active_window = guarded
        try:
            for iteration in range(macro['repeat']):
                login.execute_profile(replace(profile, steps=Steps()), settings, full=full)
                if iteration + 1 < macro['repeat']:
                    self.wait(macro['repeat_delay'])
        finally:
            login.active_window = original
            self.verify = original_verify
