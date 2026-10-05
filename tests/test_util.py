"""Yardımcılar: Türkçe ekler, süre, slug, kısaltma, zaman, JSON/JSONL, atomik yazma, süreç kontrolü."""
import datetime as dt
import os
import subprocess
import sys
import unittest
from pathlib import Path

import _ortak
from jev.util import (append_jsonl, atomic_write_json, atomic_write_text, clock, ek, ends_mid_line, expand_path, iso,
                      jev_home, local_hhmm, parse_iso, pid_alive, read_json, read_jsonl, shorten, slugify, suffix,
                      tail_file, tail_lines)

UTC = dt.timezone.utc


class SuffixTests(unittest.TestCase):
    def test_names(self):
        cases = [("Luna", "e", "Luna'ya"), ("Sol", "e", "Sol'a"), ("Sonnet", "e", "Sonnet'e"),
                 ("Opus", "in", "Opus'un"), ("Astra", "in", "Astra'nın"), ("Jev", "in", "Jev'in"),
                 ("Luna", "de", "Luna'da"), ("Sonnet", "den", "Sonnet'ten"), ("Opus", "den", "Opus'tan"),
                 ("Luna", "in", "Luna'nın")]
        for word, kind, expected in cases:
            self.assertEqual(ek(word, kind), expected, (word, kind))

    def test_times_and_numbers_use_spoken_form(self):
        cases = [("08:30", "de", "08:30'da"), ("12:00", "de", "12:00'de"), ("09:40", "de", "09:40'ta"),
                 ("10:05", "e", "10:05'e"), ("3", "den", "3'ten"), ("6", "in", "6'nın"), ("0", "e", "0'a"),
                 ("100", "de", "100'de"), ("1000", "de", "1000'de"), ("20:00", "e", "20:00'ye")]
        for word, kind, expected in cases:
            self.assertEqual(ek(word, kind), expected, (word, kind))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            suffix("Luna", "x")


