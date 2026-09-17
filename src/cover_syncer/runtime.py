"""Initialize bundled dependencies before importing native Python extensions."""
from __future__ import annotations

import os
from pathlib import Path
import sys


_dll_directories = []
_configured = False


def configure_runtime() -> None:
    global _configured
    if _configured:
        return
    bundled = Path(sys.prefix) / "Library" / "bin"
    if bundled.is_dir():
        # Explorer/native launches do not inherit `conda activate`. PATH is also
        # used by ctypes.find_library and by FFmpeg's own dependent DLLs.
        os.environ["PATH"] = str(bundled) + os.pathsep + os.environ.get("PATH", "")
        if os.name == "nt":
            # Keep the handle alive: closing it removes the DLL search directory.
            _dll_directories.append(os.add_dll_directory(str(bundled)))
    _configured = True
