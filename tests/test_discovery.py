"""CLI bulma (jev/discovery.py): ayardaki yol, PATH, bilinen klasörler ve paketli (MSIX) uygulamaların gerçek klasörleri.

Gerçek kullanıcı klasörlerine bakılmaz: ev, %APPDATA% ve %LOCALAPPDATA% geçici bir klasöre yönlendirilir.
"""
import os
import unittest
from pathlib import Path
from unittest import mock

import _ortak
from jev.discovery import find_cli

CLAUDE_PACKAGE = Path("Packages") / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming"


def touch(path: Path, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class FindCliTests(unittest.TestCase):
    def setUp(self):
        self.home = _ortak.temp_dir("ev")
        self.local = self.home / "AppData" / "Local"
        self.roaming = self.home / "AppData" / "Roaming"
        for p in (mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.local), "APPDATA": str(self.roaming)}),
                  mock.patch.object(Path, "home", return_value=self.home),
                  mock.patch("jev.discovery.shutil.which", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def claude(base: Path, version: str) -> Path:
        return touch(base / "Claude" / "claude-code" / version / "claude.exe")

    def test_claude_of_a_packaged_desktop_app(self):
        # Kullanıcının kendi terminali %APPDATA%\Claude'u görmez: dosya paketin LocalCache klasöründedir.
        exe = self.claude(self.local / CLAUDE_PACKAGE, "2.1.281")
        touch(self.local / "Packages" / "Baska.Uygulama_abc" / "LocalCache" / "Roaming" / "ayar.json")
        info = find_cli("claude")
        self.assertEqual((info.path, info.source), (exe, "bilinen klasör"))

    def test_highest_claude_version_and_the_package_copy_first(self):
        self.claude(self.roaming, "2.1.9")
        new = self.claude(self.local / CLAUDE_PACKAGE, "2.1.281")
        self.assertEqual(find_cli("claude").path, new)  # sayıyla karşılaştırılır: 281 > 9
        # Uygulamanın içinden bakınca aynı sürüm iki yerde görünür; her terminalde çalışan paket yolu seçilir.
        self.claude(self.roaming, "2.1.281")
        self.assertEqual(find_cli("claude").path, new)

    def test_newest_codex_wins_wherever_it_is(self):
        real = touch(self.local / "OpenAI" / "Codex" / "bin" / "aaa" / "codex.exe", mtime=1_000_000)
        packaged = touch(self.local / "Packages" / "OpenAI.Codex_xyz" / "LocalCache" / "Local" / "OpenAI" / "Codex"
                         / "bin" / "bbb" / "codex.exe", mtime=2_000_000)
        self.assertEqual(find_cli("codex").path, packaged)
        os.utime(real, (3_000_000, 3_000_000))
        self.assertEqual(find_cli("codex").path, real)

    def test_setting_then_path_then_known_folders(self):
        known = self.claude(self.roaming, "2.1.281")
        self.assertEqual(find_cli("claude").path, known)
        with mock.patch("jev.discovery.shutil.which", return_value=r"C:\araclar\claude.cmd"):
            info = find_cli("claude")
            self.assertEqual((info.path, info.source), (Path(r"C:\araclar\claude.cmd"), "PATH"))
            own = touch(self.home / "benim" / "claude.exe")
            info = find_cli("claude", str(own))
            self.assertEqual((info.path, info.source), (own, "ayar"))
            info = find_cli("claude", str(self.home / "yok.exe"))  # yanlış ayar başka bir yere sessizce düşmez
            self.assertEqual((info.path, info.source), (None, "bulunamadı"))
        known.unlink()
        native = touch(self.home / ".local" / "bin" / "claude.exe")
        self.assertEqual(find_cli("claude").path, native)
        native.unlink()
        self.assertEqual((find_cli("claude").path, find_cli("claude").source), (None, "bulunamadı"))


if __name__ == "__main__":
    unittest.main()
