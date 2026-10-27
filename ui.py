"""
Terminal capabilities: colors and symbols that work in Windows CMD, Windows Terminal,
PowerShell, Linux and macOS. Import this before printing anything.

Set AGENT_ASCII=1 to force plain ASCII symbols, NO_COLOR=1 to disable colors.
"""
import os
import sys


def _enable_vt_windows() -> bool:
    """Turn on ANSI escape processing in the Windows console (Windows 10+)."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        handle = k.GetStdHandle(-11)                 # stdout
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _setup():
    # Never crash on characters the console/code page cannot show.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    if color and os.name == "nt":
        color = _enable_vt_windows()
    if os.environ.get("AGENT_ASCII"):
        fancy = False
    elif os.name != "nt":
        fancy = True
    else:   # legacy CMD fonts often lack these glyphs; Windows Terminal / VS Code have them
        fancy = bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM")
                     or os.environ.get("ConEmuANSI"))
    return color, fancy


COLOR, FANCY = _setup()

DIM = "\033[2m" if COLOR else ""
BOLD = "\033[1m" if COLOR else ""
RED = "\033[31m" if COLOR else ""
RESET = "\033[0m" if COLOR else ""
CLEAR_LINE = "\r\033[K" if COLOR else ""

DOT = "●" if FANCY else "*"
RES = "⎿" if FANCY else "|_"
BAR = "│" if FANCY else "|"
ELL = "…" if FANCY else "..."
SEP = "·" if FANCY else "-"
OK = "✓" if FANCY else "yes"
NO = "✗" if FANCY else "NO"
