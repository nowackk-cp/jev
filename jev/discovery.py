"""CLI yürütülebilirlerini bulma: önce ayardaki yol, sonra PATH, sonra bilinen klasörler."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CliInfo:
    name: str
    path: Path | None
    version: str | None
    source: str  # ayar | PATH | bilinen klasör | bulunamadı


def _version_key(p: Path) -> tuple:
    parts = re.findall(r"\d+", p.parent.name)
    return tuple(int(x) for x in parts) if parts else (0,)


def _package_caches(local: Path, kind: str) -> list[Path]:
    """Paketli (MSIX, Microsoft Store) masaüstü uygulamalarının AppData klasörleri.

    Claude masaüstü uygulaması gibi paketli bir uygulamanın %APPDATA% ya da %LOCALAPPDATA% altına
    yazdıkları aslında %LOCALAPPDATA%\\Packages\\<paket>\\LocalCache\\<Roaming|Local> altında durur.
    Uygulamanın içinden başlayan işlemler onları eski yerde görür; kullanıcının kendi terminali
    göremez. Bu yüzden aynı sürümde önce buradaki gerçek yol seçilir: her terminalde çalışır.
    """
    return sorted((local / "Packages").glob(f"*/LocalCache/{kind}"))


def _candidates(name: str) -> list[Path]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    out: list[Path] = []
    if name == "codex":
        found = [p for base in (*_package_caches(local, "Local"), local)
                 for p in (base / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")]
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # en son değiştirilen
        out += found
        out += [home / ".local" / "bin" / "codex.exe"]
    elif name == "claude":
        found = [p for base in (*_package_caches(local, "Roaming"), roaming)
                 for p in (base / "Claude" / "claude-code").glob("*/claude.exe")]
        found.sort(key=_version_key, reverse=True)  # en yüksek sürüm (sıralama kararlı: eşitlikte paket önde)
        out += found
        out += [home / ".local" / "bin" / "claude.exe", home / ".claude" / "local" / "claude.exe"]
    return [p for p in out if p.exists()]


def find_cli(name: str, configured: str = "auto") -> CliInfo:
    if configured and configured != "auto":
        p = Path(os.path.expandvars(os.path.expanduser(configured)))
        return CliInfo(name, p if p.exists() else None, None, "ayar" if p.exists() else "bulunamadı")
    w = shutil.which(name)
    if w:
        return CliInfo(name, Path(w), None, "PATH")
    c = _candidates(name)
    if c:
        return CliInfo(name, c[0], None, "bilinen klasör")
    return CliInfo(name, None, None, "bulunamadı")


def cli_version(info: CliInfo, timeout: int = 20) -> str | None:
    if not info.path:
        return None
    try:
        r = subprocess.run([str(info.path), "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        v = (r.stdout or r.stderr).strip().splitlines()
        info.version = v[0] if v else None
    except (OSError, subprocess.TimeoutExpired):
        info.version = None
    return info.version
