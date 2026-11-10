"""Client for the installed GFN Flow Windows VPN service."""
import ctypes as c
from ctypes import wintypes as w
import json
import msvcrt
import os
import struct
import threading
from contextlib import contextmanager

from windows_input import api, kernel
from windows_vpn_profiles import FRAME_LIMIT, LEGACY

PIPE = r'\\.\pipe\GFNFlow.Vpn.v1'
LIMIT = 256 * 1024


@contextmanager
def connection(op, payload=None):
    frame = op
    if payload is not None:
        data = json.dumps(payload).encode('ascii')
        if len(data) > FRAME_LIMIT:
            raise ValueError('The WireGuard file is too large.')
        frame += struct.pack('<I', len(data)) + data
    create_file = api(kernel, 'CreateFileW', w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                      c.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
    handle = create_file(PIPE, 0x100003, 0, None, 3, 0x110000, None)
    if handle == c.c_void_p(-1).value and c.get_last_error() == 231:
        if api(kernel, 'WaitNamedPipeW', w.BOOL, w.LPCWSTR, w.DWORD)(PIPE, 500):
            handle = create_file(PIPE, 0x100003, 0, None, 3, 0x110000, None)
    if handle == c.c_void_p(-1).value:
        raise ValueError(f'The GFN Flow VPN service is unavailable (Windows error {c.get_last_error()}).')
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except OSError:
        api(kernel, 'CloseHandle', w.BOOL, w.HANDLE)(handle)
        raise
    with os.fdopen(fd, 'r+b', buffering=0) as stream:
        if stream.write(frame) != len(frame):
            raise ValueError('The VPN service did not receive the request.')
        yield stream


def receive(stream):
    line = stream.readline(LIMIT)
    if not line.endswith(b'\n'):
        raise ValueError('The VPN service did not complete the request.')
    event = json.loads(line)
    if event.get('type') == 'error':
        raise ValueError(event['message'])
    return event


def command(op):
    with connection(op) as stream:
        event = receive(stream)
    return event.get('type') == 'done'


def ready():
    try:
        return command(b's')
    except (OSError, ValueError):
        return False


def profiles(op=b'p', payload=None):
    with connection(op, payload) as stream:
        event = receive(stream)
    if event.get('type') != 'done':
        raise ValueError('The VPN service did not complete the request.')
    return event


def benchmark_matches(report):
    catalog = profiles()
    return tuple(catalog['active']) == LEGACY and report.get('profile_revision') == catalog['benchmark_id']


def scan(progress, cancel):
    from input_activity import macro_input_active
    from macro_library import MacroLibrary
    from vpn_servers import REPORT, SearchCancelled
    if cancel.is_set():
        raise SearchCancelled()
    finished = threading.Event()

    def watch_cancel():
        while not finished.wait(.15):
            if cancel.is_set() or macro_input_active():
                try:
                    if command(b'c'):
                        return
                except (OSError, ValueError):
                    continue

    with connection(b'b') as stream:
        thread = threading.Thread(target=watch_cancel, daemon=True)
        thread.start()
        try:
            while True:
                event = receive(stream)
                if event.get('type') == 'progress':
                    progress(*event['values'])
                elif event.get('type') == 'done':
                    report = event['report']
                    MacroLibrary.atomic_write(REPORT, report)
                    return report
                elif event.get('type') == 'cancelled':
                    raise SearchCancelled()
                else:
                    raise ValueError('The VPN service returned an invalid result.')
        finally:
            finished.set()
