"""Small, private app preferences, separate from macros and credentials."""
import json
import os
from pathlib import Path

DEFAULTS = dict(language='en', animations=True, size='standard', f9_scope='always',
                first_f9='login', reopen_after_macro=True)
SIZES = dict(compact=(360, 480, 520), standard=(430, 520, 600), large=(600, 600, 680))
F9_SCOPES = dict(always='Always', running='When GeForce NOW is running',
                 focused='When GeForce NOW is focused', game='When a GFN game is running')
FIRST_F9 = dict(login='Run login macro', full='Run full login macro', picker='Open macro picker')


class Preferences:
    def __init__(self, path):
        self.path = Path(path)
        self.data = DEFAULTS.copy()
        try:
            stored = json.loads(self.path.read_text())
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if self.valid(key, value):
                        self.data[key] = value
        except (OSError, ValueError):
            pass

    @staticmethod
    def valid(key, value):
        return ((key == 'language' and value in ('en', 'ko'))
                or (key in ('animations', 'reopen_after_macro') and isinstance(value, bool))
                or (key == 'f9_scope' and isinstance(value, str) and value in F9_SCOPES)
                or (key == 'first_f9' and isinstance(value, str) and value in FIRST_F9)
                or (key == 'size' and isinstance(value, str) and value in SIZES))

    def set(self, key, value):
        if not self.valid(key, value):
            raise ValueError('Invalid preference')
        updated = {**self.data, key: value}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name('.' + self.path.name + '.tmp')
        with open(temporary, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)
        self.data = updated

