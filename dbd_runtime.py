from __future__ import annotations

import ctypes
import os
from pathlib import Path


APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DBDMatchNotifier"


def safe_print(*args, **kwargs) -> None:
    """Print when a console exists; stay usable in a PyInstaller windowed build."""
    try:
        import sys
        if sys.stdout is not None:
            print(*args, **kwargs)
    except Exception:
        pass


def acquire_single_instance() -> int | None:
    """Return a named mutex handle, or None when another instance is active."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.GetLastError.restype = ctypes.c_ulong
    handle = kernel32.CreateMutexW(None, False, "Local\\DBDMatchNotifier.SingleInstance")
    if not handle:
        return None
    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def release_single_instance(handle: int | None) -> None:
    if handle:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
