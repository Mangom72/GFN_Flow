"""Create a release from application source and explicitly listed assets."""
import argparse
from pathlib import Path
import re
import zipfile

from fetch_font import install as install_font

ROOT = Path(__file__).resolve().parents[1]
TEXT_FILES = (
    '.gitignore', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'UI_REVIEW.md',
    'Install-Linux.sh', 'Install-Windows.ps1', 'auto-macro.service', 'popup_click.lua',
    'ui_ko.json', 'ui_tokens.json', 'login/gfn_login.py', 'assets/filter.svg',
    'assets/input-event-codes.h', 'licenses/GPL-2.0-only.txt', 'licenses/Linux-syscall-note.txt',
    'platform/linux/macro-studio', 'platform/linux/gfn-login', 'platform/linux/gfn-anti-afk',
    'platform/linux/gfn-anti-afk.py', 'platform/linux/gfn-anti-afk.service',
    'tools/build_release.py', 'tools/fetch_font.py',
)
PRIVATE = re.compile(r'(?i)(?:[A-Z]:[\\/]Users[\\/]|/home' r'/[^$%/{]|'
                     r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|'
                     r'^\s*(?:PrivateKey|PresharedKey)\s*=\s*[A-Za-z0-9+/]{43}=|'
                     r'(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,})', re.MULTILINE)


def checked_text(path):
    text = path.read_text(encoding='utf-8-sig')
    if PRIVATE.search(text):
        raise ValueError(f'Potential private data in {path.relative_to(ROOT)}.')
    return text.encode('utf-8')


def build(stage, archive):
    stage.mkdir(parents=True, exist_ok=False)
    files = {path: ROOT / path for path in TEXT_FILES}
    readme = ROOT / 'README-release.md'
    files['README.md'] = readme if readme.exists() else ROOT / 'README.md'
    for path in (*ROOT.glob('*.py'), *ROOT.glob('*.pyw'), *(ROOT / 'tests').glob('test_*.py')):
        if path.name not in ('windows_migrate.py', 'ui_qa.py'):
            files[path.relative_to(ROOT).as_posix()] = path
    for relative, source in sorted(files.items()):
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(checked_text(source))
    font_dir = stage / 'assets/fonts'
    font_dir.mkdir()
    for name in ('PretendardVariable.ttf', 'Pretendard-OFL.txt'):
        source = ROOT / 'assets/fonts' / name
        if source.is_file():
            (font_dir / name).write_bytes(source.read_bytes())
    install_font(font_dir)

    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as release:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                relative = path.relative_to(stage).as_posix()
                entry = zipfile.ZipInfo(f'{archive.stem}/{relative}')
                entry.create_system = 3
                executable = relative == 'Install-Linux.sh' or relative.startswith('platform/linux/')
                entry.external_attr = (0o100755 if executable else 0o100644) << 16
                release.writestr(entry, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', type=Path, help='New directory for the clean source tree')
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    build(args.stage, args.archive)
