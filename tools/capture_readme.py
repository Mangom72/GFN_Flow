"""Render README images from synthetic data without opening desktop windows."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro
from macro_manager import Manager


def render(output):
    if sys.platform != 'win32':
        raise SystemExit('The README screenshots include Windows VPN settings. Render them on Windows.')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        profiles = [login.Profile(name, name, 'demo@example.invalid', 'demo-key', 1920, 1080,
                                  ['id', 'tab', 'pw', 'enter'])
                    for name in ('Demo Game', 'Adventure Demo')]
        library = MacroLibrary(Path(temporary) / 'library.json', profiles)
        for game, name, steps in (
                ('Demo Game', 'Confirm menu', ['click:960,540', 'wait:250', 'key:Enter', 'wait:300', 'key:Escape']),
                ('Demo Game', 'Open inventory', ['key:I', 'wait:300']),
                ('Adventure Demo', 'Daily menu', ['key:Escape', 'wait:250', 'key:Enter']),
                ('Adventure Demo', 'Close dialogs', ['key:Escape', 'wait:200'])):
            macro = new_macro('normal')
            macro.update(game=game, title_re=game, name=name, steps=steps)
            library.put(macro)
        vault = {}
        with (patch.object(login, 'load_profiles', return_value=({}, profiles)),
              patch.object(login, 'store_secret', side_effect=vault.__setitem__),
              patch.object(login, 'get_secret', side_effect=vault.__getitem__)):
            library.save_extra_account(library.macros[0]['id'], None, '보조 계정', 'alt@example.invalid', 'fixture-only')
            picker = Picker(preview=True, width=430, height=520, library=library)
            picker.scan = lambda: None
            picker.localizer.set_language('ko')
            manager = Manager(picker, offscreen=True, width=860, height=480)
            picker.manager = manager
            manager.show()

            def settle(milliseconds=80):
                loop = picker.GLib.MainLoop()
                picker.GLib.timeout_add(milliseconds, lambda: (loop.quit(), False)[1])
                loop.run()

            def screenshot(window, filename):
                settle()
                window.get_pixbuf().savev(str(output / filename), 'png', [], [])

            try:
                manager.tree.expand_all()
                screenshot(manager.window, 'studio.png')
                manager.edit(library.macros[2])
                manager.editor.get_children()[0].set_current_page(1)
                screenshot(manager.window, 'editor.png')
                manager.show_library()
                picker.game = (login.Window('example-game', 'Demo Game', 'GeForceNOW', 0, 0, 1920, 1080), profiles[0])
                picker.render()
                picker.run_macro(library.macros[0])
                screenshot(picker.window, 'accounts.png')
                picker.dismiss_login_choice()
                picker.show_settings()
                settle()
                picker.settings_page.scroll.get_vadjustment().set_value(
                    picker.settings_page.vpn.root.get_allocation().y - 6)
                screenshot(picker.window, 'vpn.png')
                picker.hide()
                picker.motion.set_enabled(True)
                settings = picker.Gtk.Settings.get_default()
                original = settings.get_property('gtk-enable-animations')
                settings.set_property('gtk-enable-animations', True)
                frames, durations = [], []
                try:
                    manager.show()
                    for index in (0, 2, 3, 4, 0):
                        manager.tabs.set_current_page(index)
                        for frame in range(7):
                            settle(32)
                            pixbuf = manager.window.get_pixbuf()
                            mode = 'RGBA' if pixbuf.get_has_alpha() else 'RGB'
                            image = Image.frombytes(mode, (pixbuf.get_width(), pixbuf.get_height()),
                                                    pixbuf.get_pixels(), 'raw', mode, pixbuf.get_rowstride())
                            frames.append(image.convert('RGB'))
                            durations.append(700 if frame == 6 else 32)
                    frames[0].save(output / 'tabs.gif', save_all=True, append_images=frames[1:],
                                   duration=durations, loop=0, optimize=True, disposal=2)
                finally:
                    settings.set_property('gtk-enable-animations', original)
            finally:
                manager.window.destroy()
                picker.window.destroy()


if __name__ == '__main__':
    render(ROOT / 'docs' / 'images')
