"""Kurulum arka ucu (tools/paketle.py): tekerlek, düzenlenebilir tekerlek, sdist, üst veri ve kurulum dosyaları.

pip çalıştırılmaz: arka ucun kancaları doğrudan çağrılır, çıktılar geçici klasörde incelenir.
"""
import base64
import hashlib
import importlib.util
import tarfile
import tomllib
import types
import unittest
import zipfile
from pathlib import Path

import _ortak
from jev import __version__

ROOT = _ortak.ROOT
DIST = f"jev-{__version__}"
DIST_INFO = f"{DIST}.dist-info"


def load_backend() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("paketle", ROOT / "tools" / "paketle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


paketle = load_backend()


def source_files(folder: str) -> set[str]:
    """Depodaki bir klasörün pakete girmesi gereken dosyaları (önbellekler hariç)."""
    return {f.relative_to(ROOT).as_posix() for f in (ROOT / folder).rglob("*")
            if f.is_file() and not {"__pycache__", ".git", ".jev"} & set(f.parts) and f.suffix not in (".pyc", ".pyo")}


def check_record(test: unittest.TestCase, zf: zipfile.ZipFile) -> None:
    """RECORD tekerlekteki her dosyayı doğru sha256 ve boyutla listeliyor mu?"""
    lines = zf.read(f"{DIST_INFO}/RECORD").decode("utf-8").splitlines()
    test.assertEqual(sorted(line.rsplit(",", 2)[0] for line in lines), sorted(zf.namelist()))
    for line in lines:
        arc, digest, size = line.rsplit(",", 2)
        if arc == f"{DIST_INFO}/RECORD":
            test.assertEqual((digest, size), ("", ""))
            continue
        data = zf.read(arc)
        algo, want = digest.split("=", 1)
        got = base64.urlsafe_b64encode(hashlib.new(algo, data).digest()).rstrip(b"=").decode("ascii")
        test.assertEqual((algo, got, int(size)), ("sha256", want, len(data)), arc)


class WheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _ortak.temp_dir("tekerlek")
        cls.name = paketle.build_wheel(str(cls.out))
        cls.zf = zipfile.ZipFile(cls.out / cls.name)

    @classmethod
    def tearDownClass(cls):
        cls.zf.close()

    def test_whole_package_with_its_data_files(self):
        self.assertEqual(self.name, f"{DIST}-py3-none-any.whl")
        names = set(self.zf.namelist())
        self.assertEqual({n for n in names if not n.startswith(DIST_INFO + "/")}, source_files("jev"))
        self.assertEqual({n for n in names if n.startswith(DIST_INFO + "/")},
                         {f"{DIST_INFO}/{n}" for n in ("METADATA", "WHEEL", "entry_points.txt", "RECORD")})
        for data_file in ("jev/varsayilan.toml", "jev/kanca.py", "jev/mock/senaryo_varsayilan.json",
                          "jev/office/static/index.html"):
            self.assertIn(data_file, names)
        for folder in ("jev/prompts/", "jev/schemas/", "jev/mock/sablonlar/"):
            self.assertTrue(any(n.startswith(folder) for n in names), folder)

    def test_record(self):
        check_record(self, self.zf)

    def test_dist_info(self):
        self.assertEqual(self.zf.read(f"{DIST_INFO}/entry_points.txt").decode("utf-8"),
                         "[console_scripts]\njev = jev.cli:run\n")
        wheel = self.zf.read(f"{DIST_INFO}/WHEEL").decode("utf-8").splitlines()
        self.assertLessEqual({"Wheel-Version: 1.0", "Root-Is-Purelib: true", "Tag: py3-none-any"}, set(wheel))
        head, body = self.zf.read(f"{DIST_INFO}/METADATA").decode("utf-8").split("\n\n", 1)
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertLessEqual({"Name: jev", f"Version: {__version__}", f"Summary: {project['description']}",
                              f"Requires-Python: {project['requires-python']}",
                              "Description-Content-Type: text/markdown; charset=UTF-8"}, set(head.splitlines()))
        self.assertNotIn("Requires-Dist", head)  # çalışma zamanı bağımlılığı yok
        self.assertEqual(body, (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_same_bytes_on_every_build(self):
        again = _ortak.temp_dir("tekerlek")
        self.assertEqual((again / paketle.build_wheel(str(again))).read_bytes(), (self.out / self.name).read_bytes())

    def test_prepared_metadata_matches_the_wheel(self):
        out = _ortak.temp_dir("ustveri")
        self.assertEqual(paketle.prepare_metadata_for_build_wheel(str(out)), DIST_INFO)
        self.assertIs(paketle.prepare_metadata_for_build_editable, paketle.prepare_metadata_for_build_wheel)
        for f in ("METADATA", "WHEEL", "entry_points.txt"):
            self.assertEqual((out / DIST_INFO / f).read_bytes(), self.zf.read(f"{DIST_INFO}/{f}"), f)


class EditableTests(unittest.TestCase):
    def test_finder_exposes_only_jev_from_the_repo(self):
        out = _ortak.temp_dir("duzenlenebilir")
        name = paketle.build_editable(str(out))
        self.assertEqual(name, f"{DIST}-py3-none-any.whl")
        with zipfile.ZipFile(out / name) as zf:
            check_record(self, zf)
            payload = sorted(n for n in zf.namelist() if not n.startswith(DIST_INFO + "/"))
            self.assertEqual(payload, [f"__editable__.{DIST}.pth", "__editable___jev_finder.py"])
            self.assertEqual(zf.read(f"__editable__.{DIST}.pth").decode("utf-8"),
                             "import __editable___jev_finder; __editable___jev_finder.install()\n")
            source = zf.read("__editable___jev_finder.py").decode("utf-8")
        finder = types.ModuleType("__editable___jev_finder")
        exec(compile(source, "__editable___jev_finder.py", "exec"), finder.__dict__)  # install() çağrılmaz
        self.assertEqual(Path(finder.ROOT), ROOT)
        self.assertEqual(Path(finder.JevFinder.find_spec("jev").origin), ROOT / "jev" / "__init__.py")
        for other in ("tests", "tools", "paketle", "_ortak"):  # depo kökü sys.path'e girmez
            self.assertIsNone(finder.JevFinder.find_spec(other), other)


class SdistTests(unittest.TestCase):
    def test_sdist(self):
        out = _ortak.temp_dir("sdist")
        name = paketle.build_sdist(str(out))
        self.assertEqual(name, f"{DIST}.tar.gz")
        with tarfile.open(out / name) as tf:
            members = tf.getnames()
            pkg_info = tf.extractfile(f"{DIST}/PKG-INFO").read().decode("utf-8")
        self.assertTrue(all(m.startswith(DIST + "/") for m in members))
        expected = {"PKG-INFO", "pyproject.toml", "README.md", ".gitattributes", "kur.ps1"}
        for folder in ("jev", "tests", "tools", "docs"):
            expected |= source_files(folder)
        self.assertEqual({m[len(DIST) + 1:] for m in members}, expected)
        self.assertEqual(pkg_info, paketle._metadata(paketle._project()))


class BuildSystemTests(unittest.TestCase):
    def test_nothing_to_download(self):
        build = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["build-system"]
        self.assertEqual(build, {"requires": [], "build-backend": "paketle", "backend-path": ["tools"]})
        for hook in ("get_requires_for_build_wheel", "get_requires_for_build_editable", "get_requires_for_build_sdist"):
            self.assertEqual(getattr(paketle, hook)(), [], hook)

    def test_kur_ps1_is_utf8_with_bom_and_crlf(self):
        # PowerShell 5.1 BOM'suz UTF-8 betiği ANSI kod sayfasıyla okur: Türkçe metin bozulur.
        data = (ROOT / "kur.ps1").read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        text = data[3:].decode("utf-8")
        self.assertIn("-m pip install --no-index", text)
        self.assertIn("[switch]$YolaEkle", text)
        self.assertIn("*.ps1 text eol=crlf", (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()
