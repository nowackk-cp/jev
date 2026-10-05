"""Makinenin ortamı (jev/machine.py) ve KALİTE kuralı: araçları bulma, sürümü programı çalıştırmadan okuma, önbellek,
ortak kaynak kataloğu ve promptlar."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _ortak
from jev import context, guard, machine

IS_NT = os.name == "nt"
NOTEPAD = os.path.expandvars(r"%SystemRoot%\System32\notepad.exe")
FAKE_ENV = {"os": "Windows", "shell": "PowerShell", "python": "3.13", "node": None, "git": None, "ffmpeg": None,
            "winget": False, "araclar": [], "eksik": [], "paketler": []}


def fake_tool(folder: Path, name: str, line: str) -> Path:
    """Her çağrıda `line` yazan sahte komut satırı aracı."""
    folder.mkdir(parents=True, exist_ok=True)
    if IS_NT:
        p = folder / f"{name}.bat"
        p.write_text(f"@echo {line}\r\n", encoding="ascii")
    else:
        p = folder / name
        p.write_text(f"#!/bin/sh\necho '{line}'\n", encoding="ascii")
        p.chmod(0o755)
    return p


class Isolated(unittest.TestCase):
    """Boş önbellekle başlar; PATH, önbellek ve araç tablosu test sonunda eski haline döner."""

    def setUp(self):
        self.dir = _ortak.temp_dir("ortam")
        for p in (mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}),
                  mock.patch.object(machine, "_ENV", None), mock.patch.object(machine, "_KEY", None),
                  mock.patch.object(machine, "_LOCATED_AT", 0.0),
                  mock.patch.object(machine, "_py_packages", return_value=["numpy", "pillow"])):
            p.start()
            self.addCleanup(p.stop)

    def tools(self, *tools: dict):
        return mock.patch.object(machine, "TOOLS", tools)


@unittest.skipUnless(IS_NT, "sürüm bilgisi Windows'a özgü")
class FileVersionTests(unittest.TestCase):
    def test_reads_version_without_running(self):
        self.assertRegex(machine._file_version(NOTEPAD) or "", r"^\d+\.\d+(\.\d+){0,2}$")

    def test_not_a_program(self):
        p = _ortak.temp_dir("surum") / "metin.txt"
        p.write_text("sürüm bilgisi yok", encoding="utf-8")
        self.assertIsNone(machine._file_version(str(p)))
        self.assertIsNone(machine._file_version(str(p.parent / "yok.exe")))


class ToolTests(Isolated):
    def test_found_missing_and_path(self):
        cli = fake_tool(self.dir / "araclar", "jevsahte", "jevsahte 9.8.7")
        pdir = self.dir / "path_e"
        fake_tool(pdir, "jevyollu", "jevyollu surum 1.2.3")
        with self.tools(
                {"ad": "Sahte", "komut": "jevsahte", "tur": "cli", "surum": ("--version",), "kelime": 1,
                 "yerler": (str(cli),)},
                {"ad": "Yollu", "komut": "jevyollu", "tur": "cli", "surum": ("--version",), "kelime": 2,
                 "path_e_ekle": True, "yerler": (str(pdir / "jevyollu*"),)},
                {"ad": "Yok Araç", "komut": "jevyokarac", "tur": "cli", "surum": ("--version",), "kelime": 1,
                 "yerler": (str(self.dir / "yok" / "*.exe"), r"%JEV_TANIMSIZ_DEGISKEN%\jevyokarac.exe")}):
            text = machine.environment_text()
        self.assertIn(f'Sahte 9.8.7 ("{cli}")', text)  # PATH'te değil: tam yoluyla
        self.assertIn("Yollu 1.2.3 (jevyollu)", text)  # klasörü PATH'e eklendi: adıyla
        self.assertIn(os.path.normcase(str(pdir)),
                      [os.path.normcase(p) for p in os.environ["PATH"].split(os.pathsep)])
        self.assertIn("\nKurulu değil: Yok Araç", text)
        self.assertIn("\nPython paketleri: numpy, pillow", text)
        self.assertTrue(text.splitlines()[0].startswith(machine.environment()["os"]))

    @unittest.skipUnless(IS_NT, "sürüm bilgisi Windows'a özgü")
    def test_window_program_not_run(self):
        """Pencereli program çalıştırılmaz: sürüm dosyadan okunur, PATH'te değilse tam yolu verilir."""
        with self.tools({"ad": "Not Defteri", "komut": "jevnotdefteriyok", "tur": "gui",
                         "yerler": (r"%SystemRoot%\System32\notepad.exe",)}), \
                mock.patch.object(machine, "_run", wraps=machine._run) as run:
            (a,) = machine.environment()["araclar"]
        self.assertFalse([c for c in run.call_args_list if "notepad" in str(c.args[0][0]).lower()])
        self.assertRegex(a["surum"], r"^\d+\.\d+")
        self.assertEqual(a["metin"], f'Not Defteri {a["surum"]} ("{NOTEPAD}")')

    def test_latex_engines(self):
        tex = self.dir / "MiKTeX" / "bin"
        for name in ("xelatex", "pdflatex"):
            fake_tool(tex, name, "")
        ext = ".bat" if IS_NT else ""
        with self.tools({"ad": "LaTeX", "komut": "jevxelatexyok", "tur": "var",
                         "kardesler": ("xelatex", "lualatex", "pdflatex"), "dagitimlar": {"miktex": "MiKTeX"},
                         "yerler": (str(tex / f"xelatex{ext}"),)}):
            (a,) = machine.environment()["araclar"]
        self.assertEqual(a["metin"], f'LaTeX (MiKTeX: xelatex, pdflatex; klasör "{tex}")')


