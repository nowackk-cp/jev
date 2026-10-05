"""Jev'in kurulum arka ucu (PEP 517 ve PEP 660): `pip install -e .` ve `pip install .` hiçbir şey indirmeden çalışır.

pyproject.toml bu dosyayı `backend-path` ile gösterir; setuptools gibi bir derleme aracı indirilmez. Yalnızca bu
depo için yazıldı: tek bir saf Python paketi (jev), çalışma zamanı bağımlılığı yok. Proje bilgileri pyproject.toml'un
[project] tablosundan, sürüm jev/__init__.py'deki __version__'dan okunur.

- Tekerlek (wheel): jev/ klasörünün tamamı (istemler, şemalar, sahte senaryo, ofis dosyaları dahil) ve dist-info.
- Düzenlenebilir kurulum: site-packages'a depodaki jev/ klasörünü gösteren küçük bir bulucu ve onu yükleyen .pth
  konur. Koddaki değişiklikler yeniden kurmadan geçerli olur. Depo kökü sys.path'e eklenmez: tests/ ve tools/
  başka programlara görünmez.
- Kaynak paketi (sdist): depo dosyaları ve PKG-INFO.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import tarfile
import time
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "jev"
SDIST_ITEMS = ["pyproject.toml", "README.md", ".gitattributes", "kur.ps1", "jev", "tests", "tools", "docs"]
SKIP_DIRS = {"__pycache__", ".git", ".jev"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # tekerlek içeriği her derlemede aynı olsun

FINDER = '''\
"""jev düzenlenebilir kurulumu (tools/paketle.py üretti): `import jev` depodaki klasörden yüklenir."""
import sys
from importlib.machinery import PathFinder

ROOT = {root!r}


class JevFinder:
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        return PathFinder.find_spec(fullname, [ROOT]) if fullname == {package!r} else None

    @classmethod
    def invalidate_caches(cls):
        pass


def install():
    if JevFinder not in sys.meta_path:
        sys.meta_path.append(JevFinder)
'''


# --- proje bilgisi ----------------------------------------------------------------------------

def _project() -> dict:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    p = dict(data["project"])
    init = (ROOT / PACKAGE / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M)
    if not m:
        raise RuntimeError(f"{PACKAGE}/__init__.py içinde __version__ bulunamadı.")
    p["version"] = m.group(1)
    return p


def _dist_info(p: dict) -> str:
    return f"{p['name']}-{p['version']}.dist-info"


def _metadata(p: dict) -> str:
    lines = ["Metadata-Version: 2.1", f"Name: {p['name']}", f"Version: {p['version']}"]
    if p.get("description"):
        lines.append(f"Summary: {p['description']}")
    if p.get("keywords"):
        lines.append(f"Keywords: {','.join(p['keywords'])}")
    lines += [f"Classifier: {c}" for c in p.get("classifiers", [])]
    if p.get("requires-python"):
        lines.append(f"Requires-Python: {p['requires-python']}")
    lines += [f"Requires-Dist: {d}" for d in p.get("dependencies", [])]
    body = ""
    if p.get("readme"):
        lines.append("Description-Content-Type: text/markdown; charset=UTF-8")
        body = (ROOT / p["readme"]).read_text(encoding="utf-8")
    return "\n".join(lines) + "\n\n" + body


def _dist_info_files(p: dict) -> dict[str, bytes]:
    di = _dist_info(p)
    files = {
        f"{di}/METADATA": _metadata(p).encode("utf-8"),
        f"{di}/WHEEL": (f"Wheel-Version: 1.0\nGenerator: jev-paketle {p['version']}\nRoot-Is-Purelib: true\n"
                        "Tag: py3-none-any\n").encode("utf-8"),
    }
    scripts = p.get("scripts") or {}
    if scripts:
        files[f"{di}/entry_points.txt"] = ("[console_scripts]\n"
                                           + "".join(f"{k} = {v}\n" for k, v in scripts.items())).encode("utf-8")
    return files


def _skipped(rel: Path) -> bool:
    return any(part in SKIP_DIRS for part in rel.parts) or rel.suffix in SKIP_SUFFIXES


def _tree(folder: Path) -> list[Path]:
    return [f for f in sorted(folder.rglob("*")) if f.is_file() and not _skipped(f.relative_to(ROOT))]


# --- tekerlek ---------------------------------------------------------------------------------

def _b64(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _write_wheel(directory: str, p: dict, payload: dict[str, bytes]) -> str:
    name = f"{p['name']}-{p['version']}-py3-none-any.whl"
    files = {**payload, **_dist_info_files(p)}
    record = f"{_dist_info(p)}/RECORD"
    lines = [f"{arc},sha256={_b64(hashlib.sha256(data).digest())},{len(data)}" for arc, data in files.items()]
    files[record] = ("\n".join(lines + [f"{record},,"]) + "\n").encode("utf-8")
    with zipfile.ZipFile(Path(directory) / name, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, data in files.items():
            info = zipfile.ZipInfo(arc, ZIP_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    p = _project()
    payload = {f.relative_to(ROOT).as_posix(): f.read_bytes() for f in _tree(ROOT / PACKAGE)}
    return _write_wheel(wheel_directory, p, payload)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    p = _project()
    finder = f"__editable___{p['name']}_finder"
    payload = {
        f"{finder}.py": FINDER.format(root=str(ROOT), package=PACKAGE).encode("utf-8"),
        f"__editable__.{p['name']}-{p['version']}.pth": f"import {finder}; {finder}.install()\n".encode("utf-8"),
    }
    return _write_wheel(wheel_directory, p, payload)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    p = _project()
    for arc, data in _dist_info_files(p).items():
        target = Path(metadata_directory) / arc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return _dist_info(p)


prepare_metadata_for_build_editable = prepare_metadata_for_build_wheel


# --- kaynak paketi ----------------------------------------------------------------------------

def build_sdist(sdist_directory, config_settings=None):
    p = _project()
    base = f"{p['name']}-{p['version']}"
    name = f"{base}.tar.gz"

    def add(tf: tarfile.TarFile, arc: str, data: bytes, mtime: float) -> None:
        info = tarfile.TarInfo(f"{base}/{arc}")
        info.size, info.mode, info.mtime = len(data), 0o644, int(mtime)
        tf.addfile(info, io.BytesIO(data))

    with tarfile.open(Path(sdist_directory) / name, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        add(tf, "PKG-INFO", _metadata(p).encode("utf-8"), time.time())
        for item in SDIST_ITEMS:
            path = ROOT / item
            for f in [path] if path.is_file() else _tree(path) if path.is_dir() else []:
                add(tf, f.relative_to(ROOT).as_posix(), f.read_bytes(), f.stat().st_mtime)
    return name


# --- derleme gereksinimleri: yok --------------------------------------------------------------

def get_requires_for_build_wheel(config_settings=None):
    return []


get_requires_for_build_editable = get_requires_for_build_sdist = get_requires_for_build_wheel
