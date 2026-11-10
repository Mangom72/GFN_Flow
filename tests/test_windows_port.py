import base64
from contextlib import nullcontext
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
                            f'[Peer]\nPublicKey={key}\nEndpoint=jp1.nordvpn.com:51820\nAllowedIPs=0.0.0.0/0\n',
                            encoding='utf-8')
            config = vpn.read_profile(base)
            with patch('windows_vpn.read_profile', return_value=config):
                vpn.tunnel_config(derived, ['66.22.144.0/24', '66.22.131.132/32'])
            parsed = vpn.read_profile(derived)
            self.assertNotIn('DNS', parsed['Interface'])
            self.assertEqual(parsed['Peer']['AllowedIPs'], '66.22.144.0/24, 66.22.131.132/32')

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

    def test_complete_comparison_uses_one_elevation_and_forwards_progress(self):
        import windows_vpn_scan as scan
        cancel = threading.Event()
        seen = []
        report = {'results': [1, 2, 3, 4, 5, 6]}
        def launch(executable, args):
            job = Path(args[-2])
            events = [{'type': 'progress', 'values': ['test', index, 6, 'test', []]}
                      for index in range(1, 7)]
            events.append({'type': 'done', 'report': report})
            (job / 'events.jsonl').write_text(
                ''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
            return nullcontext('test-handle')
        with (
            TemporaryDirectory() as directory,
            patch.object(scan, 'RUNTIME', Path(directory)),
            patch.object(scan, 'account_sid', return_value='test-sid'),
            patch.object(scan, 'elevated_process', side_effect=launch) as elevated,
            patch.object(scan, 'process_finished', return_value=True),
            patch.object(scan, 'process_exit_code', return_value=0),
        ):
            self.assertEqual(scan.scan(lambda *row: seen.append(row[1]), cancel), report)
            self.assertEqual(seen, [1, 2, 3, 4, 5, 6])
            elevated.assert_called_once()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancel_waits_for_worker_cleanup(self):
        import windows_vpn_scan as scan
        from vpn_servers import SearchCancelled
        cancel, job = threading.Event(), None
        def launch(executable, args):
            nonlocal job
            job = Path(args[-2])
            cancel.set()
            return nullcontext('test-handle')
        def completed(handle, milliseconds):
            self.assertTrue((job / 'cancel').exists())
            (job / 'events.jsonl').write_text('{"type":"cancelled"}\n', encoding='utf-8')
            return True
        with (
            TemporaryDirectory() as directory,
            patch.object(scan, 'RUNTIME', Path(directory)),
            patch.object(scan, 'account_sid', return_value='test-sid'),
            patch.object(scan, 'elevated_process', side_effect=launch) as elevated,
            patch.object(scan, 'process_finished', side_effect=completed),
            patch.object(scan, 'process_exit_code', return_value=0),
        ):
            with self.assertRaises(SearchCancelled):
                scan.scan(lambda *_: None, cancel)
            elevated.assert_called_once()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_approved_worker_never_elevates_individual_probe_commands(self):
        import windows_vpn as vpn
        with (
            patch.object(vpn, 'WIREGUARD', Path(sys.executable)),
            patch.object(vpn, 'is_elevated', return_value=True),
            patch.object(vpn, 'elevated_process') as elevated,
            patch.object(vpn.subprocess, 'run') as command,
        ):
            command.return_value.returncode = 0
            for _ in range(5):
                vpn.wireguard('/installtunnelservice', vpn.PROBE)
                vpn.wireguard('/uninstalltunnelservice', vpn.PROBE)
            self.assertEqual(command.call_count, 10)
            elevated.assert_not_called()
