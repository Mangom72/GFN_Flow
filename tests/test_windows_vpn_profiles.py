import base64
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from windows_vpn_profiles import LEGACY, active_profile, endpoint_ipv4, parse_config, profile_path


PRIVATE = base64.b64encode(bytes(range(32))).decode()
PUBLIC = base64.b64encode(bytes(range(32, 64))).decode()


def profile(address='10.2.0.2/32, fd00::2/128', endpoint='vpn.example.com:51820', extra=''):
    return f"""# Downloaded profile
[Interface]
PrivateKey = {PRIVATE}
Address = {address}
DNS = 10.2.0.1
{extra}
[Peer]
PublicKey = {PUBLIC}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {endpoint}
PersistentKeepalive = 25
"""


class WireGuardProfileTests(unittest.TestCase):
    def test_accepts_single_peer_ipv4_and_dual_stack_downloads(self):
        for name, source, address in (
                ('Proton', profile(), '10.2.0.2/32'),
                ('Mullvad', profile('10.65.1.2/32, fc00:bbbb:bbbb:bb01::2/128', '8.8.4.4:51820'), '10.65.1.2/32'),
                ('Surfshark', profile('10.14.0.2/16', 'jp-tok.prod.surfshark.com:51820'), '10.14.0.2/32'),
                ('Nord', profile('10.5.0.2/32', 'jp123.nordvpn.com:51820'), '10.5.0.2/32')):
            with self.subTest(name=name):
                parsed = parse_config(source)
                self.assertEqual(dict(parsed['Interface']), {'Address': address, 'PrivateKey': PRIVATE})
                self.assertNotIn('AllowedIPs', parsed['Peer'])
                self.assertEqual(parsed['Peer']['PersistentKeepalive'], '25')

    def test_rejects_settings_that_cannot_be_given_to_wireguard(self):
        for source in (
                profile(extra='PostUp = arbitrary text'), profile(extra='Table = off'),
                profile() + '\n[Peer]\nPublicKey = duplicate',
                profile(address='192.0.2.42/32'), profile(address='::1/128'),
                profile(endpoint='127.0.0.1:51820'), profile(endpoint='224.0.0.1:51820'),
                profile(endpoint='vpn.example.com:65536'), profile(extra='MTU = 1400\n  extra'),
                profile().replace(PRIVATE, 'malformed'), profile() + '#' * 8192):
            with self.assertRaisesRegex(ValueError, 'single-server'):
                parse_config(source)

    def test_dns_answer_is_public_and_pinned_before_install(self):
        with patch('windows_vpn_profiles.socket.getaddrinfo') as resolve:
            resolve.return_value = [(0, 0, 0, '', ('8.8.4.4', 51820))]
            self.assertEqual(endpoint_ipv4('vpn.example.com:51820'), '8.8.4.4:51820')
            resolve.assert_called_once_with('vpn.example.com.', 51820, socket.AF_INET, socket.SOCK_DGRAM)
            resolve.return_value += [(0, 0, 0, '', ('192.168.1.1', 51820))]
            with self.assertRaisesRegex(ValueError, 'public IPv4'):
                endpoint_ipv4('vpn.example.com:51820')

    def test_path_uses_only_catalog_values_and_preserves_legacy_name(self):
        root = Path('vpn')
        self.assertEqual(profile_path(root, *LEGACY), root / 'gfn-jp.conf')
        self.assertEqual(profile_path(root, 'proton', 'JP'), root / 'profiles' / 'proton-JP.conf')
        for pair in (('proton', '../JP'), ('../proton', 'JP'), ('express', 'JP'), ([], 'JP')):
            with self.assertRaises(ValueError):
                profile_path(root, *pair)


@unittest.skipUnless(sys.platform == 'win32', 'Windows VPN service')
class ProfileServiceTests(unittest.TestCase):
    def test_import_does_not_switch_a_live_tunnel_and_selection_persists(self):
        from windows_vpn_service_worker import change_profile
        from windows_vpn import save_profile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_profile(parse_config(profile()), profile_path(root, *LEGACY))
            with (patch('windows_vpn_service_worker.PROFILE_DIR', root),
                  patch('windows_vpn_service_worker.vpn.service', return_value=4) as state):
                result = change_profile('i', {'provider': 'proton', 'country': 'JP', 'config': profile()})
                self.assertEqual(result['active'], LEGACY)
                self.assertIn(('proton', 'JP'), result['profiles'])
                self.assertNotIn(PRIVATE, str(result))
                proton = {'provider': 'proton', 'country': 'JP'}
                with self.assertRaisesRegex(ValueError, 'Disconnect'):
                    change_profile('u', proton)
                state.return_value = 0
                change_profile('u', proton)
                self.assertEqual(active_profile(root), ('proton', 'JP'))
                self.assertEqual(change_profile('u', {'provider': 'nord', 'country': 'JP'})['active'], LEGACY)
                with self.assertRaisesRegex(ValueError, 'Import'):
                    change_profile('u', {'provider': 'mullvad', 'country': 'JP'})
                self.assertEqual(active_profile(root), LEGACY)
