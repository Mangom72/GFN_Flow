"""Profile metadata and strict WireGuard import for the Windows VPN service."""
import base64
import configparser
import ipaddress
import json
import os
import re
import socket

PROVIDERS = {
    'nord': ('NordVPN', 'https://support.nordvpn.com/hc/en-us/articles/51470867535633'),
    'proton': ('Proton VPN (Free / Plus)', 'https://protonvpn.com/support/wireguard-configurations'),
    'mullvad': ('Mullvad', 'https://mullvad.net/en/help/wireguard-app-windows'),
    'surfshark': ('Surfshark', 'https://support.surfshark.com/hc/en-us/articles/6586553044498'),
}
COUNTRIES = {
    'JP': 'Japan', 'KR': 'South Korea', 'SG': 'Singapore', 'HK': 'Hong Kong',
    'TW': 'Taiwan', 'US': 'United States', 'CA': 'Canada', 'GB': 'United Kingdom',
    'AU': 'Australia', 'AT': 'Austria', 'BE': 'Belgium', 'BR': 'Brazil',
    'CH': 'Switzerland', 'CL': 'Chile', 'CO': 'Colombia', 'CZ': 'Czechia',
    'DE': 'Germany', 'DK': 'Denmark', 'ES': 'Spain', 'FI': 'Finland',
    'FR': 'France', 'GR': 'Greece', 'HU': 'Hungary', 'ID': 'Indonesia',
    'IE': 'Ireland', 'IL': 'Israel', 'IN': 'India', 'IT': 'Italy',
    'MX': 'Mexico', 'MY': 'Malaysia', 'NL': 'Netherlands', 'NO': 'Norway',
    'NZ': 'New Zealand', 'PH': 'Philippines', 'PL': 'Poland', 'PT': 'Portugal',
    'RO': 'Romania', 'SE': 'Sweden', 'TH': 'Thailand', 'TR': 'Türkiye',
    'UA': 'Ukraine', 'VN': 'Vietnam', 'ZA': 'South Africa',
}
LEGACY = ('nord', 'JP')
FILE_LIMIT = 8192
FRAME_LIMIT = 32768
PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10',
)))
DNS_NAME = re.compile(r'(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z][a-zA-Z0-9-]{0,61}[a-zA-Z0-9]')
INVALID_CONFIG = 'Choose a valid single-server WireGuard .conf file with an IPv4 address.'


def profile_path(root, provider, country):
    if not isinstance(provider, str) or not isinstance(country, str) or provider not in PROVIDERS or country not in COUNTRIES:
        raise ValueError('Choose a VPN provider and country.')
    return root / ('gfn-jp.conf' if (provider, country) == LEGACY else f'profiles/{provider}-{country}.conf')


def active_profile(root):
    try:
        selection = json.loads((root / 'selected.json').read_text(encoding='utf-8'))
        if not isinstance(selection, list) or len(selection) != 2 or not profile_path(root, *selection).is_file():
            raise ValueError
        return tuple(selection)
    except (OSError, ValueError):
        return LEGACY


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def public_address(address):
    return address.is_global and not address.is_multicast and not address.is_reserved


def endpoint_ipv4(endpoint):
    host, port = endpoint.rsplit(':', 1)
    try:
        address = ipaddress.IPv4Address(host)
        candidates = [address]
    except ValueError:
        if not DNS_NAME.fullmatch(host):
            raise ValueError(INVALID_CONFIG) from None
        try:
            candidates = [ipaddress.IPv4Address(row[4][0]) for row in
                          socket.getaddrinfo(host + '.', int(port), socket.AF_INET, socket.SOCK_DGRAM)]
        except OSError:
            raise ValueError('Could not resolve the WireGuard server to a public IPv4 address.') from None
    if not candidates or not all(map(public_address, candidates)):
        raise ValueError('Could not resolve the WireGuard server to a public IPv4 address.')
    return f'{candidates[0]}:{port}'


def parse_config(text):
    """Rebuild a single peer from an allowlist. Never hand source text to WireGuard."""
    if not isinstance(text, str) or len(text) > FILE_LIMIT or len(text.encode('utf-8', 'surrogatepass')) > FILE_LIMIT:
        raise ValueError(INVALID_CONFIG)
    original = configparser.ConfigParser(interpolation=None, strict=True, empty_lines_in_values=False,
                                         delimiters=('=',), inline_comment_prefixes=('#', ';'))
    original.optionxform = str
    try:
        original.read_string(text.lstrip('\ufeff'))
        if set(original.sections()) != {'Interface', 'Peer'} or original.defaults():
            raise ValueError
        interface, peer = original['Interface'], original['Peer']
        if not set(interface) <= {'Address', 'PrivateKey', 'ListenPort', 'MTU', 'DNS'}:
            raise ValueError
        if not set(peer) <= {'PublicKey', 'PresharedKey', 'Endpoint', 'AllowedIPs', 'PersistentKeepalive'}:
            raise ValueError
        if any(not value.isascii() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
               for section in (interface, peer) for value in section.values()):
            raise ValueError
        addresses = [ipaddress.ip_interface(value.strip()) for value in interface['Address'].split(',')]
        ipv4 = [value.ip for value in addresses if value.version == 4]
        if len(ipv4) != 1 or not any(ipv4[0] in net for net in PRIVATE_NETWORKS):
            raise ValueError
        for value in (interface['PrivateKey'], peer['PublicKey'], peer.get('PresharedKey')):
            if value is not None and (len(value) != 44 or len(base64.b64decode(value, validate=True)) != 32):
                raise ValueError
        host, port = peer['Endpoint'].rsplit(':', 1)
        if not port.isdecimal() or len(port) > 5 or not 0 < int(port) < 65536:
            raise ValueError
        if not DNS_NAME.fullmatch(host):
            if not public_address(ipaddress.IPv4Address(host)):
                raise ValueError
        clean = configparser.ConfigParser(interpolation=None)
        clean.optionxform = str
        clean['Interface'] = {'PrivateKey': interface['PrivateKey'], 'Address': f'{ipv4[0]}/32'}
        clean['Peer'] = {'PublicKey': peer['PublicKey'], 'Endpoint': f'{host.lower()}:{int(port)}'}
        if 'PresharedKey' in peer:
            clean['Peer']['PresharedKey'] = peer['PresharedKey']
        for source, target, key, low, high in (
                (interface, clean['Interface'], 'MTU', 576, 9000),
                (peer, clean['Peer'], 'PersistentKeepalive', 0, 65535)):
            if key in source:
                value = source[key]
                if not value.isdecimal() or len(value) > 5 or not low <= int(value) <= high:
                    raise ValueError
                target[key] = str(int(value))
        return clean
    except (KeyError, ValueError, configparser.Error):
        raise ValueError(INVALID_CONFIG) from None
