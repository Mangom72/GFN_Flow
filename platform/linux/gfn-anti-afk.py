#!/usr/bin/python3
"""Nudge GeForce NOW immediately and every three minutes until stopped."""
import datetime
import json
import os
import subprocess
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'gfn-flow'))
from input_activity import macro_input_active

INTERVAL = 180
os.environ.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "gfn-autologin-linux/anti-afk.json"

def write_state(**state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_name(f".anti-afk.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(dict(state, updated_at=time.time(), interval=INTERVAL)))
    temporary.replace(STATE_FILE)

def run(*args):
    output = subprocess.check_output(args, text=True, timeout=5).strip()
    if output.startswith("error:"):
        raise OSError(output)
    return output

def log(message):
    print(f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')} {message}", flush=True)

def macro_paused():
    if not macro_input_active():
        return False
    write_state(active=True, waiting=True, message="Paused during macro recording or playback", next_nudge=time.time() + INTERVAL)
    return True


def nudge():
    if macro_paused():
        return True
    clients = json.loads(run("hyprctl", "-j", "clients"))
    target = next((c for c in clients if c.get("class", "").lower() == "geforcenow" and c.get("mapped")), None)
    if target is None:
        write_state(active=False, message="GeForce NOW closed", next_nudge=0)
        log("GeForce NOW closed; stopping")
        return False
    previous = json.loads(run("hyprctl", "-j", "activewindow"))
    cursor = json.loads(run("hyprctl", "-j", "cursorpos"))
    x, y = target["at"]
    width, height = target["size"]
    if previous.get("address") != target["address"] or not (x + 20 < cursor["x"] < x + width - 20 and y + 20 < cursor["y"] < y + height - 20):
        write_state(active=True, waiting=True, message="Waiting for GFN to be focused with the pointer inside", next_nudge=time.time() + INTERVAL)
        log("GFN not focused or pointer outside; skipped without moving anything")
        return True
    if macro_paused():
        return True
    before = run("hyprctl", "cursorpos")
    run("ydotool", "mousemove", "-x", "8", "-y", "0")
    time.sleep(0.15)
    after = run("hyprctl", "cursorpos")
    run("ydotool", "mousemove", "-x", "-8", "-y", "0")
    log(f"GFN mouse nudge: {before} -> {after}; next in {INTERVAL}s")
    write_state(active=True, message="Mouse moves every 3 minutes while GFN is focused", next_nudge=time.time() + INTERVAL, last_nudge=time.time())
    return True

if __name__ == "__main__":
    log(f"Started; interval {INTERVAL}s")
    while True:
        try:
            if not nudge():
                break
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
            log(f"Nudge failed: {error}")
            write_state(active=True, message=str(error), error=True, next_nudge=time.time() + INTERVAL)
        time.sleep(INTERVAL)
