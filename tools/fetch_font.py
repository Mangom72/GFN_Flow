"""Install the pinned upstream Pretendard font and its license."""
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import urlopen

SOURCE = 'https://raw.githubusercontent.com/orioncactus/pretendard/v1.3.9/packages/pretendard/dist/'
FILES = {
    'PretendardVariable.ttf': ('public/variable/PretendardVariable.ttf',
                              '3090ccde0442bb347aa7685d9ba8b17436a60682df6e8f92a9a670de14056e22'),
    'Pretendard-OFL.txt': ('LICENSE.txt', 'b04538c9abec39a3db75108cf0af0fd9c77032fe8aa2cf38345b4d250e98e38e'),
}


def install(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, (source, digest) in FILES.items():
        path = directory / name
        if path.is_file() and sha256(path.read_bytes()).hexdigest() == digest:
            continue
        with urlopen(SOURCE + source, timeout=45) as response:
            data = response.read(8 * 1024 * 1024)
        if sha256(data).hexdigest() != digest:
            raise ValueError(f'Upstream checksum mismatch for {name}.')
        path.write_bytes(data)


if __name__ == '__main__':
    install(Path(sys.argv[1]))
