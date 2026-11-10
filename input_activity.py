"""Shared status guard for desktop actions that can disrupt macro input."""
import json
import os
from pathlib import Path
import sys


def owner_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    if type(pid) is not int or pid < 1:
        return None
    if sys.platform == 'win32':
        from windows_host import owner_identity as windows_owner
        return windows_owner(pid)
    try:
        path = Path(f'/proc/{pid}/stat')
        if path.stat().st_uid != os.getuid():
            return None
        fields = path.read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return dict(pid=pid, start=fields[19],
                    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError, ValueError, IndexError):
        return None


def macro_input_active():
    if sys.platform == 'win32':
        from windows_host import STATE
        path = STATE / 'playback.json'
    else:
        path = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'auto-macro/playback.json'
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or not any(state.get(key) is True for key in ('armed', 'recording', 'running')):
            return False
        owner = state.get('input_owner')
        return isinstance(owner, dict) and owner == owner_identity(owner.get('pid'))
    except (OSError, ValueError):
        return False
