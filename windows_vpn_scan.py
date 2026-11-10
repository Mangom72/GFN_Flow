"""One Windows administrator prompt for a complete server comparison."""
import json
from pathlib import Path
import re
import sys
import tempfile

# The worker is launched with -I, so unrelated user Python modules are ignored.
ROOT = Path(__file__).resolve().parent
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))

from windows_host import RUNTIME
from windows_vpn import account_sid, elevated_process, is_elevated, process_exit_code, process_finished


def events(path, offset):
    try:
        with path.open('rb') as stream:
            stream.seek(offset)
            for line in stream:
                if not line.endswith(b'\n'):
                    return
                offset += len(line)
                yield offset, json.loads(line)
    except FileNotFoundError:
        return


def scan(progress, cancel):
    from vpn_servers import SearchCancelled
    if cancel.is_set():
        raise SearchCancelled()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='vpn-scan-', dir=RUNTIME)).resolve()
    updates, cancel_file = job / 'events.jsonl', job / 'cancel'
    result, offset = None, 0
    try:
        args = ['-I', '-X', 'utf8', str(ROOT / 'windows_vpn_scan.py'), str(job), account_sid()]
        with elevated_process(sys.executable, args) as handle:
            while True:
                if cancel.is_set() and not cancel_file.exists():
                    cancel_file.touch()
                finished = process_finished(handle, 150)
                for offset, event in events(updates, offset):
                    if event['type'] == 'progress':
                        progress(*event['values'])
                    else:
                        result = event
                if finished:
                    code = process_exit_code(handle)
                    break
        if result and result['type'] == 'cancelled':
            raise SearchCancelled()
        if result and result['type'] == 'error':
            raise ValueError(result['message'])
        if code or not result or result['type'] != 'done':
            raise ValueError('The approved VPN comparison did not complete.')
        return result['report']
    finally:
        updates.unlink(missing_ok=True)
        cancel_file.unlink(missing_ok=True)
        job.rmdir()


class Cancellation:
    def __init__(self, path):
        self.path = path

    def is_set(self):
        return self.path.exists()

    def wait(self, seconds):
        import time
        deadline = time.monotonic() + seconds
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(.05, remaining))
        return True


def worker(job, sid):
    from vpn_servers import optimize, SearchCancelled
    if not is_elevated() or account_sid() != sid:
        return 2
    job = job.resolve(strict=True)
    if job.parent != RUNTIME.resolve() or not re.fullmatch(r'vpn-scan-[a-z0-9_]+', job.name):
        return 2
    with (job / 'events.jsonl').open('x', encoding='utf-8', buffering=1) as stream:
        def send(event):
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        try:
            # Server tests explicitly allow an open game, like the original.
            # The shared input guard and VPN lock still run in optimize().
            report = optimize([], lambda *values: send({'type': 'progress', 'values': values}),
                              Cancellation(job / 'cancel'), allow_in_game=True)
            send({'type': 'done', 'report': report})
        except SearchCancelled:
            send({'type': 'cancelled'})
        except ValueError as error:
            send({'type': 'error', 'message': str(error)})
            return 1
        except Exception:
            send({'type': 'error', 'message': 'VPN server test failed. Check the VPN connection.'})
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(worker(Path(sys.argv[1]), sys.argv[2]) if len(sys.argv) == 3 else 2)
