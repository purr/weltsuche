"""terminal output for the command line: one aligned status line per fact,
coloured when stdout is a terminal and NO_COLOR is not set. the mcp server
never prints here; stdout is its protocol channel."""

import ctypes
import os
import sys

_KINDS = {"ok": ("32", "✓"), "info": ("36", "·"), "warn": ("33", "!"), "fail": ("31", "✗")}
LABEL_WIDTH = 18


def _enable_windows_vt():
    """ansi escapes on a windows console (windows 10 and later). a pipe or
    file is not a console, and the reader on the other end handles escapes."""
    if os.name != "nt":
        return True
    try:
        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x4))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        # colour is cosmetic: without it the lines are the same, just plain
        return False


def _colour():
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and _enable_windows_vt()


def _line(kind, label, detail=""):
    code, glyph = _KINDS[kind]
    label = f"{label:<{LABEL_WIDTH}}"
    if _colour():
        glyph, label = f"\033[{code}m{glyph}\033[0m", f"\033[1m{label}\033[0m"
    sys.stdout.write(f"  {glyph}  {label} {detail}\n")


def ok(label, detail=""):
    _line("ok", label, detail)


def info(label, detail=""):
    _line("info", label, detail)


def warn(label, detail=""):
    _line("warn", label, detail)


def fail(label, detail=""):
    _line("fail", label, detail)


def write(text):
    sys.stdout.write(text)
