#!/bin/sh
set -eu

source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
app_dir="${XDG_DATA_HOME:-$HOME/.local/share}/gfn-flow"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
bin_dir="$HOME/.local/bin"

python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("GtkLayerShell", "0.1"); from gi.repository import Gtk, GtkLayerShell'
install -d "$app_dir/assets/fonts" "$app_dir/login" "$unit_dir" "$bin_dir"
install -m 644 "$source_dir"/*.py "$source_dir"/ui_tokens.json "$source_dir"/ui_ko.json "$app_dir/"
install -m 644 "$source_dir"/assets/filter.svg "$app_dir/assets/"
install -m 644 "$source_dir"/login/gfn_login.py "$app_dir/login/"
install -m 644 "$source_dir"/popup_click.lua "$source_dir"/LICENSE "$source_dir"/THIRD_PARTY_NOTICES.md "$app_dir/"
for font in PretendardVariable.ttf Pretendard-OFL.txt; do
    if [ -f "$source_dir/assets/fonts/$font" ]; then
        install -m 644 "$source_dir/assets/fonts/$font" "$app_dir/assets/fonts/"
    fi
done
python3 "$source_dir/tools/fetch_font.py" "$app_dir/assets/fonts"
font_dir="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/gfn-flow"
install -d "$font_dir"
install -m 644 "$app_dir/assets/fonts/PretendardVariable.ttf" "$font_dir/"
if command -v fc-cache >/dev/null 2>&1; then fc-cache "$font_dir"; fi

install -m 755 "$source_dir/platform/linux/macro-studio" "$source_dir/platform/linux/gfn-login" "$source_dir/platform/linux/gfn-anti-afk" \
    "$source_dir/platform/linux/gfn-anti-afk.py" "$bin_dir/"
install -m 644 "$source_dir/auto-macro.service" "$source_dir/platform/linux/gfn-anti-afk.service" "$unit_dir/"
systemctl --user daemon-reload
if systemctl --user is-active --quiet auto-macro.service; then
    systemctl --user restart auto-macro.service
fi
printf '%s\n' 'GFN Flow installed. Run ~/.local/bin/macro-studio to open the library.'
