"""Small, private app preferences, separate from macros and credentials."""
import json
import os
from pathlib import Path

DEFAULTS = dict(language='en', animations=True, size='standard')
SIZES = dict(compact=(360, 480, 520), standard=(430, 520, 600), large=(600, 600, 680))


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
                or (key == 'animations' and isinstance(value, bool))
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