class TextTests(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(clock(38), "0:38")
        self.assertEqual(clock(600), "10:00")
        self.assertEqual(clock(3725), "1:02:05")
        self.assertEqual(clock(59.9), "0:59")
        self.assertEqual(clock(-5), "0:00")

    def test_slugify(self):
        self.assertEqual(slugify("Yapılacaklar listesi CLI tasarla"), "yapilacaklar-listesi-cli-tasarla")
        self.assertEqual(slugify("İstanbul Şehir Rehberi — ÇĞÖÜ"), "istanbul-sehir-rehberi-cgou")
        self.assertEqual(slugify("Café naïve"), "cafe-naive")
        self.assertEqual(slugify("!!!"), "proje")
        self.assertEqual(slugify("a" * 50), "a" * 40)
        self.assertEqual(slugify("abc def", max_len=4), "abc")
        long_request = "Python için sıcaklık ve uzunluk birim dönüştürücü CLI tasarla"
        self.assertEqual(slugify(long_request), "python-icin-sicaklik-ve-uzunluk-birim")  # kelime sınırında
        self.assertEqual(slugify(long_request, 24), "python-icin-sicaklik-ve")
        self.assertEqual(slugify("kisa " + "b" * 60), "kisa-" + "b" * 35)  # sınır çok gerideyse kelime kesilir

    def test_shorten(self):
        self.assertEqual(shorten("a  b\n c", 10), "a b c")
        self.assertEqual(shorten("abcdef", 4), "abc…")
        self.assertEqual(shorten("abcd", 4), "abcd")
        self.assertEqual(shorten(None, 5), "")

    def test_tail_lines(self):
        self.assertEqual(tail_lines("a\nb\nc", 2), "b\nc")
        self.assertEqual(tail_lines("a", 5), "a")


class TimeTests(unittest.TestCase):
    def test_iso_roundtrip(self):
        t = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        self.assertEqual(iso(t), "2026-01-02T03:04:05Z")
        self.assertEqual(parse_iso(iso(t)), t)
        plus3 = dt.datetime(2026, 1, 2, 6, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        self.assertEqual(iso(plus3), "2026-01-02T03:04:05Z")

    def test_parse_iso_variants(self):
        self.assertEqual(parse_iso("2026-01-02T03:04:05+03:00"), dt.datetime(2026, 1, 2, 0, 4, 5, tzinfo=UTC))
        self.assertEqual(parse_iso("2026-01-02T03:04:05"), dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
        for bad in ("", None, "dün akşam"):
            self.assertIsNone(parse_iso(bad))

    def test_local_hhmm(self):
        self.assertEqual(local_hhmm(None), "?")
        t = dt.datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        self.assertEqual(local_hhmm(t), t.astimezone().strftime("%H:%M"))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = _ortak.temp_dir("util")

    def test_atomic_write_text_keeps_lf_and_leaves_no_temp(self):
        p = self.dir / "a" / "b" / "not.txt"
        atomic_write_text(p, "satır 1\nsatır 2\n")
        self.assertEqual(p.read_bytes(), "satır 1\nsatır 2\n".encode("utf-8"))
        atomic_write_text(p, "yeni")
        self.assertEqual(p.read_text(encoding="utf-8"), "yeni")
        self.assertEqual([x.name for x in p.parent.iterdir()], ["not.txt"])

    def test_json(self):
        p = self.dir / "veri.json"
        atomic_write_json(p, {"ad": "Işık", "n": [1, 2]})
        text = p.read_text(encoding="utf-8")
        self.assertIn('"ad": "Işık"', text)
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(read_json(p), {"ad": "Işık", "n": [1, 2]})
        p.write_bytes(b"\xef\xbb\xbf" + '{"bom": "var"}'.encode("utf-8"))
        self.assertEqual(read_json(p), {"bom": "var"})
        p.write_text("{bozuk", encoding="utf-8")
        self.assertEqual(read_json(p, {"varsayılan": 1}), {"varsayılan": 1})
        self.assertIsNone(read_json(self.dir / "yok.json"))

    def test_jsonl(self):
        p = self.dir / "k" / "olaylar.jsonl"
        append_jsonl(p, {"a": 1})
        append_jsonl(p, {"b": "ğ"})
        with p.open("a", encoding="utf-8") as f:
            f.write("\n{yarım satır\n\n")
        append_jsonl(p, {"c": 3})
        self.assertEqual(read_jsonl(p), [{"a": 1}, {"b": "ğ"}, {"c": 3}])
        self.assertEqual(read_jsonl(self.dir / "yok.jsonl"), [])

    def test_jsonl_after_a_torn_line(self):
        p = self.dir / "karar.jsonl"
        self.assertFalse(ends_mid_line(p))  # dosya yok
        p.write_bytes(b"")
        self.assertFalse(ends_mid_line(p))  # boş
        append_jsonl(p, {"a": 1})
        self.assertFalse(ends_mid_line(p))
        with p.open("ab") as f:  # yazan süreç satırın ortasında öldü (çok baytlı harfin yarısında)
            f.write('{"b": "yarım ş'.encode("utf-8")[:-1])
        self.assertTrue(ends_mid_line(p))
        append_jsonl(p, {"c": "ğ"})  # yeni kayıt yarım satıra yapışmaz, kaybolmaz
        self.assertEqual(read_jsonl(p), [{"a": 1}, {"c": "ğ"}])
        self.assertFalse(ends_mid_line(p))

    def test_tail_file(self):
        p = self.dir / "gunluk.txt"
        p.write_text("".join(f"satır {i}\n" for i in range(100)), encoding="utf-8")
        self.assertEqual(tail_file(p, 2), "satır 98\nsatır 99")
        self.assertEqual(tail_file(p, 1000, max_bytes=20).splitlines()[-1], "satır 99")
        self.assertLess(len(tail_file(p, 1000, max_bytes=20)), 25)
        self.assertEqual(tail_file(self.dir / "yok.txt"), "")


class EnvTests(unittest.TestCase):
    def test_jev_home_follows_env(self):
        self.assertEqual(jev_home(), _ortak.HOME)
        self.assertTrue(_ortak.HOME.is_dir())

    def test_expand_path(self):
        self.assertEqual(expand_path("~/x"), Path.home() / "x")
        os.environ["JEV_TEST_KLASOR"] = str(_ortak.HOME)
        try:
            self.assertEqual(expand_path("$JEV_TEST_KLASOR/y"), _ortak.HOME / "y")
        finally:
            del os.environ["JEV_TEST_KLASOR"]

    def test_pid_alive(self):
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(0))
        self.assertFalse(pid_alive(-1))
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(30)
        self.assertFalse(pid_alive(proc.pid))


if __name__ == "__main__":
    unittest.main()
