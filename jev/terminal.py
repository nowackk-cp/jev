"""Terminal çıktısı: UTF-8, ANSI renkleri, kısa olay satırları."""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

_COLORS = {
    "reset": "\x1b[0m", "dim": "\x1b[2m", "bold": "\x1b[1m", "red": "\x1b[31m", "green": "\x1b[32m",
    "yellow": "\x1b[33m", "blue": "\x1b[34m", "magenta": "\x1b[35m", "cyan": "\x1b[36m", "gray": "\x1b[90m",
    "orange": "\x1b[38;5;208m",
}
AGENT_COLORS = {"opus": "magenta", "sol": "yellow", "sonnet": "blue", "luna": "green", "jev": "orange"}


def setup_console() -> bool:
    """stdout/stderr'i UTF-8 yapar, Windows'ta VT modunu açar. Renk kullanılabilir mi döndürür."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.environ.setdefault("PYTHONUTF8", "1")
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            k.SetConsoleOutputCP(65001)
        except Exception:
            return False
    return True


class Terminal:
    def __init__(self, color: bool | None = None, verbose: bool = False):
        self.color = setup_console() if color is None else color
        self.verbose = verbose
        self.stamps = True  # liste komutlarında (durum, rapor, geçmiş, ajanlar) saat damgası kapatılır
        self._lock = threading.Lock()

    def c(self, text: str, color: str) -> str:
        if not self.color:
            return text
        return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"

    def line(self, text: str, color: str | None = None, stamp: bool | None = None) -> None:
        stamp = self.stamps if stamp is None else stamp
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = self.c(ts, "gray") + " " if stamp and text else ""  # boş satıra damga basılmaz
        body = self.c(text, color) if color else text
        with self._lock:
            try:
                print(prefix + body, flush=True)
            except (OSError, ValueError):
                pass

    def agent(self, name: str) -> str:
        return self.c(name, AGENT_COLORS.get(name, "bold"))

    def header(self, text: str) -> None:
        self.line("")
        self.line(self.c("━" * 8 + " " + text + " " + "━" * 8, "bold"), stamp=False)

    def info(self, text: str, stamp: bool | None = None) -> None:
        self.line(text, stamp=stamp)

    def ok(self, text: str, stamp: bool | None = None) -> None:
        self.line("✓ " + text, "green", stamp)

    def warn(self, text: str, stamp: bool | None = None) -> None:
        self.line("! " + text, "yellow", stamp)

    def error(self, text: str, stamp: bool | None = None) -> None:
        self.line("✗ " + text, "red", stamp)

    def dim(self, text: str, stamp: bool | None = None) -> None:
        self.line(text, "gray", stamp)
