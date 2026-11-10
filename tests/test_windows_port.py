import base64
from contextlib import nullcontext
import io
import json
from pathlib import Path
import sys
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


@unittest.skipUnless(sys.platform == 'win32', 'Win32 adapters')
class WindowsPortTests(unittest.TestCase):
    def test_evdev_chord_is_translated_to_win32_packets(self):
        import detection
        from windows_input import input_command
        with patch('windows_input.emit') as emit:
            input_command(['ydotool', 'key', '--key-delay', '0', '29:1', '111:1', '111:0', '29:0'])
        packets = [item.payload.keyboard for item in emit.call_args.args]
        self.assertEqual([(item.scan, item.flags) for item in packets],
                         [(29, 8), (0x53, 9), (0x53, 11), (29, 10)])

    def test_credentials_round_trip_without_plaintext_files(self):
        import detection
        from ctypes import wintypes as w
        from windows_input import advapi, api, get_secret, store_secret, target_name
        key = 'temporary-test-' + uuid.uuid4().hex
        try:
            store_secret(key, 'fixture only — 한글')
            self.assertEqual(get_secret(key), 'fixture only — 한글')
        finally:
            api(advapi, 'CredDeleteW', w.BOOL, w.LPCWSTR, w.DWORD, w.DWORD)(target_name(key), 1, 0)

    def test_file_lock_excludes_a_second_instance(self):
        from windows_host import file_lock
        with TemporaryDirectory() as directory:
            lock = Path(directory) / 'resident.lock'
            with file_lock(lock):
                with self.assertRaises(BlockingIOError), file_lock(lock):
                    self.fail('Duplicate daemon acquired a locked file')
            with file_lock(lock):
                pass

    def test_gfn_profile_produces_only_explicit_split_routes(self):
        import windows_vpn as vpn
        key = base64.b64encode(bytes(range(32))).decode()
        with TemporaryDirectory() as directory:
            base = Path(directory) / 'original.conf'
            derived = Path(directory) / 'derived.conf'
            base.write_text(f'[Interface]\nPrivateKey={key}\nAddress=10.5.0.2/32\nDNS=10.5.0.1\n'
                            f'[Peer]\nPublicKey={key}\nEndpoint=8.8.4.4:51820\nAllowedIPs=0.0.0.0/0\n',
                            encoding='utf-8')
            config = vpn.read_profile(base)
            with patch('windows_vpn.read_profile', return_value=config):
                vpn.tunnel_config(derived, ['66.22.144.0/24', '66.22.131.132/32'])
            generated = derived.read_text(encoding='utf-8')
            self.assertNotIn('DNS', generated)
            self.assertNotIn('0.0.0.0/0', generated)
            self.assertIn('AllowedIPs = 66.22.144.0/24, 66.22.131.132/32', generated)

    def test_restart_leaves_the_current_window_open_when_vpn_fails(self):
        import windows_tools as tools
        with TemporaryDirectory() as directory:
            with (
                patch.object(tools, 'RUNTIME', Path(directory)),
                patch.object(tools, 'GFN_EXE', SimpleNamespace(is_file=lambda: True)),
                patch.object(tools, 'macro_input_active', return_value=False),
                patch('tool_status.vpn_lock', side_effect=nullcontext),
                patch('windows_vpn.set_connected', side_effect=ValueError('VPN failed')),
                patch('windows_tools.all_windows') as windows,
                patch('windows_tools.subprocess.Popen') as launch,
            ):
                with self.assertRaisesRegex(ValueError, 'VPN failed'):
                    tools.restart_gfn()
                windows.assert_not_called()
                launch.assert_not_called()

    def test_broker_comparison_forwards_progress_and_saves_report(self):
        import detection
        import windows_vpn_broker as broker
        events = [{'type': 'progress', 'values': ['test', i, 4, 'test', []]} for i in range(1, 5)]
        report = {'results': [1, 2, 3, 4]}
        events.append({'type': 'done', 'report': report})
        stream = io.BytesIO(''.join(json.dumps(event) + '\n' for event in events).encode())
        seen = []
        with (
            patch.object(broker, 'connection', return_value=nullcontext(stream)) as connect,
            patch('macro_library.MacroLibrary.atomic_write') as save,
        ):
            self.assertEqual(broker.scan(lambda *row: seen.append(row[1]), threading.Event()), report)
        connect.assert_called_once_with(b'b')
        self.assertEqual(seen, [1, 2, 3, 4])
        self.assertEqual(save.call_args.args[1], report)

    def test_broker_cancellation_is_not_saved(self):
        import detection
        import windows_vpn_broker as broker
        from vpn_servers import SearchCancelled
        stream = io.BytesIO(b'{"type":"cancelled"}\n')
        with (
            patch.object(broker, 'connection', return_value=nullcontext(stream)),
            patch('macro_library.MacroLibrary.atomic_write') as save,
        ):
            with self.assertRaises(SearchCancelled):
                broker.scan(lambda *_: None, threading.Event())
        save.assert_not_called()

    def test_wireguard_commands_are_rejected_in_the_app(self):
        import windows_vpn as vpn
        with (
            patch.object(vpn, 'WIREGUARD', Path(sys.executable)),
            patch.object(vpn.subprocess, 'run') as command,
        ):
            with self.assertRaisesRegex(ValueError, 'VPN service is required'):
                vpn.wireguard('/installtunnelservice', vpn.PROBE)
        command.assert_not_called()

    def test_unavailable_broker_does_not_fall_back_to_admin(self):
        import windows_vpn as vpn
        with patch('windows_vpn_broker.command', side_effect=ValueError('service unavailable')) as command:
            with self.assertRaisesRegex(ValueError, 'service unavailable'):
                vpn.set_connected(True)
        command.assert_called_once_with(b'1')
