# GFN Flow

GFN Flow runs per-game macros in GeForce NOW on Windows and Linux with Hyprland. Record or edit an action sequence, replay it from the game picker, or assign a login macro to the first F9 press of a session. Visual workflows can use a matching image to decide when to run an action.

The distribution includes the application and installers. A fresh installation starts with an empty library and uses the platform's credential store when you register a login.

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

Install Python 3, GTK 3, PyGObject, the `GtkLayerShell` 0.1 typelib, and Adwaita icons. Configure `ydotool` and its daemon for playback. Login credentials need `secret-tool` and a running Secret Service. Recording needs Python `libevdev` and read access to input devices. Visual workflows need Pillow and NumPy.

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
3. Focus the game and press **F9**. If first-F9 login is enabled, the app attempts it once per game session. Otherwise, F9 opens the picker so you can select a macro. Press F9 again during playback to stop it.

In Macro Studio, **Ctrl+1–5** opens Library, History, Visual Workflows, Login Credentials, or Blacklist. **Esc** goes back. Deleting a detected game from **Blacklist** stops detection and keeps its macros. **Unblock and restore** puts the game back immediately. **Remove only** lets detection add it again when the game is next seen.

> [!NOTE]
> Credentials registered in **Login Credentials** are stored in Windows Credential Manager or Linux Secret Service. Ordinary text actions are included in library exports, so use the saved credential actions for account names and passwords.

## Optional controls

- **F10** toggles Japan VPN. Windows requires [WireGuard](https://www.wireguard.com/install/) and your own single-peer configuration at `%APPDATA%\MacroStudioNative\vpn\gfn-jp.conf`. Starting or comparing tunnels can request administrator approval. Linux uses NetworkManager and, where configured, NordVPN.
- **F12** toggles Windows Mobile Hotspot when a GeForce NOW window is focused. The tray menu also has a **Mobile Hotspot** command.

See [LICENSE](LICENSE) for the MIT license and [third-party notices](THIRD_PARTY_NOTICES.md) for bundled components.
