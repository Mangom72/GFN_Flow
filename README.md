# GFN Flow

GFN Flow runs per-game macros in GeForce NOW on Windows and Linux with Hyprland. Record or edit an action sequence, replay it from the game picker, or assign a login macro to the first F9 press of a session. A login macro can share its actions across several accounts for the same game.

Download the ZIP and its SHA-256 checksum from [GitHub Releases](https://github.com/Mangom72/GFN_Flow/releases/latest). The ZIP includes the application and installers. A fresh installation starts with an empty library and uses the platform's credential store when you register a login.

## Install

Run these commands from a checkout of this repository or from the extracted distribution ZIP.

### Windows x64

Install [MSYS2](https://www.msys2.org/) and finish its update steps. In the **UCRT64** shell, install the runtime packages.

```sh
pacman -S --needed \
  mingw-w64-ucrt-x86_64-gtk3 \
  mingw-w64-ucrt-x86_64-adwaita-icon-theme \
  mingw-w64-ucrt-x86_64-adwaita-icon-theme-legacy \
  mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-python-pillow \
  mingw-w64-ucrt-x86_64-python-numpy
```

Run the installer in PowerShell. Adjust `-MsysRoot` to match your MSYS2 installation.

```powershell
.\Install-Windows.ps1 -MsysRoot C:\msys64
```

Open **GFN Flow** from Start. Add `-StartOnSignIn` to create a startup shortcut or `-Launch` to open the library when installation finishes.

### Linux with Hyprland

Install Python 3, GTK 3, PyGObject with Cairo support, the `GtkLayerShell` 0.1 typelib, and Adwaita icons. Configure `ydotool` and its daemon for playback. Login credentials need `secret-tool` and a running Secret Service. Recording needs Python `libevdev` and read access to input devices. Visual workflows need Pillow and NumPy.

```sh
sh ./Install-Linux.sh
~/.local/bin/macro-studio
```

The installer registers a user service. To use F9 and F10, add the following bindings to your Hyprland configuration if those keys are free.

```ini
bind = , F9, exec, ~/.local/bin/macro-studio f9
bind = , F10, exec, ~/.local/bin/macro-studio vpn-toggle
```

Existing `~/.config/auto-macro` libraries and legacy GFN login profiles are read from their usual locations.

## Run your first macro

1. Open a game in the GeForce NOW app so GFN Flow can detect it. Open the library and use **Add macro** to create a normal or login macro for that game.
2. Build or record the action sequence and save it. For a login macro, use the saved-account and saved-password actions and register the account in **Login Credentials**. Enable first-F9 login in the editor if you want it to run automatically on that keypress.
3. Focus the game and press **F9**. By default, the app attempts the enabled login macro once per game session. Later presses open the picker. Press F9 again during playback to stop it.

In Macro Studio, **Ctrl+1–5** opens Library, History, Visual Workflows, Login Credentials, or Blacklist. **Esc** goes back. Deleting a detected game from **Blacklist** stops detection and keeps its macros. **Unblock and restore** puts the game back immediately. **Remove only** lets detection add it again when the game is next seen.

For multiple accounts in one game, open **Login Credentials**, select the existing login macro, and choose **Add profile**. F9 and manual runs ask which account to use only when that macro has more than one. The **Page animations** setting controls the studio tab label and page transitions.

## Set F9 behavior

Open **Settings → F9 behavior** to choose when F9 can open the picker.

| Open the picker | When F9 can open it |
| --- | --- |
| Always | Even when GeForce NOW is closed |
| When GeForce NOW is running | The launcher or a game is running, including in the background |
| When GeForce NOW is focused | The launcher or a game is the active window |
| When a GFN game is running | A game session is detected and allowed by the blacklist, even in the background |

Turn on **Run login macro on first F9** to attempt the game's enabled login macro once per session while the game is focused. Turn it off to open the picker without logging in. **Reopen picker after normal macros** controls what happens after successful playback. F9 can still close an open picker or stop playback, and the app menu can open it directly.

> [!NOTE]
> Credentials registered in **Login Credentials** are stored in Windows Credential Manager or Linux Secret Service. Additional account profiles are omitted from exports. Ordinary text actions are included, so use the saved credential actions for account names and passwords.

## VPN on Windows

The VPN is optional. Install [WireGuard for Windows](https://www.wireguard.com/install/). Then open PowerShell as administrator from the extracted release and run this once under the same Windows account that will use GFN Flow:

```powershell
.\Install-Windows-Vpn.ps1
```

Open **Settings → VPN**, choose a provider and country, and import its single-server WireGuard `.conf` file. Search the country menu by name or two-letter code. Select **Use profile** to assign it to F10. Disconnect before switching the active profile.

Supported sources: [NordVPN](https://support.nordvpn.com/hc/en-us/articles/51470867535633), [Proton VPN Free or Plus](https://protonvpn.com/support/wireguard-configurations), [Mullvad](https://mullvad.net/en/help/wireguard-app-windows), and [Surfshark](https://support.surfshark.com/hc/en-us/articles/6586553044498). Available countries depend on your provider and plan. The VPN service applies the GFN routes and leaves the default internet route in place. Profile imports, F10, and the NordVPN Japan server comparison run without administrator prompts after setup.

## Optional controls

- **F10** toggles the selected VPN on Windows. Linux uses the existing Japan VPN setup with NetworkManager and, where configured, NordVPN.
- **F12** toggles Windows Mobile Hotspot when a GeForce NOW window is focused. The tray menu also has a **Mobile Hotspot** command.

See [LICENSE](LICENSE) for the MIT license and [third-party notices](THIRD_PARTY_NOTICES.md) for bundled components.
