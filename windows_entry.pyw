"""Log GUI startup and command failures without opening a console window."""
from pathlib import Path
import os
import sys
import traceback

logs = Path(os.environ['LOCALAPPDATA']) / 'MacroStudioNative' / 'logs'
logs.mkdir(parents=True, exist_ok=True)
with (logs / 'app.log').open('a', encoding='utf-8', buffering=1) as log:
    sys.stdout = sys.stderr = log
    try:
        from auto_macro import main
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
