"""Testlerin ortak yardımcıları. Her test modülü `jev`'den önce bunu içe aktarır.

İçe aktarıldığı anda JEV_HOME'u geçici bir klasöre yönlendirir: testler kullanıcının gerçek
%USERPROFILE%\\.jev klasörüne (jev.toml, kosular.json, kota.json) dokunmaz.

Git de kullanıcının genel ayarlarından (imza, kancalar, varsayılan dal) etkilenmesin diye boş bir genel
ayar dosyasıyla çalışır ve sistem ayarı kapatılır. Kullanıcının genel ayar dosyası değiştirilmez.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_BASE = Path(tempfile.mkdtemp(prefix="jev-test-"))
HOME = _BASE / "jev-home"
HOME.mkdir()
os.environ["JEV_HOME"] = str(HOME)
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.pop("TYPESAFE_API_KEY", None)  # testler gerçek Jev modeline (TypeSafe) asla çıkmaz
_GITCONFIG = _BASE / "gitconfig"
_GITCONFIG.write_text("", encoding="utf-8")
os.environ["GIT_CONFIG_GLOBAL"] = str(_GITCONFIG)
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

from jev.terminal import Terminal  # noqa: E402  (JEV_HOME ayarlandıktan sonra)


def rmtree_force(path: Path) -> None:
    """Salt okunur dosyaları (git nesneleri) da silen rmtree."""
    def onexc(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if Path(path).exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=onexc)
        else:  # 3.11: onexc yok; onerror aynı işi görür (üçüncü bağımsız değişken kullanılmıyor)
            shutil.rmtree(path, onerror=onexc)


atexit.register(rmtree_force, _BASE)


def temp_dir(prefix: str = "t") -> Path:
    """Test oturumu sonunda silinen yeni bir geçici klasör."""
    return Path(tempfile.mkdtemp(prefix=prefix + "-", dir=_BASE))


def slow(obj):
    """Tam kuru koşu gibi uzun testler (her biri ~20-30 sn): `JEV_HIZLI=1` iken atlanır."""
    return unittest.skipIf(os.environ.get("JEV_HIZLI") == "1", "JEV_HIZLI=1: uzun test atlandı")(obj)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietTerminal(Terminal):
    """Ekrana yazmayan terminal: satırları `lines` listesinde toplar."""

    def __init__(self, verbose: bool = False):
        super().__init__(color=False, verbose=verbose)
        self.lines: list[str] = []

    def line(self, text: str, color: str | None = None, stamp: bool | None = None) -> None:
        with self._lock:
            self.lines.append(text)

    def text(self) -> str:
        return "\n".join(self.lines)


def clear_index() -> None:
    """Koşu dizinini (kosular.json) boşaltır; find_run testleri birbirini etkilemesin."""
    p = HOME / "kosular.json"
    if p.exists():
        p.unlink()


def make_config(**overrides):
    from jev.config import load_config
    return load_config(overrides=overrides or None)


def make_run(request: str = "Yapılacaklar listesi CLI tasarla", *, project: Path | None = None, cfg=None,
             speed: float = 50.0, approval: bool = False, **runner_kw):
    """Kuru koşu (mock ajanlar) için Runner: arayüz ve tarayıcı kapalı, rapordan sonra çıkar."""
    from jev.runner import create_run
    cfg = cfg or make_config()
    project = project or (temp_dir("proje") / "proj")
    kw = dict(ui=False, open_browser=False, exit_after=True)
    kw.update(runner_kw)
    term = kw.pop("term", None) or QuietTerminal()
    return create_run(cfg, request, project_arg=str(project), approval=approval, dry=True, speed=speed,
                      term=term, **kw)


def planned_run(request: str = "Yapılacaklar listesi CLI tasarla", **kw):
    """Planı ve görev kartları yazılmış kuru koşu (aşama: executing; görevler T01–T06, hepsi 'pending').

    İş bitince `run.bus.close()` çağrılmalı (events.jsonl açık kalmasın)."""
    from jev.phases.plan import run_plan
    kw.setdefault("speed", 1000.0)
    run = make_run(request, **kw)
    run_plan(run)
    return run
