"""Makinenin ortamı (belirtim §8): işletim sistemi, kurulu araçlar, Python paketleri ve ortak kaynaklar.

Süreç başına bir kez ölçülür; PATH değişince (ör. bir işçi winget ile araç kurunca) yeniden ölçülür. Pencereli
programlar (Inkscape, Blender, LibreOffice, Chrome) çalıştırılmaz: sürümleri dosyanın sürüm bilgisinden okunur."""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .util import jev_home, refresh_path

# Jev'in tanıdığı araçlar. tur: "cli" sürüm komutuyla ölçülür · "var" yalnızca bulunur · "gui" pencereli program,
# çalıştırılmaz. yerler: PATH'te değilse bakılan kalıplar (ortam değişkenleri açılır; * birden çok eşleşirse en
# yüksek sürüm seçilir). Klasörü PATH'e eklenen tek araç Graphviz: klasöründe yalnızca kendi araçları var.
# LibreOffice, Inkscape ve Blender klasörlerinde kendi python.exe'leri durduğundan onlar tam yoluyla anılır.
TOOLS: tuple[dict, ...] = (
    {"ad": "ImageMagick", "komut": "magick", "tur": "cli", "surum": ("-version",), "kelime": 2,
     "yerler": (r"%ProgramFiles%\ImageMagick-*\magick.exe",)},
    {"ad": "pandoc", "komut": "pandoc", "tur": "cli", "surum": ("--version",), "kelime": 1,
     "yerler": (r"%LOCALAPPDATA%\Pandoc\pandoc.exe", r"%ProgramFiles%\Pandoc\pandoc.exe")},
    {"ad": "typst", "komut": "typst", "tur": "cli", "surum": ("--version",), "kelime": 1, "yerler": ()},
    {"ad": "Graphviz", "komut": "dot", "tur": "cli", "surum": ("-V",), "kelime": 4, "path_e_ekle": True,
     "yerler": (r"%ProgramFiles%\Graphviz\bin\dot.exe", r"%ProgramFiles(x86)%\Graphviz\bin\dot.exe")},
    {"ad": "LaTeX", "komut": "xelatex", "tur": "var", "kardesler": ("xelatex", "lualatex", "pdflatex"),
     "dagitimlar": {"miktex": "MiKTeX", "texlive": "TeX Live"},
     "yerler": (r"%LOCALAPPDATA%\Programs\MiKTeX\miktex\bin\x64\xelatex.exe",
                r"%ProgramFiles%\MiKTeX\miktex\bin\x64\xelatex.exe", r"%SystemDrive%\texlive\*\bin\win*\xelatex.exe")},
    {"ad": "Inkscape", "komut": "inkscape", "tur": "gui", "yerler": (r"%ProgramFiles%\Inkscape\bin\inkscape.com",)},
    {"ad": "Blender", "komut": "blender", "tur": "gui",
     "yerler": (r"%ProgramFiles%\Blender Foundation\Blender *\blender.exe",)},
    {"ad": "LibreOffice", "komut": "soffice", "tur": "gui", "yerler": (r"%ProgramFiles%\LibreOffice\program\soffice.com",)},
    {"ad": "Google Chrome", "komut": "chrome", "tur": "gui", "not": "Playwright'ta channel=\"chrome\"",
     "yerler": (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")},
)
# Promptta anılan Python paketleri (görünen ad, içe aktarma adı). Tam liste ortak kataloğun işi.
PY_PACKAGES = (("numpy", "numpy"), ("pandas", "pandas"), ("scipy", "scipy"), ("matplotlib", "matplotlib"),
               ("plotly", "plotly"), ("pillow", "PIL"), ("opencv", "cv2"), ("scikit-image", "skimage"),
               ("moviepy", "moviepy"), ("manim", "manim"), ("av", "av"), ("geopandas", "geopandas"),
               ("rasterio", "rasterio"), ("cartopy", "cartopy"), ("shapely", "shapely"), ("pyproj", "pyproj"),
               ("python-docx", "docx"), ("python-pptx", "pptx"), ("openpyxl", "openpyxl"),
               ("reportlab", "reportlab"), ("pymupdf", "pymupdf"), ("playwright", "playwright"))
_PY_PROBE = "import importlib.util as u,sys;print(' '.join(m for m in sys.argv[1:] if u.find_spec(m)))"

RELOCATE_SECONDS = 60

_ENV: dict | None = None
_KEY: tuple | None = None  # ölçümün parmak izi: PATH ve araçların yerleri
_LOCATED_AT = 0.0
_LOCK = threading.Lock()


def _run(argv: list[str], timeout: float = 8) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None


def _tool_version(name: str, args: tuple[str, ...] = ("--version",)) -> str | None:
    """Aracın sürüm satırı (`name` komut adı ya da tam yol); kurulu değilse ya da çalışmıyorsa (ör. Microsoft
    Store'un python kısayolu) None."""
    exe = name if os.path.isabs(name) else shutil.which(name)
    r = _run([exe, *args]) if exe else None
    if r is None:
        return None
    lines = (r.stdout or r.stderr or "").strip().splitlines()
    return lines[0].strip() if r.returncode == 0 and lines else None


def _os_name() -> str:
    if os.name != "nt":
        return platform.platform(terse=True)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = str(winreg.QueryValueEx(k, "ProductName")[0])
            build = int(winreg.QueryValueEx(k, "CurrentBuild")[0])
    except (OSError, ValueError):
        return f"Windows {platform.release()}"
    if build >= 22000:  # Windows 11 kayıt defterinde hâlâ "Windows 10" der
        name = name.replace("Windows 10", "Windows 11")
    return f"{name} (derleme {build})"


def _word(text: str | None, i: int) -> str | None:
    parts = (text or "").split()
    return parts[i] if len(parts) > i else (text or None)


def _file_version(path: str) -> str | None:
    """Windows'ta programın sürüm bilgisi, programı çalıştırmadan: "1.4.4", "154.0.8037.58"."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    try:
        ver = ctypes.WinDLL("version")
    except OSError:
        return None
    ver.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, wintypes.LPDWORD)
    ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    ver.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
    ver.GetFileVersionInfoW.restype = wintypes.BOOL
    ver.VerQueryValueW.argtypes = (wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID),
                                   wintypes.PUINT)
    ver.VerQueryValueW.restype = wintypes.BOOL
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    ptr, n = wintypes.LPVOID(), wintypes.UINT()
    if (not ver.GetFileVersionInfoW(path, 0, size, buf)
            or not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)) or n.value < 52):
        return None
    f = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD * 13)).contents  # VS_FIXEDFILEINFO
    if f[0] != 0xFEEF04BD:
        return None
    parts = [f[4] >> 16, f[4] & 0xFFFF, f[5] >> 16, f[5] & 0xFFFF]  # ürün sürümü
    while len(parts) > 2 and parts[-1] == 0:
        parts.pop()
    return ".".join(map(str, parts))


def _version_key(path: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", path))


def _locate(tool: dict) -> tuple[str | None, bool]:
    """Aracın yolu ve PATH'te olup olmadığı. Bilinen yerlerde kalıp sırası tercihtir; aynı kalıbın eşleşmeleri
    arasından en yüksek sürüm seçilir."""
    w = shutil.which(tool["komut"])
    if w:
        return w, True
    for pattern in tool.get("yerler") or ():
        p = os.path.expandvars(pattern)
        found = glob.glob(p) if "%" not in p else []  # tanımsız ortam değişkeni: o kalıp atlanır
        if found:
            return max(found, key=_version_key), False
    return None, False


def _append_path(folder: str) -> None:
    """Klasörü bu sürecin PATH'inin sonuna ekler (ajanlar ve doğrulama komutları bu süreçten türer). Kalıcı değil."""
    cur = os.environ.get("PATH", "")
    have = {os.path.normcase(os.path.normpath(p)) for p in cur.split(os.pathsep) if p.strip()}
    if os.path.normcase(os.path.normpath(folder)) not in have:
        os.environ["PATH"] = cur.rstrip(os.pathsep) + os.pathsep + folder


def _py_packages() -> list[str]:
    """PY_PACKAGES içinden kurulu olanlar (içe aktarmadan, find_spec ile). Python yoksa boş."""
    mods = [m for _, m in PY_PACKAGES]
    for py in ("python", "py"):
        exe = shutil.which(py)
        r = _run([exe, "-c", _PY_PROBE, *mods], timeout=15) if exe else None
        if r is not None and r.returncode == 0:
            have = set(r.stdout.split())
            return [name for name, mod in PY_PACKAGES if mod in have]
    return []


def _tool_version_of(tool: dict, path: str) -> str | None:
    if tool["tur"] == "cli":
        return _word(_tool_version(path, tool["surum"]), tool["kelime"])
    if tool["tur"] == "gui":
        exe = Path(path).with_suffix(".exe")  # .com başlatıcının sürüm bilgisi olmayabilir
        return _file_version(str(exe if exe.is_file() else path))
    return None


def _tool_text(tool: dict, path: str, on_path: bool, version: str | None) -> str:
    """"ImageMagick 7.1.2-31 (magick)", "pandoc 3.12", "Inkscape 1.4.4 ("C:\\…\\inkscape.com")"."""
    name = f"{tool['ad']} {version}" if version else tool["ad"]
    if tool["tur"] == "var":
        folder = Path(path).parent
        how = ", ".join(s for s in tool.get("kardesler") or (tool["komut"],)
                        if (folder / (s + Path(path).suffix)).is_file()) or tool["komut"]
        dist = next((d for k, d in (tool.get("dagitimlar") or {}).items() if k in path.lower()), None)
        if dist:
            how = f"{dist}: {how}"
        if not on_path:
            how += f'; klasör "{folder}"'
    else:
        how = tool["komut"] if on_path else f'"{path}"'
    if tool.get("not"):
        how += f"; {tool['not']}"
    return name if how.lower() == tool["ad"].lower() else f"{name} ({how})"


def _locate_all() -> dict[str, tuple[str | None, bool]]:
    where = {}
    for t in TOOLS:
        path, on_path = _locate(t)
        if path and not on_path and t.get("path_e_ekle"):
            _append_path(os.path.dirname(path))
            on_path = True
        where[t["ad"]] = (path, on_path)
    return where


def _measure(where: dict[str, tuple[str | None, bool]]) -> dict:
    jobs = {"python": lambda: _word(_tool_version("python") or _tool_version("py"), 1),
            "node": lambda: _tool_version("node"), "git": lambda: _word(_tool_version("git"), 2),
            "ffmpeg": lambda: _word(_tool_version("ffmpeg", ("-version",)), 2), "paketler": _py_packages}
    for t in TOOLS:
        if where[t["ad"]][0]:
            jobs[t["ad"]] = (lambda t=t: _tool_version_of(t, where[t["ad"]][0]))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {k: ex.submit(f) for k, f in jobs.items()}
        got = {k: f.result() for k, f in futures.items()}
    node = got["node"]
    return {"os": _os_name(), "shell": "Windows PowerShell 5.1" if os.name == "nt" else "sh",
            "python": got["python"], "node": node.lstrip("v") if node else None, "git": got["git"],
            "ffmpeg": got["ffmpeg"], "winget": bool(shutil.which("winget")),
            "araclar": [{"ad": t["ad"], "yol": where[t["ad"]][0], "surum": got[t["ad"]],
                         "metin": _tool_text(t, where[t["ad"]][0], where[t["ad"]][1], got[t["ad"]])}
                        for t in TOOLS if where[t["ad"]][0]],
            "eksik": [t["ad"] for t in TOOLS if not where[t["ad"]][0]], "paketler": got["paketler"]}


def environment(refresh: bool = False) -> dict:
    """Makinenin ortamı. Ölçüm süreç başına bir kez yapılır (araçlar aynı anda, ~1 sn). Bir işçi araç kurarsa
    yeniden ölçülür: PATH değişince hemen, PATH'e eklenmeyen bir program (ör. Blender) en geç bir dakikada fark edilir."""
    global _ENV, _KEY, _LOCATED_AT
    with _LOCK:
        refresh_path()
        if (not refresh and _ENV is not None and _KEY is not None and os.environ.get("PATH", "") == _KEY[0]
                and time.monotonic() - _LOCATED_AT < RELOCATE_SECONDS):
            return _ENV
        where = _locate_all()
        _LOCATED_AT = time.monotonic()
        key = (os.environ.get("PATH", ""), tuple(where.items()))  # bulma Graphviz klasörünü eklemiş olabilir
        if refresh or _ENV is None or key != _KEY:
            _ENV, _KEY = _measure(where), key
        return _ENV


def catalog() -> Path | None:
    """Ortak kaynak kataloğu (%USERPROFILE%\\.jev\\kaynaklar\\KATALOG.md) varsa yolu."""
    p = jev_home() / "kaynaklar" / "KATALOG.md"
    return p if p.is_file() else None


def catalog_topics(path: Path, limit: int = 12) -> str:
    """Kataloğun bölüm başlıkları ("Harita verisi · Yazı tipleri · …"); katalog büyüdükçe kendiliğinden güncel."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    heads = [re.sub(r"^\d+[.)]\s*|\s*\([^)]*\)", "", ln[3:].strip()) for ln in text.splitlines()
             if ln.startswith("## ")]
    return " · ".join(h for h in heads[:limit] if h)


def remotion_browser() -> str | None:
    """Ortak npm deposundaki Remotion tarayıcısı (chrome-headless-shell) varsa yolu."""
    base = jev_home() / "kaynaklar" / "npm" / "node_modules" / ".remotion" / "chrome-headless-shell"
    exe = "chrome-headless-shell.exe" if os.name == "nt" else "chrome-headless-shell"
    found = sorted(glob.glob(str(base / "*" / "*" / exe)))
    return found[0] if found else None


def environment_text() -> str:
    """Promptlardaki ORTAM: temel satır, kurulu araçlar, Python paketleri ve (varsa) ortak kaynaklar."""
    e = environment()

    def tool(label: str, v: str | None) -> str:
        return f"{label} {v}" if v else f"{label} kurulu değil"
    lines = [" · ".join([e["os"], e["shell"], tool("Python", e["python"]), tool("Node", e["node"]),
                         tool("Git", e["git"]), tool("ffmpeg", e["ffmpeg"]),
                         "winget var" if e["winget"] else "winget yok"])]
    if e["araclar"]:
        lines.append("Kurulu araçlar: " + " · ".join(a["metin"] for a in e["araclar"]))
    if e["eksik"]:
        lines.append("Kurulu değil: " + ", ".join(e["eksik"]))
    if e["paketler"]:
        lines.append("Python paketleri: " + ", ".join(e["paketler"]))
    cat = catalog()
    if cat:
        topics = catalog_topics(cat)
        lines.append(f"Ortak kaynaklar (salt okunur; görsel, video, harita ve belge işlerinde önce bunu oku, araçların "
                     f"tarifleri ve bilinen sorunları da orada): {cat}" + (f" — {topics}" if topics else ""))
        shell = remotion_browser()
        if shell:
            lines.append(f"Remotion için hazır tarayıcı: {shell} (--browser-executable ile ver; Remotion ayrıca "
                         "indirmesin)")
    return "\n".join(lines)
