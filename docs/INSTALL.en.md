# Install GFN Flow

[README](README.en.md) · [한국어](INSTALL.md) · [Latest download](https://github.com/Mangom72/GFN_Flow/releases/latest)

Download the ZIP and its SHA-256 file from the latest release. The ZIP contains installers for Windows and Linux. The Windows package uses the MSYS2 runtime.

## Windows x64

1. Install [MSYS2](https://www.msys2.org/) and complete its initial updates.
2. In the **UCRT64** shell, install the runtime packages.

   ```sh
   pacman -S --needed \
     mingw-w64-ucrt-x86_64-gtk3 \
     mingw-w64-ucrt-x86_64-adwaita-icon-theme \
     mingw-w64-ucrt-x86_64-adwaita-icon-theme-legacy \
     mingw-w64-ucrt-x86_64-python-gobject \
     mingw-w64-ucrt-x86_64-python-pillow \
     mingw-w64-ucrt-x86_64-python-numpy
   ```

3. Open regular PowerShell in the extracted folder. Adjust `-MsysRoot` to match your MSYS2 installation.

   ```powershell
   .\Install-Windows.ps1 -MsysRoot C:\msys64
   ```

4. Open **GFN Flow** from Start.

Add `-StartOnSignIn` to start GFN Flow when you sign in, or `-Launch` to open the Studio after installation.

## Linux (Hyprland)

Install Python 3, GTK 3, PyGObject with Cairo support, the GtkLayerShell 0.1 typelib, Adwaita icons, Pillow, and NumPy. Playback uses `ydotool` and its daemon. Credential storage requires `secret-tool` and a running Secret Service. Recording requires Python `libevdev` and access to read input devices.

```sh
sh ./Install-Linux.sh
~/.local/bin/macro-studio
```

The installer registers a user service. Add these bindings to your Hyprland configuration if the keys are available:

```ini
bind = , F9, exec, ~/.local/bin/macro-studio f9
bind = , F10, exec, ~/.local/bin/macro-studio vpn-toggle
```

Existing `~/.config/auto-macro` libraries and legacy login profiles are read in place. Linux uses the existing Japan VPN setup with NetworkManager.

## Windows VPN (optional)

1. Install [WireGuard for Windows](https://www.wireguard.com/install/).
2. Open PowerShell as administrator under the Windows account that will use GFN Flow. Run this once from the extracted folder:

   ```powershell
   .\Install-Windows-Vpn.ps1
   ```

3. Download a single-server WireGuard configuration from [NordVPN](https://support.nordvpn.com/hc/en-us/articles/51470867535633), [Proton VPN Free or Plus](https://protonvpn.com/support/wireguard-configurations), [Mullvad](https://mullvad.net/en/help/wireguard-app-windows), or [Surfshark](https://support.surfshark.com/hc/en-us/articles/6586553044498).
4. In **Settings → VPN**, select the provider and country that match the file, import it, and choose **Use profile** to assign it to F10.

Disconnect before changing the active profile. Available servers depend on your provider and plan. Keep the WireGuard configuration private because it contains a private key.

## Verify the download

Compare the archive hash with the `.sha256` file from the same release:

```powershell
Get-FileHash -Algorithm SHA256 .\GFN_Flow-0.3.0.zip
```

```sh
sha256sum -c GFN_Flow-0.3.0.sha256
```

Use the file name for the version you downloaded.
