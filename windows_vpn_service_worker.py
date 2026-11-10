"""Fixed operations invoked by the GFN Flow Windows VPN service."""
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
if __name__ == '__main__':
    os.environ['APPDATA'] = str(DATA)
    os.environ['LOCALAPPDATA'] = str(DATA)

import windows_vpn as vpn
from vpn_servers import SearchCancelled, optimize
from windows_vpn_profiles import (COUNTRIES, FRAME_LIMIT, LEGACY, PROVIDERS,
                                  active_profile, atomic_text, parse_config, profile_path)

PROFILE_DIR = DATA / 'MacroStudioNative' / 'vpn'
REVISION = 'nord-jp.revision'


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


def send(kind, **values):
    print(json.dumps({'type': kind, **values}), flush=True)


def benchmark_id():
    try:
        return (PROFILE_DIR / REVISION).read_text(encoding='ascii')
    except FileNotFoundError:
        return None


def catalog():
    return {
        'active': active_profile(PROFILE_DIR),
        'benchmark_id': benchmark_id(),
        'profiles': [(provider, country) for provider in PROVIDERS for country in COUNTRIES
                     if profile_path(PROFILE_DIR, provider, country).is_file()],
    }


def change_profile(op, payload):
    fields = {'provider', 'country', 'config'} if op == 'i' else {'provider', 'country'}
    if not isinstance(payload, dict) or payload.keys() != fields:
        raise ValueError('Choose a VPN provider and country.')
    provider, country = payload['provider'], payload['country']
    path = profile_path(PROFILE_DIR, provider, country)
    current = active_profile(PROFILE_DIR)
    if op == 'i':
        if (provider, country) == current and vpn.service('query') not in (0, 1):
            raise ValueError('Disconnect the VPN before replacing the active profile.')
        config = parse_config(payload['config'])
        if (provider, country) == LEGACY:
            atomic_text(PROFILE_DIR / REVISION, uuid4().hex)
        vpn.save_profile(config, path)
    else:
        if not path.is_file():
            raise ValueError('Import a WireGuard profile in VPN settings first.')
        if (provider, country) != current:
            if vpn.service('query') not in (0, 1):
                raise ValueError('Disconnect the VPN before changing profiles.')
            atomic_text(PROFILE_DIR / 'selected.json', json.dumps([provider, country]))
    return catalog()


def run(op, payload=None):
    if vpn.account_sid() != 'S-1-5-18':
        raise ValueError('The VPN worker must run as its installed service.')
    vpn.SERVICE_WORKER = True
    if op == 'p':
        return catalog()
    if op in ('i', 'u'):
        return change_profile(op, payload)
    selection = active_profile(PROFILE_DIR)
    vpn.PROFILE = profile_path(PROFILE_DIR, *selection)
    if op == 'x':
        if vpn.service('query', vpn.PROBE):
            vpn.wireguard('/uninstalltunnelservice', vpn.PROBE)
            vpn.wait_state(vpn.PROBE, (0,))
        vpn.PROBE.unlink(missing_ok=True)
        return {}
    if op == '1':
        vpn.set_connected(True)
    elif op == '0':
        vpn.set_connected(False)
    elif op == 'b':
        if selection != LEGACY:
            raise ValueError('Server comparison is available for NordVPN Japan.')
        if vpn.service('query', vpn.PROBE):
            run('x')
        report = optimize([], lambda *values: send('progress', values=values),
                          Cancellation(DATA / 'cancel'), allow_in_game=True)
        report['profile_revision'] = benchmark_id()
        return {'report': report}
    else:
        raise ValueError('Unsupported VPN operation.')
    return {}


if __name__ == '__main__':
    try:
        operation = sys.argv[1]
        payload = json.loads(sys.stdin.buffer.read(FRAME_LIMIT + 1)) if operation in ('i', 'u') else None
        send('done', **run(operation, payload))
    except SearchCancelled:
        send('cancelled')
    except Exception as exc:
        send('error', message=str(exc) if type(exc) is ValueError else 'The VPN service could not complete the request.')
        sys.exit(1)
