# GFN Flow 설치 가이드

[README](../README.md) · [English](INSTALL.en.md) · [최신 다운로드](https://github.com/Mangom72/GFN_Flow/releases/latest)

최신 릴리스에서 ZIP과 SHA-256 파일을 받으십시오. Windows와 Linux 설치 스크립트가 같은 ZIP에 들어 있습니다. Windows 배포는 단독 실행 파일이 아니므로 MSYS2 런타임이 필요합니다.

## Windows x64

1. [MSYS2](https://www.msys2.org/)를 설치하고 초기 업데이트를 완료합니다.
2. **UCRT64** 셸에서 앱에 필요한 패키지를 설치합니다.

   ```sh
   pacman -S --needed \
     mingw-w64-ucrt-x86_64-gtk3 \
     mingw-w64-ucrt-x86_64-adwaita-icon-theme \
     mingw-w64-ucrt-x86_64-adwaita-icon-theme-legacy \
     mingw-w64-ucrt-x86_64-python-gobject \
     mingw-w64-ucrt-x86_64-python-pillow \
     mingw-w64-ucrt-x86_64-python-numpy
   ```

3. 압축을 푼 폴더에서 **일반 PowerShell**을 열고 아래 명령을 실행합니다. `C:\msys64`는 MSYS2 설치 경로에 맞게 바꿉니다.

   ```powershell
   .\Install-Windows.ps1 -MsysRoot C:\msys64
   ```

4. 시작 메뉴에서 **GFN Flow**를 엽니다.

Windows 로그인 시 실행하려면 설치 명령에 `-StartOnSignIn`을 붙입니다. 설치 직후 스튜디오를 열려면 `-Launch`를 사용합니다.

## Linux (Hyprland)

Python 3, GTK 3, Cairo를 지원하는 PyGObject, GtkLayerShell 0.1 typelib, Adwaita 아이콘, Pillow와 NumPy가 필요합니다. 매크로 재생은 `ydotool`과 해당 데몬을 사용합니다. 계정 저장에는 `secret-tool`과 Secret Service가, 입력 녹화에는 Python `libevdev`와 입력 장치 읽기 권한이 필요합니다.

```sh
sh ./Install-Linux.sh
~/.local/bin/macro-studio
```

설치 스크립트는 사용자 서비스를 등록합니다. 키가 비어 있다면 Hyprland 설정에 다음을 추가합니다.

```ini
bind = , F9, exec, ~/.local/bin/macro-studio f9
bind = , F10, exec, ~/.local/bin/macro-studio vpn-toggle
```

기존 `~/.config/auto-macro` 라이브러리와 기존 로그인 프로필은 원래 위치에서 읽습니다. Linux VPN은 NetworkManager 기반 일본 VPN 설정을 사용합니다.

## Windows VPN (선택 사항)

1. [WireGuard for Windows](https://www.wireguard.com/install/)를 설치합니다.
2. GFN Flow를 사용할 Windows 계정으로 **관리자 PowerShell**을 열고, 압축을 푼 폴더에서 전용 서비스를 한 번 설치합니다.

   ```powershell
   .\Install-Windows-Vpn.ps1
   ```

3. 제공업체에서 서버 하나에 해당하는 WireGuard `.conf`를 받습니다.

   - [NordVPN](https://support.nordvpn.com/hc/en-us/articles/51470867535633)
   - [Proton VPN Free / Plus](https://protonvpn.com/support/wireguard-configurations)
   - [Mullvad](https://mullvad.net/en/help/wireguard-app-windows)
   - [Surfshark](https://support.surfshark.com/hc/en-us/articles/6586553044498)

4. **설정 → VPN**에서 파일에 맞는 제공업체와 국가를 고른 후 가져옵니다. **이 프로필 사용**을 누르면 F10에서 사용할 수 있습니다.

VPN을 연결한 상태에서 활성 프로필을 바꾸려면 F10으로 먼저 연결을 끊습니다. 국가와 서버는 요금제에 따라 달라집니다. 설정 파일에는 개인 키가 있으므로 공개 저장소나 이슈에 올리지 마십시오.

## 다운로드 확인

ZIP의 SHA-256이 같은 릴리스의 `.sha256` 파일과 일치하는지 확인할 수 있습니다.

```powershell
Get-FileHash -Algorithm SHA256 .\GFN_Flow-0.3.0.zip
```

```sh
sha256sum -c GFN_Flow-0.3.0.sha256
```

위 명령의 파일 이름은 다운로드한 버전에 맞게 바꿉니다.