class CacheTests(Isolated):
    def test_measured_once_until_path_changes(self):
        with self.tools(), mock.patch.object(machine, "_measure", side_effect=lambda where: {"where": where}) as m:
            first = machine.environment()
            self.assertIs(machine.environment(), first)
            self.assertEqual(m.call_count, 1)
            os.environ["PATH"] += os.pathsep + str(self.dir)  # ör. bir işçi winget ile araç kurdu
            second = machine.environment()
            self.assertIsNot(second, first)
            self.assertIsNot(machine.environment(refresh=True), second)
            self.assertEqual(m.call_count, 3)

    def test_program_installed_off_path_noticed(self):
        target = self.dir / "sonra"
        with self.tools({"ad": "Sonradan", "komut": "jevsonradan", "tur": "var",
                         "yerler": (str(target / "jevsonradan*"),)}), \
                mock.patch.object(machine, "_measure", side_effect=lambda where: {"where": dict(where)}):
            self.assertIsNone(machine.environment()["where"]["Sonradan"][0])
            fake_tool(target, "jevsonradan", "x")
            self.assertIsNone(machine.environment()["where"]["Sonradan"][0])  # süre dolmadı: önbellekten
            machine._LOCATED_AT -= machine.RELOCATE_SECONDS + 1
            self.assertIsNotNone(machine.environment()["where"]["Sonradan"][0])


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.base = machine.jev_home() / "kaynaklar"
        _ortak.rmtree_force(self.base)
        self.addCleanup(_ortak.rmtree_force, self.base)
        p = mock.patch.object(machine, "environment", return_value=FAKE_ENV)
        p.start()
        self.addCleanup(p.stop)

    def test_without_catalog(self):
        self.assertIsNone(machine.catalog())
        self.assertEqual(machine.environment_text(), "Windows · PowerShell · Python 3.13 · Node kurulu değil · "
                                                     "Git kurulu değil · ffmpeg kurulu değil · winget yok")
        rule = context.quality_rule()
        self.assertTrue(rule.startswith("KALİTE: "))
        self.assertNotIn("kataloğ", rule)

    def test_with_catalog(self):
        self.base.mkdir(parents=True)
        cat = self.base / "KATALOG.md"
        cat.write_text("# Katalog\n## Kurallar\n## 1. Harita verisi\n## 2. Yazı tipleri (SIL OFL 1.1)\n### alt\n",
                       encoding="utf-8")
        exe = "chrome-headless-shell.exe" if IS_NT else "chrome-headless-shell"
        shell = (self.base / "npm" / "node_modules" / ".remotion" / "chrome-headless-shell" / "win64"
                 / "chrome-headless-shell-win64" / exe)
        shell.parent.mkdir(parents=True)
        shell.write_bytes(b"")
        self.assertEqual(machine.catalog(), cat)
        lines = machine.environment_text().splitlines()
        self.assertTrue(lines[1].startswith("Ortak kaynaklar (salt okunur"), lines)
        self.assertTrue(lines[1].endswith(f"{cat} — Kurallar · Harita verisi · Yazı tipleri"), lines)
        self.assertTrue(lines[2].startswith(f"Remotion için hazır tarayıcı: {shell} "), lines)
        self.assertIn(f"ortak kaynak kataloğunu oku ({cat})", context.quality_rule())


class PromptTests(unittest.TestCase):
    """KALİTE kuralı plan (görev kartlarıyla birlikte), tek görevli iş ve kart işçisi promptlarına girer; son kontrol
    kaliteyi de ölçer."""

    def test_rule_in_prompts(self):
        cfg, tmp = _ortak.make_config(), _ortak.temp_dir("istem")
        st = {"request": "Osmanlı tarihi videosu", "tasks": []}
        task = {"id": "T01", "title": "Harita sahnesi"}
        prompts = {"plan": context.plan_prompt("opus", st["request"], tmp, True, "", st),
                   "mini": context.mini_prompt(cfg, st, task, "sol", tmp, tmp),
                   "isci": context.worker_prompt(cfg, st, {}, task, "sol", tmp)}
        for name, text in prompts.items():
            self.assertIn("\n- KALİTE: Sonuç, alanının iyi bir uzmanının", text, name)
            self.assertNotIn("kalite_kurali", text, name)
        # ezberden doğru üretilemeyecek bilgiye araştırma kartı; veri hazırlığı ve görsel öz kontrol ilgili karta
        self.assertIn("için bir araştırma kartı aç", prompts["plan"])
        self.assertIn("görsel öz kontrolü ilgili kartın", prompts["plan"])
        self.assertIn("Kaliteyi de denetle", context.template("denetim"))

    def test_temp_allowed_like_the_guard(self):
        """KALİTE kuralı kareleri %TEMP%'e çıkarttırır; koruma %TEMP%'e izin verir, promptlar da bununla çelişmez."""
        allowed = "geçici dosyalar için %TEMP% serbest"
        for name in ("isci_gorev", "isci_mini"):
            self.assertIn(allowed, context.template(name), name)
        self.assertIn(allowed, context.agents_md_section({}))
        project = _ortak.temp_dir("koruma")
        self.assertIsNone(guard.classify_write(str(Path(tempfile.gettempdir()) / "kare_12.png"), project, project))

    def test_block_dropped_when_empty(self):
        text = context.render("- a\n{{#kalite_kurali}}\n- {{kalite_kurali}}\n{{/kalite_kurali}}\n- b\n",
                              {"kalite_kurali": ""})
        self.assertEqual(text, "- a\n- b\n")


if __name__ == "__main__":
    unittest.main()
