#!/usr/bin/env python3
"""GFN login input automation for Hyprland/Wayland."""

from __future__ import annotations

import argparse
import configparser
import sys
if sys.platform != 'win32':
    import fcntl
import json
import ipaddress
import math
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


APP_ID = "gfn-autologin-linux"
BASE = Path(__file__).resolve().parent
CONFIG = BASE / "profiles.ini"
if sys.platform != "win32" and not CONFIG.is_file():
    legacy_config = Path.home() / ".local/share/gfn-autologin-linux/profiles.ini"
    CONFIG = legacy_config if legacy_config.is_file() else (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gfn-flow/profiles.ini")
SPLIT_CONFIG = BASE / "gfn-jp.conf"
SPLIT_CONNECTION = "gfn-jp"
SPLIT_SUBNET = "66.22.144.0/24"
LIBRARY_HOST = "apps.gxn.nvidia.com"
# Exact control hosts observed in the native client's launch and region logs.
# Japan currently aliases other metros; the old subnet is no longer sufficient.
CONTROL_HOSTS = ("prod.cloudmatchbeta.nvidiagrid.net",
                 "ap-japan.cloudmatchbeta.nvidiagrid.net",
                 "ap-india.cloudmatchbeta.nvidiagrid.net")
LIBRARY_ROUTE_KEY = "auto-macro.gfn-library-routes"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID
LOG_FILE = STATE_DIR / "connect.log"
MONITOR_LOCK = STATE_DIR / "vpn-monitor.lock"
VPN_RESULT_FILE = STATE_DIR.parent / "auto-macro/vpn-servers.json"
WIDGET_STATE = STATE_DIR / "widget.json"
WIDGET_LOCK = STATE_DIR / "widget.lock"
WIDGET_SCRIPT = BASE / "gfn_widget.py"
CALIBRATION_STATE = STATE_DIR / "calibration.json"
GFN_APP_CLASSES = re.compile(r"(geforce|gfn|nvidia|electron|chrome|chromium|firefox)", re.I)
VPN_BLOCKED_SSIDS = (re.compile(r"^CBE_", re.I),)
GFN_IDLE_TITLE = "GeForce NOW"
CALIBRATION_LABELS = {
    "스타레일": ("아이디 입력칸", "비밀번호 입력칸", "로그인 / 게임 시작 버튼"),
    "젠존제": ("이메일 입력칸", "비밀번호 입력칸", "로그인 / 게임 시작 버튼"),
    "명조": ("이메일 입력칸", "비밀번호 입력칸", "로그인 버튼", "약관 전체 동의", "수락 / 게임 시작 버튼"),
    "이환": ("이메일 로그인 선택", "비밀번호 입력칸 / 다음 버튼", "로그인 / 게임 시작 버튼"),
}


class LoginError(RuntimeError):
    pass


@dataclass
class Window:
    address: str
    title: str
    window_class: str
    x: int
    y: int
    width: int
    height: int


@dataclass
class Profile:
    name: str
    title_re: str
    user: str
    credential_key: str
    ref_width: int
    ref_height: int
    steps: list[str]


def run(
    args: list[str],
    *,
    input_data: str | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_data,
        text=True,
        capture_output=capture,
        check=check,
    )


def widget_running() -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with WIDGET_LOCK.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def show_widget(
    title: str,
    detail: str = "",
    *,
    tone: str = "info",
    expires: float = 0,
    countdown_until: float = 0,
    visible: bool = True,
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "title": title,
        "detail": detail,
        "tone": tone,
        "visible": visible,
        "expires_at": time.time() + min(expires, 3) if expires else 0,
        "dismiss_on_expiry": bool(expires),
        "game_title": globals().get("SESSION_GAME_TITLE", ""),
        "countdown_until": countdown_until,
    }
    temporary = WIDGET_STATE.with_name(f".{WIDGET_STATE.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, WIDGET_STATE)
    if visible and WIDGET_SCRIPT.exists() and not widget_running():
        try:
            subprocess.Popen(
                [sys.executable, str(WIDGET_SCRIPT)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass


def notify(message: str, *, error: bool = False, visible: bool = True) -> None:
    title = "GFN 오류" if error else "GFN 자동 로그인"
    detail = " · ".join(message.splitlines())
    show_widget(title, detail, tone="error" if error else "info", expires=5 if error else 3.5, visible=visible)
    print(message, file=sys.stderr if error else sys.stdout)


def log(message: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 256 * 1024:
        LOG_FILE.replace(LOG_FILE.with_suffix(".1.log"))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(f"[{stamp}] {message}\n")


def require_commands(*commands: str) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise LoginError("필요한 명령을 찾을 수 없습니다: " + ", ".join(missing))


def load_profiles() -> tuple[dict[str, str], list[Profile]]:
    if not CONFIG.exists():
        return {}, []

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    with CONFIG.open("r", encoding="utf-16") as stream:
        parser.read_file(stream)

    global_settings = dict(parser["global"])
    profiles: list[Profile] = []
    for section in parser.sections():
        if section == "global":
            continue
        values = parser[section]
        profiles.append(
            Profile(
                name=section,
                title_re=values.get("TitleRe", ""),
                user=values.get("User", ""),
                credential_key=values.get("CredKey", section),
                ref_width=int(values.get("RefWidth", "1920")),
                ref_height=int(values.get("RefHeight", "1080")),
                steps=[part.strip() for part in values.get("Steps", "").split("|") if part.strip()],
            )
        )
    return global_settings, profiles


def active_window() -> Window:
    require_commands("hyprctl")
    try:
        data = json.loads(run(["hyprctl", "activewindow", "-j"]).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise LoginError("Hyprland 활성 창 정보를 읽을 수 없습니다") from exc

    at = data.get("at", [0, 0])
    size = data.get("size", [0, 0])
    return Window(
        address=str(data.get("address", "")),
        title=str(data.get("title", "")),
        window_class=str(data.get("class", "")),
        x=int(at[0]),
        y=int(at[1]),
        width=int(size[0]),
        height=int(size[1]),
    )


def verify_window(original: Window) -> Window:
    current = active_window()
    if not current.address or current.address != original.address:
        raise LoginError("활성 창이 바뀌어 입력을 중단했습니다")
    if current.width < 640 or current.height < 360:
        raise LoginError("GFN 창이 너무 작아 입력을 중단했습니다")
    return current


def profile_matches_title(profile: Profile, title: str) -> bool:
    if not profile.title_re:
        return False
    try:
        # AHK allows a mid-pattern (?i); Python only permits it at the start.
        pattern = profile.title_re.replace("(?i)", "")
        return re.search(pattern, title, re.I) is not None
    except re.error:
        return False


def detect_profile(profiles: list[Profile], title: str) -> Profile | None:
    return next((profile for profile in profiles if profile_matches_title(profile, title)), None)


def is_gfn_idle(window: Window) -> bool:
    return bool(GFN_APP_CLASSES.search(window.window_class)) and window.title.strip() == GFN_IDLE_TITLE


def is_gfn_session(window: Window) -> bool:
    """Return whether a GFN window has transitioned from the idle title to a game."""
    title = window.title.strip()
    return (
        bool(GFN_APP_CLASSES.search(window.window_class))
        and bool(title)
        and title != GFN_IDLE_TITLE
    )


def all_windows() -> list[Window]:
    require_commands("hyprctl")
    try:
        items = json.loads(run(["hyprctl", "clients", "-j"]).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise LoginError("Hyprland 창 목록을 읽을 수 없습니다") from exc
    windows = []
    for item in items:
        at = item.get("at", [0, 0])
        size = item.get("size", [0, 0])
        windows.append(
            Window(
                address=str(item.get("address", "")),
                title=str(item.get("title", "")),
                window_class=str(item.get("class", "")),
                x=int(at[0]),
                y=int(at[1]),
                width=int(size[0]),
                height=int(size[1]),
            )
        )
    return windows


def choose_profile(profiles: list[Profile]) -> Profile | None:
    require_commands("fuzzel")
    choices = "\n".join(f"{index + 1}. {profile.name}" for index, profile in enumerate(profiles))
    result = run(
        ["fuzzel", "--dmenu", "--prompt", "GFN 로그인 > "],
        input_data=choices,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    match = re.match(r"\s*(\d+)\.", result.stdout)
    if not match:
        return None
    index = int(match.group(1)) - 1
    return profiles[index] if 0 <= index < len(profiles) else None


def get_secret(key: str) -> str:
    require_commands("secret-tool")
    result = run(
        ["secret-tool", "lookup", "application", APP_ID, "account", key],
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise LoginError(f"Linux 키링에 '{key}' 비밀번호가 없습니다. 먼저 자격 증명을 등록하세요")
    return result.stdout.rstrip("\n")


def store_secret(key: str, password: str) -> None:
    require_commands("secret-tool")
    result = run(
        [
            "secret-tool",
            "store",
            f"--label=GFN Auto Login - {key}",
            "application",
            APP_ID,
            "account",
            key,
        ],
        input_data=password,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "키링 저장 실패"
        raise LoginError(detail)


def delete_secret(key: str) -> None:
    require_commands("secret-tool")
    run(["secret-tool", "clear", "application", APP_ID, "account", key], check=False)


def credential_dialog(profiles: list[Profile]) -> None:
    require_commands("fuzzel", "zenity")
    keys = list(dict.fromkeys(profile.credential_key for profile in profiles))
    result = run(
        ["fuzzel", "--dmenu", "--prompt", "비밀번호 키 > "],
        input_data="\n".join(keys),
        check=False,
    )
    key = result.stdout.strip()
    if result.returncode != 0 or key not in keys:
        return
    password_result = run(
        ["zenity", "--password", "--title", f"GFN 비밀번호 등록: {key}"],
        check=False,
    )
    if password_result.returncode != 0:
        return
    password = password_result.stdout.rstrip("\n")
    if not password:
        raise LoginError("빈 비밀번호는 저장하지 않았습니다")
    store_secret(key, password)
    password = ""
    notify(f"'{key}' 비밀번호를 Linux 키링에 저장했습니다")


def current_ssid() -> str:
    if not shutil.which("nmcli"):
        return ""
    result = run(
        ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"],
        check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("yes:"):
            return line.split(":", 1)[1].replace(r"\:", ":").strip()
    return ""


def ssid_blocks_vpn(ssid: str) -> bool:
    return any(pattern.search(ssid) for pattern in VPN_BLOCKED_SSIDS)


def nordvpn_status() -> str:
    require_commands("nordvpn")
    result = run(["env", "LC_ALL=C", "nordvpn", "status"], check=False)
    return (result.stdout + "\n" + result.stderr).strip()


def nordvpn_connected(status: str) -> bool:
    return re.search(r"^Status:\s*Connected\s*$", status, re.I | re.M) is not None


def nordvpn_in_japan(status: str) -> bool:
    return nordvpn_connected(status) and re.search(r"^Country:\s*Japan\s*$", status, re.I | re.M) is not None


def rig_addresses() -> list[str]:
    if not shutil.which("ss"):
        return []
    result = run(["ss", "-Hnt", "state", "established"], check=False)
    return sorted(set(re.findall(r"\b66\.22\.\d{1,3}\.\d{1,3}\b", result.stdout)))


def split_connection_exists() -> bool:
    if not shutil.which("nmcli"):
        return False
    result = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], check=False)
    return any(
        line.rsplit(":", 1) == [SPLIT_CONNECTION, "wireguard"]
        for line in result.stdout.splitlines()
    )


def split_connection_active() -> bool:
    if not shutil.which("nmcli"):
        return False
    result = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"], check=False)
    return any(
        line.rsplit(":", 1) == [SPLIT_CONNECTION, "wireguard"]
        for line in result.stdout.splitlines()
    )


def route_device(address: str) -> str:
    if not shutil.which("ip"):
        return ""
    result = run(["ip", "route", "get", address], check=False)
    match = re.search(r"\bdev\s+(\S+)", result.stdout)
    return match.group(1) if match else ""


def validate_library_routes(routes: list[str]) -> list[str]:
    """Only bounded public IPv4 host routes; never a default/CDN subnet."""
    try:
        if not isinstance(routes, list) or not 0 < len(routes) <= 64:
            raise ValueError
        for route in routes:
            network = ipaddress.IPv4Network(route)
            if (not isinstance(route, str) or str(network) != route or network.prefixlen != 32
                    or not network.network_address.is_global or network.network_address.is_multicast):
                raise ValueError
        return sorted(set(routes), key=lambda r: int(ipaddress.IPv4Network(r).network_address))
    except (TypeError, ValueError):
        raise LoginError("Invalid GFN library host routes.") from None


def resolve_library_routes() -> list[str]:
    """Resolve catalog and launch controls before changing any saved/live route.

    Retain the existing function/metadata names for the installed server tester.
    These are public /32 destinations, never a broad CDN or streaming subnet.
    """
    routes = set()
    for host in (LIBRARY_HOST, *CONTROL_HOSTS):
        try:
            result = subprocess.run(["getent", "ahostsv4", host],
                                    capture_output=True, text=True, timeout=8, check=True)
            resolved = validate_library_routes(list({line.split()[0] + "/32"
                                                    for line in result.stdout.splitlines() if line.strip()}))
            routes.update(resolved)
        except (OSError, subprocess.SubprocessError, LoginError):
            raise LoginError(f"Could not resolve the GFN service {host}. Try connecting again.") from None
    return validate_library_routes(list(routes))


def library_routes_from_profile(remote) -> list[str]:
    import gi
    gi.require_version("NM", "1.0")
    from gi.repository import NM
    user = remote.get_setting(NM.SettingUser)
    value = user.get_data(LIBRARY_ROUTE_KEY) if user else None
    if value is None:
        return []  # Existing profiles need no migration or credential changes.
    try:
        if len(value) > 4096:
            raise ValueError
        return validate_library_routes(json.loads(value))
    except (TypeError, ValueError):
        raise LoginError("Invalid GFN library route metadata.") from None


def supported_split_routes(allowed: list[str], library: list[str]) -> bool:
    try:
        managed = validate_library_routes(library) if library else []
        return len(allowed) == len(set(allowed)) and set(allowed) == {SPLIT_SUBNET, *managed}
    except (LoginError, TypeError):
        return False


def refresh_library_routes(*, active: bool = False) -> list[str]:
    """Refresh catalog/control routes. Caller holds Macro Studio's VPN action lock.

    Metadata and routes are committed together. Reapply changes only allowed IPs
    on the live peer, preserving its endpoint even if a new server was saved.
    """
    routes = resolve_library_routes()  # DNS failure leaves the profile untouched.
    import gi
    gi.require_version("NM", "1.0")
    from gi.repository import NM
    client = NM.Client.new(None)
    remote = next((c for c in client.get_connections() if c.get_id() == SPLIT_CONNECTION), None)
    if remote is None:
        raise LoginError("The GFN split VPN profile is unavailable.")

    # CEF may still hold an earlier DNS answer. Keep the existing managed /32s
    # while connected; the next cold connection prunes them to fresh DNS answers.
    if active:
        routes = validate_library_routes(list(set(routes) | set(library_routes_from_profile(remote))))

    def checked_peer(connection, managed=None):
        setting = connection.get_setting(NM.SettingWireGuard)
        ip4, ip6 = connection.get_setting_ip4_config(), connection.get_setting_ip6_config()
        if (not setting or setting.get_peers_len() != 1 or not ip4 or not ip4.get_never_default()
                or ip6 and (not ip6.get_never_default() or ip6.get_method() != "disabled")):
            raise LoginError("A dedicated GFN IPv4 split VPN profile is required.")
        peer = setting.get_peer(0)
        allowed = [peer.get_allowed_ip(i) for i in range(peer.get_allowed_ips_len())]
        if not supported_split_routes(allowed, library_routes_from_profile(connection) if managed is None else managed):
            raise LoginError("The GFN VPN routes differ from the supported split profile.")
        endpoint = peer.get_endpoint().rsplit(":", 1)[0]
        if endpoint + "/32" in routes:
            raise LoginError("The library route overlaps the VPN server.")
        return setting, peer, allowed

    def update(connection, setting, peer, *, metadata=True):
        replacement = NM.WireGuardPeer.new_clone(peer, True)
        replacement.clear_allowed_ips()
        for route in [SPLIT_SUBNET, *routes]:
            replacement.append_allowed_ip(route, False)
        setting.set_peer(replacement, 0)
        if metadata:
            user = connection.get_setting(NM.SettingUser)
            if user is None:
                user = NM.SettingUser.new()
                connection.add_setting(user)
            user.set_data(LIBRARY_ROUTE_KEY, json.dumps(routes))

    setting, peer, allowed = checked_peer(remote)
    device = applied = original_applied = None
    if active:
        device = client.get_device_by_iface(SPLIT_CONNECTION)
        if device is None:
            raise LoginError("The active GFN VPN device is unavailable.")
        applied, version = device.get_applied_connection(0, None)
        # Saved routes may have been refreshed while the active tunnel kept its
        # previous DNS generation. Validate the active profile against its own
        # metadata; older helper versions reapplied without updating metadata,
        # so accept the saved generation only when it exactly matches the peer.
        live_managed = library_routes_from_profile(applied)
        live_setting = applied.get_setting(NM.SettingWireGuard)
        live_peer = live_setting.get_peer(0) if live_setting and live_setting.get_peers_len() == 1 else None
        live_allowed = [live_peer.get_allowed_ip(i) for i in range(live_peer.get_allowed_ips_len())] if live_peer else []
        if not supported_split_routes(live_allowed, live_managed):
            live_managed = library_routes_from_profile(remote)
        live_setting, live_peer, live_allowed = checked_peer(applied, live_managed)
        routes = validate_library_routes(list(set(routes) | set(live_managed)))
        original_applied = NM.SimpleConnection.new_clone(applied)
        if live_allowed != [SPLIT_SUBNET, *routes] or library_routes_from_profile(applied) != routes:
            update(applied, live_setting, live_peer)
        else:
            applied = None
    if allowed == [SPLIT_SUBNET, *routes] and applied is None:
        return routes

    # Merge secrets internally, never passing private keys to subprocess argv.
    remote.update_secrets("wireguard", remote.get_secrets("wireguard", None))
    original = NM.SimpleConnection.new_clone(remote)
    try:
        update(remote, setting, peer)
        if not remote.commit_changes(True, None):
            raise LoginError("Could not save GFN library VPN routes.")
        if applied is not None and not device.reapply(applied, version, 0, None):
            raise LoginError("Could not apply GFN library VPN routes.")
    except Exception:
        try:
            remote.replace_settings_from_connection(original)
            if not remote.commit_changes(True, None):
                raise LoginError("Could not restore the VPN profile.")
            if applied is not None:
                _, current_version = device.get_applied_connection(0, None)
                if not device.reapply(original_applied, current_version, 0, None):
                    raise LoginError("Could not restore the active VPN routes.")
        except Exception:
            raise LoginError("GFN library route update and rollback failed. Check the VPN profile.") from None
        raise LoginError("Could not update GFN library VPN routes; previous settings restored.") from None
    log(f"GFN catalog/control VPN routes updated: IPv4 hosts={len(routes)}")
    return routes


def validate_split_config() -> None:
    if not SPLIT_CONFIG.exists():
        raise LoginError(f"분할 WireGuard 설정이 없습니다: {SPLIT_CONFIG}")
    text = SPLIT_CONFIG.read_text(encoding="utf-8-sig")
    allowed = re.search(r"^\s*AllowedIPs\s*=\s*(.+?)\s*$", text, re.I | re.M)
    if not allowed or allowed.group(1) != "66.22.144.0/24":
        raise LoginError("WireGuard AllowedIPs가 66.22.144.0/24 분할 경로가 아닙니다")
    for required in ("PrivateKey", "PublicKey", "Endpoint", "Address"):
        if not re.search(rf"^\s*{required}\s*=\s*\S+", text, re.I | re.M):
            raise LoginError(f"WireGuard 설정에 {required} 항목이 없습니다")


def split_config_values() -> dict[str, str]:
    validate_split_config()
    values: dict[str, str] = {}
    for raw in SPLIT_CONFIG.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        values[key] = value
    return values


def setup_split_vpn() -> None:
    require_commands("nmcli")
    validate_split_config()
    if split_connection_exists():
        notify("NetworkManager에 gfn-jp 분할 연결이 이미 등록돼 있습니다")
        return
    imported = run(
        ["nmcli", "connection", "import", "type", "wireguard", "file", str(SPLIT_CONFIG)],
        check=False,
    )
    if imported.returncode != 0 and not split_connection_exists():
        # Some nmcli builds expose WireGuard as a native connection type but do not
        # route it through `connection import`. Build the same profile explicitly.
        values = split_config_values()
        peer = (
            f"{values['PublicKey']} "
            f"endpoint={values['Endpoint']} "
            f"allowed-ips={values['AllowedIPs']} "
            f"persistent-keepalive={values.get('PersistentKeepalive', '25')}"
        )
        result = run(
            [
                "nmcli", "connection", "add",
                "type", "wireguard",
                "ifname", SPLIT_CONNECTION,
                "con-name", SPLIT_CONNECTION,
                "connection.autoconnect", "no",
                "ipv4.method", "manual",
                "ipv4.addresses", values["Address"],
                "ipv4.never-default", "yes",
                "ipv6.method", "disabled",
                "ipv6.never-default", "yes",
                "wireguard.private-key", values["PrivateKey"],
                "wireguard.mtu", values.get("MTU", "1420"),
                "wireguard.peer-routes", "yes",
                "wireguard.peers", peer,
            ],
            check=False,
        )
        values["PrivateKey"] = ""
        if result.returncode != 0:
            first_error = (imported.stderr or imported.stdout).strip()
            second_error = (result.stderr or result.stdout).strip()
            raise LoginError(second_error or first_error or "WireGuard 프로필 생성 실패")
    if not split_connection_exists():
        raise LoginError("WireGuard 프로필을 가져왔지만 gfn-jp 연결을 찾지 못했습니다")
    modify = run(
        [
            "nmcli", "connection", "modify", SPLIT_CONNECTION,
            "connection.autoconnect", "no",
            "ipv4.never-default", "yes",
            "ipv6.never-default", "yes",
        ],
        check=False,
    )
    if modify.returncode != 0:
        raise LoginError((modify.stderr or modify.stdout).strip() or "분할 연결 안전 설정 실패")
    notify("gfn-jp 분할 WireGuard 연결을 NetworkManager에 등록했습니다")
    log("NetworkManager split WireGuard profile installed")


def choose_vpn_backend() -> str:
    if SPLIT_CONFIG.exists():
        validate_split_config()
        if not split_connection_exists():
            raise LoginError("먼저 터미널에서 'gfn-login setup-split-vpn'을 한 번 실행하세요")
        return "split"
    return "nordvpn"


def vpn_monitor_running() -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with MONITOR_LOCK.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def start_vpn_monitor(window: Window, profiles: list[Profile], backend: str) -> None:
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "vpn-monitor",
            "--window-address",
            window.address,
            "--window-class",
            window.window_class,
            "--vpn-backend",
            backend,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log(f"VPN monitor started: backend={backend} address={window.address} class={window.window_class}")


def connect_japan_vpn(window: Window | None, profiles: list[Profile], *, force: bool = False, monitor: bool = True) -> None:
    require_commands("nmcli")
    backend = choose_vpn_backend()
    if backend == "split":
        already_active = split_connection_active()
        library_routes = refresh_library_routes(active=already_active)
        if already_active:
            gfn_route = route_device("66.22.144.44")
            normal_route = route_device("1.1.1.1")
            if (gfn_route != SPLIT_CONNECTION or normal_route == SPLIT_CONNECTION
                    or any(route_device(route.split("/")[0]) != SPLIT_CONNECTION for route in library_routes)):
                raise LoginError(
                    "기존 gfn-jp 연결의 분할 경로가 올바르지 않습니다: "
                    f"GFN={gfn_route or '?'}, 일반={normal_route or '?'}"
                )
            if not monitor:
                return
            if vpn_monitor_running():
                show_widget("GFN 분할 VPN 활성", "게임을 시작하세요 · VPN은 수동으로 해제하세요", tone="ok", visible=False)
                log("Split VPN already connected; monitor already running")
                return
            show_widget("GFN 분할 VPN 활성", "세션 감시를 연결합니다 · VPN은 수동으로 해제하세요", tone="ok", visible=False)
            log("Split VPN already connected; monitor attached")
            start_vpn_monitor(window, profiles, backend)
            return
    else:
        require_commands("nordvpn")
        status = nordvpn_status()
        if nordvpn_connected(status):
            if not monitor and not nordvpn_in_japan(status):
                raise LoginError("Connect NordVPN to Japan before restarting GeForce NOW.")
            notify("NordVPN이 이미 연결돼 있습니다. 기존 연결은 자동으로 변경하거나 해제하지 않습니다", visible=False)
            log("NordVPN already connected; left untouched")
            return

    ssid = current_ssid()
    if ssid and ssid_blocks_vpn(ssid) and not force:
        raise LoginError(
            f"{ssid}는 기존 설정상 VPN 차단망입니다. Linux SSH 대체 경로는 아직 없으므로 "
            "Ctrl+Alt+V로 NordVPN 연결을 강제할 수 있습니다"
        )

    if backend == "split":
        show_widget("GFN 일본 우회", "분할 VPN 연결 중…", tone="info", visible=False)
        result = run(["nmcli", "connection", "up", SPLIT_CONNECTION], check=False)
        if result.returncode != 0 or not split_connection_active():
            detail = (result.stderr or result.stdout).strip()
            log(f"Split VPN connect failed: {detail}")
            raise LoginError(detail or "gfn-jp 분할 VPN 연결에 실패했습니다")
        gfn_route = route_device("66.22.144.44")
        normal_route = route_device("1.1.1.1")
        if (gfn_route != SPLIT_CONNECTION or normal_route == SPLIT_CONNECTION
                or any(route_device(route.split("/")[0]) != SPLIT_CONNECTION for route in library_routes)):
            run(["nmcli", "connection", "down", SPLIT_CONNECTION], check=False)
            log(f"Split route verification failed: gfn={gfn_route}; normal={normal_route}")
            raise LoginError(
                "분할 라우팅 검증에 실패해 연결을 되돌렸습니다: "
                f"GFN={gfn_route or '?'}, 일반={normal_route or '?'}"
            )
    else:
        show_widget("GFN 일본 우회", "NordVPN 일본 서버 연결 중…", tone="info", visible=False)
        result = run(["env", "LC_ALL=C", "nordvpn", "connect", "Japan"], check=False)
        new_status = nordvpn_status()
        if (result.returncode != 0 or not nordvpn_connected(new_status)
                or not monitor and not nordvpn_in_japan(new_status)):
            detail = (result.stderr or result.stdout or new_status).strip()
            log(f"NordVPN connect failed: {detail}")
            raise LoginError(detail or "NordVPN 일본 연결에 실패했습니다")

    log(f"VPN connected: backend={backend}; ssid={ssid or '(unknown)'}")
    route_note = "GFN 제어·라이브러리 일본 경유" if backend == "split" else "일본 풀터널"
    show_widget("GFN VPN 연결됨", f"{route_note} · VPN은 수동으로 해제하세요", tone="ok", visible=False)
    if monitor:
        start_vpn_monitor(window, profiles, backend)


def find_monitored_window(address: str, window_class: str) -> Window | None:
    windows = all_windows()
    exact = next((window for window in windows if address and window.address == address), None)
    if exact:
        return exact
    return next(
        (
            window
            for window in windows
            if window.window_class == window_class and GFN_APP_CLASSES.search(window.window_class)
        ),
        None,
    )


def direct_connection_is_fastest(report: dict) -> bool:
    """Only a complete, reliable direct measurement can enable auto-disconnect."""
    try:
        if not isinstance(report, dict):
            return False
        if (report['version'] != 2 or report['backend'] != 'split'
                or report['method'] != 'tcp-connect'):
            return False
        if not isinstance(report['target'], str):
            return False
        target, port = report['target'].rsplit(':', 1)
        if (port != '443' or not ipaddress.IPv4Address(target).is_global
                or (report['target'] != '66.22.144.44:443'
                    and report.get('target_host') != CONTROL_HOSTS[0])):
            return False
        direct = report['direct']
        if (not isinstance(direct, dict) or not isinstance(report['results'], list)
                or any(not isinstance(row, dict) for row in report['results'])):
            return False
        rows = [row for row in report['results'] if row.get('kind') == 'vpn']
        def valid(row):
            return (row['samples'] >= 8 and 0 <= row['failures'] <= row['samples']
                    and row['latency'] is not None and row['variation'] is not None
                    and all(isinstance(row[key], (int, float)) and math.isfinite(row[key]) and row[key] >= 0
                            for key in ('latency', 'variation')))
        if (not rows or direct.get('kind') != 'direct' or not valid(direct)
                or direct['failures'] != 0 or not any(valid(row) for row in rows)):
            return False
        for row in rows:
            if row['latency'] is None and row['failures'] == row['samples']:
                continue
            direct_rank = (0, direct['latency'] + direct['variation'], direct['latency'])
            vpn_rank = (row['failures'] / row['samples'], row['latency'] + row['variation'], row['latency']) if valid(row) else None
            if (vpn_rank is None or direct['latency'] >= row['latency'] or direct_rank >= vpn_rank):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def direct_disconnect_report() -> dict | None:
    try:
        report = json.loads(VPN_RESULT_FILE.read_text())
        if not direct_connection_is_fastest(report) or not 0 <= time.time() - report['tested_at'] <= 86400:
            return None
        direct = report['direct']
        current = subprocess.run(['ip', '-j', 'route', 'get', '1.1.1.1'],
                                 text=True, capture_output=True, timeout=2, check=True)
        if json.loads(current.stdout)[0].get('dev') != direct['interface']:
            return None
        result = subprocess.run(['ip', '-j', 'route', 'get', report['target'].rsplit(':', 1)[0], 'oif', direct['interface']],
                                text=True, capture_output=True, timeout=2, check=True)
        route = json.loads(result.stdout)[0]
        # Do not reuse a result after a network/address/gateway change.
        if any(route.get(key, '') != direct.get(key, '') for key in ('dev', 'gateway', 'prefsrc')):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def split_active_identity() -> str:
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE,UUID,ACTIVE-PATH',
                                 'connection', 'show', '--active'],
                                text=True, capture_output=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return ''
    prefix = SPLIT_CONNECTION + ':wireguard:'
    return next((line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)), '')


def vpn_text(english: str, korean: str) -> str:
    try:
        state = json.loads((STATE_DIR.parent / 'auto-macro/playback.json').read_text())
        return korean if state.get('language') == 'ko' else english
    except (OSError, ValueError):
        return english


def disconnect_if_direct_is_fastest(address: str, window_class: str, session_title: str) -> None:
    report = direct_disconnect_report()
    if not report:
        return
    identity = split_active_identity()
    if not identity:
        return
    deadline = time.monotonic() + 10
    show_widget('GFN VPN', vpn_text('Without VPN is faster · disconnecting in 10s',
                'VPN 미사용 연결이 더 빠릅니다 · 10초 후 해제합니다'), tone='info',
                countdown_until=time.time() + 10, visible=False)
    def cancelled():
        show_widget('GFN VPN', vpn_text('Automatic disconnect cancelled',
                    '자동 해제를 취소했습니다'), tone='ok', expires=3, visible=False)
    while True:
        window = find_monitored_window(address, window_class)
        current = direct_disconnect_report()
        if (not window or (address and window.address != address) or window.title.strip() != session_title or not current
                or current['tested_at'] != report['tested_at'] or split_active_identity() != identity):
            cancelled()
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(.5)
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'auto-macro'
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / 'vpn-toggle.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            cancelled()
            return
        window = find_monitored_window(address, window_class)
        current = direct_disconnect_report()
        if (not window or (address and window.address != address) or window.title.strip() != session_title or not current
                or current['tested_at'] != report['tested_at'] or split_active_identity() != identity):
            cancelled()
            return
        result = run(['nmcli', 'connection', 'down', SPLIT_CONNECTION], check=False)
        if result.returncode:
            show_widget('GFN VPN', vpn_text('Automatic VPN disconnect failed',
                        'VPN 자동 해제에 실패했습니다'), tone='error', expires=3, visible=False)
            log('Automatic split VPN disconnect failed')
            return
        show_widget('GFN VPN', vpn_text('Disconnected · without VPN was faster',
                    'VPN을 해제했습니다 · VPN 미사용 연결이 더 빨랐습니다'), tone='ok', expires=3, visible=False)
        log('Split VPN disconnected after 10s: verified direct connection was fastest')


def claim_vpn_monitor(lock, *, replace: bool = False) -> bool:
    """Replace only a verified, same-user GFN VPN monitor, never an arbitrary PID."""
    deadline = time.monotonic() + 3 if replace else 0
    signalled = None
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if not replace:
                return False
        try:
            lock.seek(0)
            pid = int(lock.read(32))
            process = Path("/proc") / str(pid)
            handle = os.pidfd_open(pid)
            try:
                arguments = (process / "cmdline").read_bytes().split(b"\0")
                script = str(Path(__file__).resolve()).encode()
                if (pid != os.getpid() and pid != signalled and process.stat().st_uid == os.getuid()
                        and len(arguments) >= 3 and arguments[1] == script
                        and arguments[2] in (b"vpn-monitor", b"vpn-watch")):
                    signal.pidfd_send_signal(handle, signal.SIGTERM)
                    signalled = pid
            finally:
                os.close(handle)
        except (OSError, ValueError):
            pass  # Owner may be exiting or still writing its PID.
        if time.monotonic() >= deadline:
            raise LoginError("Could not replace the GFN VPN session monitor.")
        time.sleep(.05)


def vpn_monitor(address: str, window_class: str, profiles: list[Profile], backend: str, *, restart: bool = False) -> None:
    global SESSION_GAME_TITLE
    SESSION_GAME_TITLE = ""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with MONITOR_LOCK.open("a+", encoding="utf-8") as lock:
        if not claim_vpn_monitor(lock, replace=restart):
            log("A VPN monitor is already running; duplicate exited")
            return
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()

        if restart:
            # Bind the new native window without focusing it. Slow launcher
            # creation is expected after Flatpak reports its process running.
            deadline = time.monotonic() + 15
            while True:
                connected = split_connection_active() if backend == "split" else nordvpn_connected(nordvpn_status())
                if not connected:
                    return  # Respect a manual disconnect; never reconnect here.
                window = find_monitored_window("", "GeForceNOW")
                if window:
                    address, window_class = window.address, window.window_class
                    break
                if time.monotonic() >= deadline:
                    raise LoginError("GeForce NOW opened but its VPN session monitor could not find the window.")
                time.sleep(.25)

        automatic = backend == 'split' and bool(direct_disconnect_report())
        show_widget(vpn_text('Waiting for GFN game', 'GFN 세션 대기 중'),
                    vpn_text('Game detection starts a 10s VPN disconnect countdown',
                             '게임을 감지하면 10초 후 VPN을 해제합니다') if automatic else
                    vpn_text('Start a game · VPN stays connected', '게임을 시작하세요 · VPN 연결을 유지합니다'), tone='ok', visible=False)
        while True:
            window = find_monitored_window(address, window_class)
            if window and is_gfn_session(window):
                session_title = window.title.strip()
                break
            if not window:
                log("VPN monitor stopped: GFN window closed; connection left unchanged")
                return
            connected = split_connection_active() if backend == "split" else nordvpn_connected(nordvpn_status())
            if not connected:
                log("VPN monitor stopped: VPN disconnected")
                return
            time.sleep(0.5)

        SESSION_GAME_TITLE = session_title
        log(f"Session title detected: {session_title}")
        if backend == 'split' and direct_disconnect_report():
            disconnect_if_direct_is_fastest(address, window_class, session_title)
            return
        show_widget(
            "GFN VPN 연결 유지",
            f"{session_title} · VPN은 수동으로 해제하세요",
            tone="ok",
            visible=False,
        )
        log(f"VPN kept connected after session detection; backend={backend}; no eligible faster direct result")


def disconnect_vpn() -> None:
    if split_connection_active():
        result = run(["nmcli", "connection", "down", SPLIT_CONNECTION], check=False)
        label = "GFN 분할 VPN"
    else:
        require_commands("nordvpn")
        result = run(["env", "LC_ALL=C", "nordvpn", "disconnect"], check=False)
        label = "NordVPN"
    if result.returncode != 0:
        raise LoginError((result.stderr or result.stdout).strip() or f"{label} 해제 실패")
    show_widget("GFN VPN 해제", f"{label} 연결을 해제했습니다", tone="ok", expires=4, visible=False)
    log(f"VPN manually disconnected: {label}")


def map_point(window: Window, profile: Profile, source_x: int, source_y: int) -> tuple[int, int]:
    scale = min(window.width / profile.ref_width, window.height / profile.ref_height)
    content_width = profile.ref_width * scale
    content_height = profile.ref_height * scale
    offset_x = window.x + (window.width - content_width) / 2
    offset_y = window.y + (window.height - content_height) / 2
    return round(offset_x + source_x * scale), round(offset_y + source_y * scale)


def cursor_position() -> tuple[int, int]:
    require_commands("hyprctl")
    try:
        cursor = json.loads(run(["hyprctl", "cursorpos", "-j"]).stdout)
        return int(cursor["x"]), int(cursor["y"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise LoginError("현재 마우스 좌표를 읽을 수 없습니다") from exc


def move_cursor(target_x: int, target_y: int) -> None:
    require_commands("hyprctl", "ydotool")
    # This host maps one ydotool relative unit to roughly two Hyprland logical
    # pixels. Send half the observed gap directly; feedback handles any small
    # acceleration error without the slow visible staircase used previously.
    trail: list[tuple[int, int]] = []
    for _attempt in range(10):
        current_x, current_y = cursor_position()
        trail.append((current_x, current_y))
        delta_x, delta_y = target_x - current_x, target_y - current_y
        if abs(delta_x) <= 2 and abs(delta_y) <= 2:
            return
        step_x = round(delta_x * 0.5)
        step_y = round(delta_y * 0.5)
        if abs(delta_x) > 2 and abs(step_x) < 4:
            step_x = 4 if delta_x > 0 else -4
        if abs(delta_y) > 2 and abs(step_y) < 4:
            step_y = 4 if delta_y > 0 else -4
        # `--` is required when either delta is negative; otherwise ydotool
        # parses values such as -366 as command-line options.
        run(["ydotool", "mousemove", "--", str(step_x), str(step_y)])
        time.sleep(0.015)
    current_x, current_y = cursor_position()
    if abs(target_x - current_x) > 3 or abs(target_y - current_y) > 3:
        recent = " → ".join(f"{x},{y}" for x, y in trail[-4:])
        raise LoginError(
            f"마우스 좌표 보정 실패: 목표={target_x},{target_y} "
            f"현재={current_x},{current_y} ({recent})"
        )


def click(window: Window, profile: Profile, source_x: int, source_y: int) -> None:
    require_commands("hyprctl", "ydotool")
    x, y = map_point(window, profile, source_x, source_y)
    move_cursor(x, y)
    run(["ydotool", "click", "0xC0"])


def type_text(text: str, delay_ms: int) -> None:
    require_commands("ydotool")
    # GFN captures raw keyboard input for the remote stream. wtype's Wayland
    # virtual-keyboard keycodes can be interpreted as a numeric sequence by
    # the remote client, while ydotool produces ordinary evdev/uinput events.
    args = ["ydotool", "type"]
    if delay_ms > 0:
        args.extend(["--key-delay", str(delay_ms)])
    args.extend(["--", text])
    run(args)


def send_key(key: str) -> None:
    require_commands("ydotool")
    codes = {
        "escape": 1,
        "esc": 1,
        "tab": 15,
        "enter": 28,
        "return": 28,
        "f2": 60,
        "delete": 111,
    }
    code = codes.get(key.lower())
    if code is None:
        raise LoginError(f"지원하지 않는 키 입력입니다: {key}")
    run(["ydotool", "key", "--key-delay", "10", f"{code}:1", f"{code}:0"])


def clear_field() -> None:
    require_commands("ydotool")
    # Raw Linux input codes: LeftCtrl+A, then Backspace. This also clears
    # Chromium/GFN autofill text without depending on Fcitx or key layout.
    run(
        [
            "ydotool", "key", "--key-delay", "10",
            "29:1", "30:1", "30:0", "29:0", "14:1", "14:0",
        ]
    )
    time.sleep(0.05)


def execute_profile(profile: Profile, settings: dict[str, str], *, full: bool) -> None:
    window = active_window()
    if not GFN_APP_CLASSES.search(window.window_class):
        raise LoginError(
            "활성 창이 GFN 앱으로 인식되지 않습니다: "
            f"{window.window_class or '(알 수 없음)'} (Ctrl+Alt+I로 창 정보를 확인하세요)"
        )
    if window.width <= 0 or window.height <= 0:
        raise LoginError("활성 창 크기를 확인할 수 없습니다")
    if not profile.user:
        raise LoginError(f"'{profile.name}' 아이디가 설정되지 않았습니다")

    password = get_secret(profile.credential_key)
    type_delay = max(int(settings.get("TypeDelay", "20")), 0)
    step_delay = max(int(settings.get("StepDelay", "15")), 0) / 1000

    notify(f"{profile.name} 로그인 입력을 시작합니다")
    field_cleared = False
    try:
        for step in profile.steps:
            log(f"Login step: profile={profile.name}; step={step}")
            if step.startswith("wait:"):
                wait_ms = int(step.split(":", 1)[1])
                if not full and wait_ms >= 2000:
                    break
                if wait_ms >= 2000:
                    # Login/server round trip before the game-start action.
                    wait_ms = 1000
                elif wait_ms >= 1000:
                    # Text submission before moving to the next control.
                    wait_ms = 500
                elif profile.name == "이환" and wait_ms in {300, 400}:
                    # NTE changes the visible login stage at these points.
                    wait_ms = 250
                else:
                    # Direct input focus and checkbox/button settling.
                    wait_ms = 100
                time.sleep(wait_ms / 1000)
                continue

            current = verify_window(window)
            if step == "id":
                if not field_cleared:
                    clear_field()
                type_text(profile.user, type_delay)
                field_cleared = False
            elif step == "pw":
                if not field_cleared:
                    clear_field()
                type_text(password, type_delay)
                field_cleared = False
            elif step == "clear":
                clear_field()
                field_cleared = True
            elif step in {"tab", "enter"}:
                send_key(step)
                field_cleared = False
            elif step == "shifttab":
                run(
                    [
                        "ydotool", "key", "--key-delay", "10",
                        "42:1", "15:1", "15:0", "42:0",
                    ]
                )
                field_cleared = False
            elif step.startswith("click:"):
                coords = step.split(":", 1)[1].split(",", 1)
                click(current, profile, int(coords[0]), int(coords[1]))
                field_cleared = False
            elif step.startswith("key:"):
                send_key(step.split(":", 1)[1].strip("{}"))
                field_cleared = False
            else:
                raise LoginError(f"지원하지 않는 단계입니다: {step}")
            time.sleep(step_delay)
    finally:
        password = ""
    suffix = "전체 단계 완료" if full else "로그인 버튼까지 입력 완료"
    notify(f"{profile.name}: {suffix}")


def inspect_window() -> None:
    window = active_window()
    message = (
        f"class={window.window_class or '(없음)'}\n"
        f"title={window.title or '(없음)'}\n"
        f"position={window.x},{window.y}\n"
        f"size={window.width}x{window.height}"
    )
    try:
        run(["wl-copy"], input_data=message, check=False)
    except OSError:
        pass
    notify(message)


def calibration_points(profile: Profile) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for step in profile.steps:
        if not step.startswith("click:"):
            continue
        raw_x, raw_y = step.split(":", 1)[1].split(",", 1)
        point = (int(raw_x), int(raw_y))
        if point not in points:
            points.append(point)
    return points


def save_calibration(
    profile: Profile,
    window: Window,
    old_points: list[tuple[int, int]],
    new_points: list[tuple[int, int]],
) -> None:
    mapping = dict(zip(old_points, new_points))
    rewritten_steps: list[str] = []
    for step in profile.steps:
        if not step.startswith("click:"):
            rewritten_steps.append(step)
            continue
        raw_x, raw_y = step.split(":", 1)[1].split(",", 1)
        point = mapping[(int(raw_x), int(raw_y))]
        rewritten_steps.append(f"click:{point[0]},{point[1]}")

    source = CONFIG.read_text(encoding="utf-16")
    section_pattern = re.compile(
        rf"(^\[{re.escape(profile.name)}\]\s*$)(.*?)(?=^\[|\Z)",
        re.M | re.S,
    )
    match = section_pattern.search(source)
    if match is None:
        raise LoginError(f"'{profile.name}' 프로필 설정 구역을 찾지 못했습니다")
    block = match.group(2)
    block = re.sub(r"^RefWidth=.*$", f"RefWidth={window.width}", block, count=1, flags=re.M)
    block = re.sub(r"^RefHeight=.*$", f"RefHeight={window.height}", block, count=1, flags=re.M)
    block = re.sub(r"^Steps=.*$", "Steps=" + "|".join(rewritten_steps), block, count=1, flags=re.M)
    updated = source[:match.start(2)] + block + source[match.end(2):]

    backup = CONFIG.with_suffix(CONFIG.suffix + ".bak")
    shutil.copy2(CONFIG, backup)
    temporary = CONFIG.with_name(f".{CONFIG.name}.{os.getpid()}.tmp")
    temporary.write_text(updated, encoding="utf-16")
    os.replace(temporary, CONFIG)


def write_calibration_state(state: dict[str, object]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CALIBRATION_STATE.with_name(f".{CALIBRATION_STATE.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, CALIBRATION_STATE)


def read_calibration_state() -> dict[str, object]:
    try:
        return json.loads(CALIBRATION_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LoginError("진행 중인 좌표 보정이 없습니다. 먼저 Ctrl+Alt+M을 누르세요") from exc


def calibration_prompt(profile_name: str, labels: tuple[str, ...], index: int, total: int) -> None:
    label = labels[index] if index < len(labels) else f"클릭 위치 {index + 1}"
    show_widget(
        f"{profile_name} 좌표 보정 {index + 1}/{total}",
        f"화면을 자유롭게 조작하세요 · {label}에 마우스를 올리고 Ctrl+Alt+Space",
        tone="info",
    )


def calibrate_profile(profiles: list[Profile]) -> None:
    require_commands("hyprctl")
    window = active_window()
    profile = detect_profile(profiles, window.title)
    if profile is None:
        raise LoginError("보정할 게임의 로그인 화면을 활성화한 뒤 Ctrl+Alt+M을 누르세요")
    if window.width < 640 or window.height < 360:
        raise LoginError("GFN 창 크기를 확인할 수 없어 보정을 중단했습니다")

    old_points = calibration_points(profile)
    labels = CALIBRATION_LABELS.get(profile.name, ())
    state: dict[str, object] = {
        "profile": profile.name,
        "window": {
            "address": window.address,
            "title": window.title,
            "class": window.window_class,
            "x": window.x,
            "y": window.y,
            "width": window.width,
            "height": window.height,
        },
        "old_points": old_points,
        "new_points": [],
    }
    write_calibration_state(state)
    calibration_prompt(profile.name, labels, 0, len(old_points))


def capture_calibration_point(profiles: list[Profile]) -> None:
    require_commands("hyprctl")
    state = read_calibration_state()
    profile_name = str(state.get("profile", ""))
    profile = next((item for item in profiles if item.name == profile_name), None)
    if profile is None:
        raise LoginError(f"'{profile_name}' 프로필을 찾을 수 없습니다")

    saved_window = dict(state.get("window", {}))
    current = active_window()
    if current.address != str(saved_window.get("address", "")):
        raise LoginError("보정을 시작한 GFN 창을 활성화한 상태에서 좌표를 기록하세요")
    expected_size = (int(saved_window.get("width", 0)), int(saved_window.get("height", 0)))
    if (current.width, current.height) != expected_size:
        raise LoginError("보정 중 GFN 창 크기가 바뀌었습니다. Ctrl+Alt+M으로 다시 시작하세요")

    global_x, global_y = cursor_position()

    local_x = global_x - int(saved_window.get("x", 0))
    local_y = global_y - int(saved_window.get("y", 0))
    if not (0 <= local_x < current.width and 0 <= local_y < current.height):
        raise LoginError("마우스를 GFN 창 안의 지정할 위치에 올려놓으세요")

    old_points = [tuple(map(int, point)) for point in state.get("old_points", [])]
    new_points = [tuple(map(int, point)) for point in state.get("new_points", [])]
    if len(new_points) >= len(old_points):
        raise LoginError("좌표 보정이 이미 완료됐습니다")
    new_points.append((local_x, local_y))
    state["new_points"] = new_points

    labels = CALIBRATION_LABELS.get(profile.name, ())
    if len(new_points) < len(old_points):
        write_calibration_state(state)
        calibration_prompt(profile.name, labels, len(new_points), len(old_points))
        return

    window = Window(
        address=str(saved_window.get("address", "")),
        title=str(saved_window.get("title", "")),
        window_class=str(saved_window.get("class", "")),
        x=int(saved_window.get("x", 0)),
        y=int(saved_window.get("y", 0)),
        width=int(saved_window.get("width", 0)),
        height=int(saved_window.get("height", 0)),
    )
    save_calibration(profile, window, old_points, new_points)
    CALIBRATION_STATE.unlink(missing_ok=True)
    summary = " · ".join(f"{point[0]},{point[1]}" for point in new_points)
    show_widget(f"{profile.name} 좌표 보정 완료", summary, tone="ok", expires=6)


def cancel_calibration() -> None:
    existed = CALIBRATION_STATE.exists()
    CALIBRATION_STATE.unlink(missing_ok=True)
    detail = "설정은 변경되지 않았습니다" if existed else "진행 중인 보정이 없었습니다"
    show_widget("좌표 보정 취소", detail, tone="warn", expires=4)


def diagnose(profiles: list[Profile]) -> int:
    commands = (
        "hyprctl", "ydotool", "secret-tool", "fuzzel", "zenity",
        "nmcli", "nordvpn", "ss", "ip",
    )
    failed = False
    for command in commands:
        status = "OK" if shutil.which(command) else "없음"
        print(f"{command}: {status}")
        failed |= status != "OK"
    print(f"프로필: {len(profiles)}개")
    for profile in profiles:
        result = run(
            ["secret-tool", "lookup", "application", APP_ID, "account", profile.credential_key],
            check=False,
        ) if shutil.which("secret-tool") else None
        state = "등록됨" if result and result.returncode == 0 and result.stdout else "미등록"
        print(f"- {profile.name}: 아이디={'설정됨' if profile.user else '미설정'}, 키={profile.credential_key}({state})")
    if SPLIT_CONFIG.exists():
        try:
            validate_split_config()
            config_state = "정상"
        except LoginError as exc:
            config_state = f"오류: {exc}"
    else:
        config_state = "없음"
    print(f"분할 WireGuard 설정: {config_state}")
    if shutil.which("nmcli"):
        registered = "등록됨" if split_connection_exists() else "미등록"
        active = ", 활성" if split_connection_active() else ""
        print(f"NetworkManager gfn-jp: {registered}{active}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GFN 로그인 자동 입력 (Hyprland/Wayland)")
    parser.add_argument(
        "command",
        choices=(
            "auto", "picker", "credentials", "inspect", "diagnose", "vpn", "vpn-prepare", "vpn-disconnect",
            "vpn-monitor", "vpn-watch", "setup-split-vpn", "calibrate", "calibrate-capture", "calibrate-cancel",
        ),
    )
    parser.add_argument("--full", action="store_true", help="로그인 뒤 게임 시작/약관 단계까지 실행")
    parser.add_argument("--force", action="store_true", help="차단 SSID 판정을 무시하고 VPN 연결")
    parser.add_argument("--window-address", default="", help=argparse.SUPPRESS)
    parser.add_argument("--window-class", default="", help=argparse.SUPPRESS)
    parser.add_argument("--vpn-backend", choices=("split", "nordvpn"), default="nordvpn", help=argparse.SUPPRESS)
    args = parser.parse_args()

    settings, profiles = load_profiles()
    if args.command == "inspect":
        inspect_window()
        return 0
    if args.command == "calibrate":
        calibrate_profile(profiles)
        return 0
    if args.command == "calibrate-capture":
        capture_calibration_point(profiles)
        return 0
    if args.command == "calibrate-cancel":
        cancel_calibration()
        return 0
    if args.command == "setup-split-vpn":
        setup_split_vpn()
        return 0
    if args.command == "vpn-watch":
        vpn_monitor("", "GeForceNOW", profiles, choose_vpn_backend(), restart=True)
        return 0
    if args.command == "vpn-monitor":
        vpn_monitor(args.window_address, args.window_class, profiles, args.vpn_backend)
        return 0
    if args.command == "vpn-disconnect":
        disconnect_vpn()
        return 0
    if args.command == "vpn-prepare":
        connect_japan_vpn(None, profiles, force=args.force, monitor=False)
        return 0
    if args.command == "vpn":
        connect_japan_vpn(active_window(), profiles, force=args.force)
        return 0
    if args.command == "credentials":
        credential_dialog(profiles)
        return 0
    if args.command == "diagnose":
        return diagnose(profiles)

    profile: Profile | None
    if args.command == "picker":
        profile = choose_profile(profiles)
    else:
        window = active_window()
        if is_gfn_idle(window):
            connect_japan_vpn(window, profiles, force=args.force)
            return 0
        profile = detect_profile(profiles, window.title)
        if profile is None:
            profile = choose_profile(profiles)
    if profile is None:
        return 0
    execute_profile(profile, settings, full=args.full)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LoginError as exc:
        notify(str(exc), error=True)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        notify(detail or f"명령 실행 실패: {' '.join(exc.cmd)}", error=True)
        raise SystemExit(1)
