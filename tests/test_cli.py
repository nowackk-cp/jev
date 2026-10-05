"""Komut satırı (§5.12): seçenek ayrıştırma, çıkış kodları, etkileşimli mod, alt komutlar ve ekran biçimleri.

Model çağrısı yapılmaz ve tarayıcı açılmaz: yeni koşu testlerinde koşu oluşturma yakalanır; koşu gerektiren alt
komutlar planı yazılmış bir kuru koşunun kayıtlarıyla çalışır; `jev ajanlar` CLI'ları aramaz, sürümlerini sormaz.
"""
import contextlib
import copy
import datetime as dt
import io
import os
import types
import unittest
from pathlib import Path
from unittest import mock

import _ortak
from jev import __version__, cli
from jev import runner as runner_mod
from jev.adapters.base import AgentResult
from jev.config import ConfigError
from jev.context import display
from jev.discovery import CliInfo
from jev.gateway import Gateway
from jev.gitops import GitError
from jev.phases.review import report_name
from jev.quota import QuotaBook
from jev.runner import ACTIVE_PHASES, SetupError, parse_command
from jev.state import LockError, index_update, jev_dir, load_state, run_dir, save_state
from jev.util import atomic_write_json, atomic_write_text, ek, iso, local_hhmm, now

HELP = cli.HELP_TEXT + "\n"
HINT = "Yardım için: jev --yardim"
LIVE_PID = 4242  # testlerde "başka bir jev süreci" sayılan PID


def header(text: str) -> str:
    return "━" * 8 + " " + text + " " + "━" * 8


def run_main(*argv, tty: bool = False, inputs=()):
    """cli.main'i ekrana yazmadan çalıştırır. Girdi etkileşimli konsol sayılmaz (tty=True ile yalnızca `jev`
    komutu için sayılır; koşucu hiçbir zaman stdin okumaz); `inputs` input()'un sırayla döndüreceği yanıtlar."""
    terms: list[_ortak.QuietTerminal] = []

    def make_term(*_a, **_kw):
        terms.append(_ortak.QuietTerminal())
        return terms[-1]

    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(cli, "Terminal", side_effect=make_term))
        stack.enter_context(mock.patch.object(cli, "_stdin_is_tty", return_value=tty))
        stack.enter_context(mock.patch.object(runner_mod, "_stdin_is_tty", return_value=False))
        stack.enter_context(mock.patch("builtins.input", side_effect=list(inputs)))
        browser = stack.enter_context(mock.patch("webbrowser.open"))
        stack.enter_context(contextlib.redirect_stdout(out))
        code = cli.main([str(x) for x in argv])
    return types.SimpleNamespace(code=code, term=terms[0], lines=terms[0].lines, out=out.getvalue(),
                                 browser=browser)


class ParseArgsTests(unittest.TestCase):
    def test_defaults_and_request_words(self):
        self.assertEqual(cli.parse_args([]), cli.Args())
        words = ["Yapılacaklar", "listesi", "CLI", "tasarla"]
        self.assertEqual(cli.parse_args(words), cli.Args(positional=words))

    def test_flag_aliases(self):
        table = {
            "onay": ["--onay"], "kuru": ["--kuru"], "ayrintili": ["--ayrintili", "--ayrıntılı", "-v"],
            "arayuz_yok": ["--arayuz-yok", "--arayüz-yok"], "bitince_cik": ["--bitince-cik", "--bitince-çık"],
            "test": ["--test"], "surum": ["--surum", "--sürüm", "--version", "-V"],
            "yardim": ["-h", "--help", "--yardim", "--yardım"],
        }
        self.assertEqual(set(cli._FLAGS), {n for names in table.values() for n in names})  # tablo eksiksiz
        for attr, names in table.items():
            for name in names:
                with self.subTest(name=name):
                    self.assertEqual(cli.parse_args([name]), cli.Args(**{attr: True}))

    def test_long_options_ignore_case_short_ones_do_not(self):
        self.assertEqual(cli.parse_args(["--ONAY", "--Arayüz-Yok", "--PROJE", "d"]),
                         cli.Args(onay=True, arayuz_yok=True, proje="d"))
        self.assertEqual(cli.parse_args(["-v"]), cli.Args(ayrintili=True))
        self.assertEqual(cli.parse_args(["-V"]), cli.Args(surum=True))
        with self.assertRaisesRegex(cli.UsageError, r"^Bilinmeyen seçenek: -P$"):
            cli.parse_args(["-P", "d"])

    def test_valued_options(self):
        cases = [
            (["--proje", "C:/iş/proje"], {"proje": "C:/iş/proje"}),
            (["-p", "d"], {"proje": "d"}),
            (["-p=d"], {"proje": "d"}),
            (["--proje=d=e"], {"proje": "d=e"}),  # ilk '=' ayırır
            (["-p", "-x"], {"proje": "-x"}),  # değer '-' ile başlasa da değerdir
            (["--kuru-hiz", "5"], {"kuru_hiz": 5.0}),  # --kuru'yu cmd_new açar
            (["--kuru-hız=2,5"], {"kuru_hiz": 2.5}),
            (["--KURU-HIZ", "1000"], {"kuru_hiz": 1000.0}),
            (["--kuru-hiz", "0.01"], {"kuru_hiz": 0.01}),
        ]
        for argv, want in cases:
            with self.subTest(argv=argv):
                self.assertEqual(cli.parse_args(argv), cli.Args(**want))

    def test_errors(self):
        rng = "--kuru-hiz 0'dan büyük ve en fazla 1000 olmalı."
        cases = [
            (["--onay=evet"], "--onay değer almaz."),
            (["--Kuru=1"], "--Kuru değer almaz."),
            (["istek", "--proje"], "--proje bir değer bekliyor."),
            (["-p"], "-p bir değer bekliyor."),
            (["--proje="], "--proje boş olamaz."),
            (["--proje", "  "], "--proje boş olamaz."),
            (["--kuru-hiz", "hızlı"], "--kuru-hiz bir sayı bekliyor (ör. 5)."),
            (["--kuru-hiz="], "--kuru-hiz bir sayı bekliyor (ör. 5)."),
            (["--kuru-hiz", "0"], rng),
            (["--kuru-hiz", "-3"], rng),
            (["--kuru-hiz", "1000,5"], rng),
            (["--kuru-hiz", "nan"], rng),
            (["--kuru-hiz", "inf"], rng),
            (["--bilinmeyen"], "Bilinmeyen seçenek: --bilinmeyen"),
            (["--proj", "d"], "Bilinmeyen seçenek: --proj"),  # kısaltma yok
            (["-x=1"], "Bilinmeyen seçenek: -x=1"),
        ]
        for argv, msg in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(cli.UsageError) as cm:
                    cli.parse_args(argv)
                self.assertEqual(str(cm.exception), msg)

    def test_double_dash_and_single_dash(self):
        self.assertEqual(cli.parse_args(["--kuru", "--", "--onay", "-v", "--", "--proje"]),
                         cli.Args(positional=["--onay", "-v", "--", "--proje"], kuru=True))
        self.assertEqual(cli.parse_args(["-"]), cli.Args(positional=["-"]))
        self.assertEqual(cli.parse_args(["--kuru", "istek", "metni", "-p", "d", "--onay", "sonu"]),
                         cli.Args(positional=["istek", "metni", "sonu"], proje="d", kuru=True, onay=True))


class FormatTests(unittest.TestCase):
    def test_numbers(self):
        for x, want in [(1, "1"), (1.0, "1"), ("5", "5"), (2.5, "2.5"), (0.25, "0.25"), (1000.0, "1000")]:
            self.assertEqual(cli._fmt_num(x), want)

    def test_tokens(self):
        for n, want in [(None, "0"), (0, "0"), (999, "999"), (1000, "1.0k"), (1500, "1.5k"), (999_949, "999.9k"),
                        (999_950, "1.0M"), (1_000_000, "1.0M"), (2_500_000, "2.5M")]:
            with self.subTest(n=n):
                self.assertEqual(cli._fmt_tokens(n), want)

    def test_durations(self):
        for s, want in [(None, "0 sn"), (0, "0 sn"), (59.4, "59 sn"), (59.6, "1 dk 0 sn"), (61, "1 dk 1 sn"),
                        (3599, "59 dk 59 sn"), (3600, "1 sa 0 dk"), (3725, "1 sa 2 dk"), (90061, "25 sa 1 dk")]:
            with self.subTest(s=s):
                self.assertEqual(cli._fmt_duration(s), want)

    def test_resume_hint(self):
        rid = "20260928-101500-x"
        self.assertEqual(cli._resume_hint({"phase": "reported", "run_id": rid}),
                         f'Koşu kaydedildi; düzeltme için: jev duzelt {rid} "not" · izlemek için: jev ofis {rid}')
        self.assertEqual(cli._resume_hint({"phase": "aborted", "run_id": rid}), "Koşu iptal edilmişti.")
        for ph in ("paused", "executing", "planning", None):
            self.assertEqual(cli._resume_hint({"phase": ph, "run_id": rid}),
                             f"Koşu kaydedildi; sürdürmek için: jev devam {rid}")


class CommandWordTests(unittest.TestCase):
    def test_tables_match_handlers(self):
        self.assertEqual(set(cli.SUBCOMMANDS.values()), set(cli.HANDLERS) | {"yardim"})
        self.assertLessEqual(set(runner_mod.COMMANDS.values()),
                             {"duzelt", "durum", "rapor", "devam", "ofis", "cikis", "onayla", "reddet", "yardim"})

    def test_parse_command(self):
        cases = {
            None: ("", ""), "": ("", ""), "   ": ("", ""),
            "düzelt": ("duzelt", ""), "Düzelt eksik testler": ("duzelt", "eksik testler"),
            'DÜZELT  "tırnaklı not" ': ("duzelt", "tırnaklı not"), "duzelt 'tek tırnak'": ("duzelt", "tek tırnak"),
            'düzelt "yarım': ("duzelt", '"yarım'), 'düzelt "': ("duzelt", '"'),
            "ÇIKIŞ": ("cikis", ""), "çıkış": ("cikis", ""), "cikis": ("cikis", ""), "q": ("cikis", ""),
            "Exit": ("cikis", ""), "?": ("yardim", ""), "yardım": ("yardim", ""), "onayla": ("onayla", ""),
            "reddet": ("reddet", ""), "  durum  ": ("durum", ""), "rapor": ("rapor", ""), "devam": ("devam", ""),
            "ofis": ("ofis", ""), "sil her şeyi": ("?sil", "her şeyi"),
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(parse_command(line), want)

    def test_single_id(self):
        self.assertIsNone(cli._single_id([], "durum"))
        self.assertEqual(cli._single_id(["20260928"], "durum"), "20260928")
        with self.assertRaises(cli.UsageError) as cm:
            cli._single_id(["yapilacaklar", "listesi"], "rapor")
        self.assertEqual(str(cm.exception), "`jev rapor` en fazla bir koşu kimliği alır. Yeni bir istek başlatmak "
                                            "istiyorsan isteği tırnak içinde yaz: jev \"…\"")

    def test_split_run_and_note(self):
        _ortak.clear_index()
        self.addCleanup(_ortak.clear_index)
        index_update("yerel-kosu", project="C:/yok")
        cases = [
            ([], (None, "")),
            (["20260928-101500-yapilacaklar", "eksik", "testler"], ("20260928-101500-yapilacaklar", "eksik testler")),
            (["20260928"], ("20260928", "")),
            ([" 20260928-1015 ", " silmeden önce sor "], ("20260928-1015", "silmeden önce sor")),
            (["yerel-kosu", "not"], ("yerel-kosu", "not")),  # dizindeki kimlik
            (["eksik", "testler"], (None, "eksik testler")),
            (["Silmeden önce onay sor"], (None, "Silmeden önce onay sor")),
            (["20260928 tarihli testler kırık"], (None, "20260928 tarihli testler kırık")),  # tırnaklı not
            (["12345678x", "düzelt"], (None, "12345678x düzelt")),
        ]
        for rest, want in cases:
            with self.subTest(rest=rest):
                self.assertEqual(cli._split_run_and_note(rest), want)


class MainTests(unittest.TestCase):
    def setUp(self):
        _ortak.clear_index()
        self.addCleanup(_ortak.clear_index)

    def test_version(self):
        for argv in (["--surum"], ["-V"], ["--version", "istek"], ["--yardim", "--sürüm"]):
            with self.subTest(argv=argv):
                r = run_main(*argv)
                self.assertEqual((r.code, r.out, r.lines), (0, f"jev {__version__}\n", []))

    def test_help(self):
        for argv in (["--yardim"], ["-h"], ["yardım"], ["HELP"], ["yardim", "fazla", "sözcük"], ["durum", "--help"]):
            with self.subTest(argv=argv):
                r = run_main(*argv)
                self.assertEqual((r.code, r.lines), (0, []))
                self.assertEqual(r.out, HELP)

    def test_no_request_without_console_prints_help(self):
        for argv in ([], ["   "]):
            r = run_main(*argv)
            self.assertEqual(r.code, 2)
            self.assertEqual(r.out, HELP)

    def test_usage_errors_exit_2(self):
        agents = ", ".join(_ortak.make_config().agents)
        cases = [
            (["--bilinmeyen"], "Bilinmeyen seçenek: --bilinmeyen"),
            (["istek", "--kuru-hiz", "0"], "--kuru-hiz 0'dan büyük ve en fazla 1000 olmalı."),
            (["gecmis", "abc"], "Kullanım: jev gecmis [N]  (N pozitif bir sayı)"),
            (["geçmiş", "0"], "Kullanım: jev gecmis [N]  (N pozitif bir sayı)"),
            (["gecmis", "3", "4"], "Kullanım: jev gecmis [N]  (N pozitif bir sayı)"),
            (["ajanlar", "opus", "yok", "Astra2"], f"Bilinmeyen ajan: yok, astra2 (bilinenler: {agents})"),
            (["ofis", "--arayuz-yok"], "`jev ofis` ile --arayuz-yok birlikte kullanılamaz."),
        ]
        for cmd in ("durum", "devam", "rapor", "ofis"):
            cases.append(([cmd, "yapılacaklar", "listesi"], f"`jev {cmd}` en fazla bir koşu kimliği alır. Yeni bir "
                                                           "istek başlatmak istiyorsan isteği tırnak içinde yaz: jev \"…\""))
        for argv, msg in cases:
            with self.subTest(argv=argv):
                r = run_main(*argv)
                self.assertEqual((r.code, r.out), (2, ""))
                self.assertEqual(r.lines[-2:], ["✗ " + msg, HINT])

    def test_errors_exit_1(self):
        r = run_main("durum", "yok-boyle-bir-kosu")
        self.assertEqual((r.code, r.lines), (1, ["✗ Koşu bulunamadı: yok-boyle-bir-kosu"]))
        r = run_main("rapor", "-p", _ortak.temp_dir("bos"))
        self.assertEqual((r.code, r.lines), (1, ['✗ Hiç koşu bulunamadı. Yeni koşu için: jev "istek"']))

    def test_broken_config_exit_1(self):
        home = _ortak.temp_dir("bozuk-ayar")
        (home / "jev.toml").write_text("[jev\nbeyin = ", encoding="utf-8")
        with mock.patch.dict(os.environ, {"JEV_HOME": str(home)}):
            r = run_main("gecmis")
        self.assertEqual(r.code, 1)
        self.assertTrue(r.lines[-1].startswith(f"✗ {home / 'jev.toml'} okunamadı: "), "hata satırı beklenen biçimde değil")

    def test_setup_errors_exit_1_and_ctrl_c_130(self):
        errors = (SetupError("Çalışma ağacı temiz değil."), ConfigError("Ayar hatalı."), LockError("Kilitli."),
                  FileNotFoundError("Koşu bulunamadı: x"), GitError("git başarısız"))
        for err in errors:
            with self.subTest(err=type(err).__name__):
                with mock.patch.object(cli, "create_run", side_effect=err):
                    r = run_main("istek")
                self.assertEqual((r.code, r.lines), (1, [f"✗ {err}"]))
        with mock.patch.dict(cli.HANDLERS, {"gecmis": mock.Mock(side_effect=KeyboardInterrupt)}):
            r = run_main("gecmis")
        self.assertEqual((r.code, r.lines), (130, ["", "! Durduruldu."]))

    def test_empty_history(self):
        r = run_main("gecmis")
        self.assertEqual((r.code, r.lines), (0, ['Henüz koşu yok. Yeni koşu için: jev "istek"']))

    def test_subcommand_dispatch_and_terminal_setup(self):
        seen = {}

        def handler(cfg, a, term, rest):
            seen.update(stamps=term.stamps, verbose=term.verbose, rest=rest)
            return 7

        cases = [(["durum"], False, []), (["RAPOR", "x"], False, ["x"]), (["geçmiş", "5"], False, ["5"]),
                 (["ajanlar", "sol"], False, ["sol"]), (["ajanlar", "--test"], True, []), (["devam", "-v"], True, []),
                 (["düzelt", "eksik", "testler"], True, ["eksik", "testler"]), (["Ofis", "x"], True, ["x"])]
        for argv, stamps, rest in cases:
            with self.subTest(argv=argv):
                sub = cli.SUBCOMMANDS[argv[0].lower()]
                with mock.patch.dict(cli.HANDLERS, {sub: handler}):
                    r = run_main(*argv)
                self.assertEqual(r.code, 7)  # alt komutun çıkış kodu aynen döner
                self.assertEqual(seen, {"stamps": stamps, "verbose": "-v" in argv, "rest": rest})


class NewRunTests(unittest.TestCase):
    """`jev "istek"` ve etkileşimli mod: seçenekler koşu oluşturmaya doğru aktarılır (koşu oluşturma yakalanır)."""

    def start(self, *argv, cfg=None, **kw):
        cfg = cfg or _ortak.make_config()
        runner = mock.Mock(name="runner")
        with mock.patch.object(cli, "load_config", return_value=cfg), \
                mock.patch.object(cli, "create_run", return_value=runner) as create, \
                mock.patch.object(cli, "_run_session", return_value=0) as session:
            r = run_main(*argv, **kw)
        r.create, r.session, r.runner, r.cfg = create, session, runner, cfg
        return r

    def assert_created(self, r, request, **want):
        kw = dict(project_arg=None, approval=False, dry=False, speed=1.0, term=r.term, scale=None, ui=True,
                  verbose=False, exit_after=False)
        kw.update(want)
        r.create.assert_called_once_with(r.cfg, request, **kw)
        r.session.assert_called_once_with(r.runner, r.term)

    def test_request_and_defaults(self):
        r = self.start("Yapılacaklar", "listesi", "CLI", "tasarla")
        self.assertEqual(r.code, 0)
        self.assert_created(r, "Yapılacaklar listesi CLI tasarla")

    def test_options_reach_the_run(self):
        r = self.start("  istek ", "--kuru")
        self.assert_created(r, "istek", dry=True)
        r = self.start("istek", "--kuru-hiz", "5", "-p", "C:/proje", "--onay", "--arayuz-yok", "--bitince-cik", "-v")
        self.assert_created(r, "istek", project_arg="C:/proje", approval=True, dry=True, speed=5.0, ui=False,
                            verbose=True, exit_after=True)
        r = self.start("istek", cfg=_ortak.make_config(plan_onayi=True))
        self.assert_created(r, "istek", approval=True)  # ayardaki plan_onayi
        r = self.start("istek", "--olcek", "Küçük")
        self.assert_created(r, "istek", scale="kucuk")

    def test_session_exit_code_is_returned(self):
        with mock.patch.object(cli, "create_run", return_value=mock.Mock()), \
                mock.patch.object(cli, "_run_session", return_value=3):
            self.assertEqual(run_main("istek").code, 3)

    def test_interactive_request(self):
        r = self.start(tty=True, inputs=["  Birim dönüştürücü CLI tasarla  "])
        self.assertEqual(r.code, 0)
        self.assert_created(r, "Birim dönüştürücü CLI tasarla")
        self.assertEqual(r.lines[1], header(f"jev {__version__} · yazılım ofisi"))
        # tek sözcük değilse alt komut sayılmaz ("devam" ile başlayan bir istek)
        r = self.start("--kuru", tty=True, inputs=["devam eden işleri listeleyen CLI tasarla"])
        self.assert_created(r, "devam eden işleri listeleyen CLI tasarla", dry=True)

    def test_interactive_exit_words(self):
        for answer in ("", "   ", "çıkış", "  ÇIKIŞ ", "Q", "exit", EOFError()):
            with self.subTest(answer=answer):
                r = self.start(tty=True, inputs=[answer])
                self.assertEqual(r.code, 0)
                r.create.assert_not_called()

    def test_interactive_subcommands(self):
        r = self.start(tty=True, inputs=["yardım"])
        self.assertEqual((r.code, r.out), (0, HELP))
        r.create.assert_not_called()
        calls = []

        def handler(cfg, a, term, rest):
            calls.append(rest)
            return 5

        with mock.patch.dict(cli.HANDLERS, {"duzelt": handler, "durum": handler}):
            for answer, rest in (("düzelt eksik testler", ["eksik", "testler"]), ("Düzelt", []), ("durum", [])):
                with self.subTest(answer=answer):
                    r = self.start(tty=True, inputs=[answer])
                    self.assertEqual((r.code, calls[-1]), (5, rest))
                    r.create.assert_not_called()
        r = self.start(tty=True, inputs=["gecmis"])  # gerçek alt komut
        self.assertEqual((r.code, r.lines[-1]), (0, 'Henüz koşu yok. Yeni koşu için: jev "istek"'))


class SessionTests(unittest.TestCase):
    """_run_session: kilit alınamazsa kayda dokunmaz; her durumda kapatır; Ctrl+C'de yarım işi güvenle durdurur."""

    def fake(self, phase="executing"):
        r = mock.Mock(name="runner")
        r.state = {"phase": phase, "run_id": "20260928-101500-x"}
        return r

    def test_open_failure_closes_only_the_bus(self):
        r = self.fake()
        r.open.side_effect = LockError("Bu projede başka bir jev süreci çalışıyor.")
        with self.assertRaises(LockError):
            cli._run_session(r, _ortak.QuietTerminal(), intro=False)
        r.bus.close.assert_called_once_with()
        r.close.assert_not_called()  # close() durumu kaydederdi: kilidi tutan sürecin state.json'u ezilmesin
        r.session.assert_not_called()

    def test_body_and_session(self):
        r = self.fake()
        self.assertEqual(cli._run_session(r, _ortak.QuietTerminal(), body=lambda: 3, intro=False), 3)
        r.session.assert_not_called()
        r.close.assert_called_once_with()
        r = self.fake()
        r.session.return_value = 0
        self.assertEqual(cli._run_session(r, _ortak.QuietTerminal(), intro=False), 0)
        r.session.assert_called_once_with()
        r.close.assert_called_once_with()

    def test_ctrl_c(self):
        for phase in sorted(ACTIVE_PHASES) + ["reported", "paused", "aborted"]:
            with self.subTest(phase=phase):
                r, term = self.fake(phase), _ortak.QuietTerminal()
                r.session.side_effect = KeyboardInterrupt
                self.assertEqual(cli._run_session(r, term, intro=False), 130)
                r.close.assert_called_once_with()
                if phase in ACTIVE_PHASES:
                    r.interrupted.assert_called_once_with()
                else:
                    r.interrupted.assert_not_called()
                    self.assertEqual(term.lines[-1], "! Çıkılıyor. " + cli._resume_hint(r.state))

    def test_unexpected_errors_still_close(self):
        r = self.fake()
        r.session.side_effect = RuntimeError("beklenmedik")
        with self.assertRaises(RuntimeError):
            cli._run_session(r, _ortak.QuietTerminal(), intro=False)
        r.close.assert_called_once_with()


class RunCommandTests(unittest.TestCase):
    """Kayıtlı bir koşu üzerinde durum, rapor, devam, duzelt ve ofis (planı yazılmış kuru koşu, T01–T06 bekliyor)."""

    @classmethod
    def setUpClass(cls):
        run = _ortak.planned_run()
        run.bus.close()
        cls.runner, cls.rid, cls.project, cls.rdir = run, run.run_id, run.project, run.rdir
        cls.lock = jev_dir(run.project) / "lock"
        cls.base = load_state(run.rdir)

    def setUp(self):
        save_state(self.rdir, copy.deepcopy(self.base))
        for p in (self.lock, *self.rdir.glob("report*")):
            p.unlink(missing_ok=True)
        index_update(self.rid, project=str(self.project), request=self.base["request"], created=self.base["created"],
                     dry=True)
        p = mock.patch.object(cli, "pid_alive", side_effect=lambda pid: pid == LIVE_PID)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.lock.unlink, missing_ok=True)

    def set_state(self, **fields):
        st = load_state(self.rdir)
        st.update(fields)
        save_state(self.rdir, st)

    def state_bytes(self) -> bytes:
        return (self.rdir / "state.json").read_bytes()

    def hold_lock(self, run_id=None):
        """Projeyi canlı başka bir jev süreci tutuyor."""
        atomic_write_json(self.lock, {"pid": LIVE_PID, "run_id": run_id or self.rid, "since": iso()})

    def write_report(self, round_, md):
        name = report_name(round_)
        atomic_write_json(self.rdir / f"{name}.json", {"verdict": "basarili", "summary": "özet", "gaps": [],
                                                      "criteria": [], "risks": [], "next_steps": []})
        atomic_write_text(self.rdir / f"{name}.md", md)
        st = load_state(self.rdir)
        st["reports"].append({"round": round_, "file": f"{name}.md", "verdict": "basarili", "summary": "özet",
                              "ts": iso()})
        save_state(self.rdir, st)

    def test_durum_of_crashed_run(self):
        before = self.state_bytes()
        r = run_main("durum", self.rid)
        self.assertEqual(r.code, 0)
        self.assertFalse(r.term.stamps)
        self.assertIn(header(f"Koşu {self.rid}"), r.lines)
        phase = next(ln for ln in r.lines if ln.startswith("Aşama: "))
        self.assertTrue(phase.startswith("Aşama: Uygulama · tur 1 · ilerleme 0/6"), phase)
        self.assertTrue(phase.endswith(" · KURU KOŞU"), phase)
        self.assertEqual([ln.split()[0] for ln in r.lines if ln.startswith("  T")],
                         ["T01", "T02", "T03", "T04", "T05", "T06"])
        self.assertEqual(r.lines[-1], "! Koşu etkin görünüyor ama çalışan bir jev süreci yok (süreç beklenmedik "
                                      f"şekilde kapanmış). Sürdürmek için: jev devam {self.rid}")
        self.assertEqual(self.state_bytes(), before)  # durum yalnızca okur

    def test_durum_of_live_run_shows_office(self):
        url = "http://127.0.0.1:8765/?k=deneme"
        self.set_state(ui_url=url)
        self.hold_lock()
        r = run_main("durum", "-p", self.project)  # kimliksiz: projenin son koşusu
        self.assertEqual(r.code, 0)
        self.assertIn(header(f"Koşu {self.rid}"), r.lines)
        self.assertIn(f"Ofis: {url}", r.lines)
        self.assertEqual(r.lines[-1], f"Bu koşu şu an çalışıyor (PID {LIVE_PID}).")

    def test_durum_hints(self):
        self.set_state(phase="paused", paused_reason="Opus kotası doldu.")
        r = run_main("durum", self.rid)
        self.assertIn("Duraklama nedeni: Opus kotası doldu.", r.lines)
        self.assertEqual(r.lines[-1], f"Sürdürmek için: jev devam {self.rid}")
        self.set_state(phase="reported")
        r = run_main("durum", self.rid[:15])  # kimliğin başı yeter
        self.assertEqual(r.lines[-1], f'Rapor: jev rapor {self.rid} · düzeltme: jev duzelt {self.rid} "not"')

    def test_rapor(self):
        r = run_main("rapor", self.rid)
        self.assertEqual((r.code, r.lines), (1, ["! Bu koşunun henüz raporu yok."]))
        self.write_report(1, "# Rapor\n\nİlk tur")
        self.write_report(2, "# Rapor (tur 2)\n\nDüzeltme turu")
        r = run_main("rapor", self.rid)
        self.assertEqual(r.code, 0)
        self.assertEqual(r.lines, ["# Rapor (tur 2)\n\nDüzeltme turu", f"Rapor dosyası: {self.rdir / 'report-2.md'}"])

    def test_busy_project(self):
        url = "http://127.0.0.1:8765/?k=deneme"
        self.set_state(ui_url=url)
        self.hold_lock()
        before = self.state_bytes()
        busy = f"✗ Bu projede başka bir jev süreci çalışıyor (PID {LIVE_PID}, koşu {self.rid})."
        r = run_main("devam", self.rid)
        self.assertEqual((r.code, r.lines), (1, [busy, f"Ofis: {url}",
                                                 "O süreçte `devam` yaz ya da ofisteki Devam düğmesini kullan."]))
        r = run_main("duzelt", self.rid, "eksik testler")
        self.assertEqual((r.code, r.lines), (1, [busy, f"Ofis: {url}",
                                                 "O süreçte `düzelt [not]` yaz ya da ofisteki Düzelt düğmesini kullan."]))
        r = run_main("ofis", self.rid)
        self.assertEqual((r.code, r.lines), (0, [f"Koşu başka bir jev sürecinde açık; ofis: {url}"]))
        r.browser.assert_called_once_with(url)
        self.assertEqual(self.state_bytes(), before)  # öteki sürecin kaydına dokunulmaz
        self.assertTrue(self.lock.exists())
        # kilit bu projedeki başka bir koşunun: bu koşunun ofis adresi gösterilmez
        self.hold_lock("20260101-000000-baska")
        r = run_main("ofis", self.rid)
        self.assertEqual((r.code, r.lines), (1, [f"✗ Bu projede başka bir jev süreci çalışıyor (PID {LIVE_PID}, koşu "
                                                 "20260101-000000-baska)."]))
        r.browser.assert_not_called()

    def test_devam_refuses_aborted_run(self):
        self.set_state(phase="aborted", paused_reason="Kullanıcı planı reddetti.")
        before = self.state_bytes()
        r = run_main("devam", self.rid)
        self.assertEqual((r.code, r.lines), (4, ["✗ Bu koşu iptal edilmiş; devam ettirilemez. Yeni bir koşu başlat."]))
        self.assertEqual(self.state_bytes(), before)

    def test_devam_on_reported_run(self):
        self.set_state(phase="reported")
        r = run_main("devam", self.rid, "--arayuz-yok")
        self.assertEqual(r.code, 0)
        self.assertIn("Koşu rapor aşamasında. Düzeltme için: `düzelt [not]`.", r.lines)
        self.assertEqual(load_state(self.rdir)["phase"], "reported")
        self.assertFalse(self.lock.exists())  # oturum kapanınca kilit bırakılır

    def test_duzelt_needs_a_finished_run(self):
        # süreç çökmüş (etkin aşama, kilit yok): koşu önce duraklatılmış sayılır, düzeltme turu açılmaz
        r = run_main("duzelt", "eksik testler", "-p", self.project, "--arayuz-yok")
        self.assertEqual(r.code, 3)
        self.assertIn("! Koşu duraklatılmış; önce `jev devam` ile raporu tamamla, sonra düzelt.", r.lines)
        st = load_state(self.rdir)
        self.assertEqual((st["phase"], st["pause_kind"], st["resume_phase"], st["round"]),
                         ("paused", "hata", "executing", 1))
        self.assertFalse(self.lock.exists())
        self.set_state(phase="aborted")
        r = run_main("duzelt", self.rid, "--arayuz-yok")
        self.assertEqual(r.code, 4)
        self.assertIn("✗ Bu koşu iptal edilmiş; düzeltme turu açılamaz.", r.lines)

    def test_intro(self):
        run, term = self.runner, _ortak.QuietTerminal()
        with mock.patch.object(runner_mod, "_stdin_is_tty", return_value=False):
            cli._intro(run, term)
        self.assertEqual(term.lines[:5], [
            "", header(f"jev · koşu {self.rid}"), "İstek: Yapılacaklar listesi CLI tasarla",
            f"Proje: {self.project} · dal {run.state['branch']}",
            "KURU KOŞU: sahte ajanlar, kota harcanmaz (hız ×1000)."])
        self.assertTrue(term.lines[5].startswith("  Senaryo sabit: isteğin ne olursa olsun «"), term.lines[5])
        self.assertEqual(len(term.lines), 6)  # ofis yok, konsol yok: başka satır yok
        term = _ortak.QuietTerminal()
        with mock.patch.object(runner_mod, "_stdin_is_tty", return_value=True), \
                mock.patch.object(run, "url", "http://ofis"):
            cli._intro(run, term)
        self.assertEqual(term.lines[-2:], ["Ofis: http://ofis", "Koşu sırasında: durum · çıkış   (Ctrl+C güvenle durdurur)"])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        _ortak.clear_index()
        self.addCleanup(_ortak.clear_index)
        self.root = _ortak.temp_dir("gecmis")

    def add(self, rid, phase, *, exists=True, locked=False, **rec):
        proj = self.root / rid
        if exists:
            run_dir(proj, rid).mkdir(parents=True)
        if locked:
            atomic_write_json(jev_dir(proj) / "lock", {"pid": LIVE_PID, "run_id": rid})
        index_update(rid, project=str(proj), phase=phase, request=f"{rid} isteği", **rec)
        return proj

    def test_listing(self):
        a = self.add("20260925-100000-a", "reported", created="2026-09-25T10:00:00+03:00", dry=True, round=2)
        b = self.add("20260926-100000-b", "executing", created="2026-09-26T10:00:00+03:00")
        c = self.add("20260927-100000-c", "fixing", created="2026-09-27T10:00:00+03:00", locked=True, round=2)
        d = self.add("20260928-100000-d", "paused", created="2026-09-28T10:00:00+03:00", exists=False)
        with mock.patch.object(cli, "pid_alive", side_effect=lambda pid: pid == LIVE_PID):
            r = run_main("gecmis")
        self.assertEqual(r.code, 0)
        self.assertEqual(r.lines, [
            "", header("Koşular (4)"),
            "20260928-100000-d  Duraklatıldı", "    20260928-100000-d isteği", f"    {d}  (silinmiş)",
            "20260927-100000-c  Düzeltme (çalışıyor)", "    20260927-100000-c isteği  [tur 2]", f"    {c}",
            "20260926-100000-b  Uygulama (yarıda kaldı → jev devam)", "    20260926-100000-b isteği", f"    {b}",
            "20260925-100000-a  Rapor", "    20260925-100000-a isteği  [kuru · tur 2]", f"    {a}",
        ])
        r = run_main("geçmiş", "2")
        self.assertEqual(r.lines[1], header("Son 2 koşu"))
        self.assertEqual([ln.split()[0] for ln in r.lines[2::3]], ["20260928-100000-d", "20260927-100000-c"])


class AgentsTests(unittest.TestCase):
    """`jev ajanlar`: roller, kota, beyin, CLI'lar, kullanım toplamları ve `--test` sonuç iletileri."""

    def setUp(self):
        _ortak.clear_index()
        QuotaBook().clear()
        self.addCleanup(_ortak.clear_index)
        self.addCleanup(QuotaBook().clear)
        self.cfg = _ortak.make_config()
        self.clis = clis = {"codex": CliInfo("codex", Path(r"C:\araclar\codex.cmd"), None, "PATH"),
                            "claude": CliInfo("claude", None, None, "bulunamadı")}
        for target, fake in (("jev.discovery.find_cli", lambda name, configured="auto": clis[name]),
                             ("jev.discovery.cli_version", lambda info, timeout=20: "codex-cli 9.9.9")):
            p = mock.patch(target, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def section(lines, title):
        """Başlığın altındaki boş olmayan satırlar (bir sonraki başlığa kadar)."""
        rest = lines[lines.index(header(title)) + 1:]
        end = next((i for i, ln in enumerate(rest) if ln.startswith("━" * 8 + " ")), len(rest))
        return [ln for ln in rest[:end] if ln]

    def usage(self, rid, data, dry=False):
        proj = _ortak.temp_dir("kullanim")
        atomic_write_json(run_dir(proj, rid) / "usage.json", data)
        index_update(rid, project=str(proj), dry=dry, created=rid)

    def test_listing(self):
        QuotaBook().mark("sonnet", now() + dt.timedelta(hours=2), "limit doldu")
        self.usage("20260920-100000-a", {"opus": {"calls": 3, "input_tokens": 1500, "output_tokens": 2_500_000,
                                                  "duration_s": 3725},
                                         "sonnet": {"calls": 2, "input_tokens": 999, "output_tokens": 1000,
                                                    "duration_s": 59.4},
                                         "sol": "bozuk kayıt"})
        self.usage("20260921-100000-kuru", {"luna": {"calls": 9, "input_tokens": 9, "output_tokens": 9}}, dry=True)
        self.usage("20260922-100000-b", {"opus": {"calls": 1, "input_tokens": 500}})
        r = run_main("ajanlar")
        self.assertEqual(r.code, 0)
        self.assertFalse(r.term.stamps)
        agents = self.section(r.lines, "Ajanlar")
        for name in self.cfg.agents:
            with self.subTest(agent=name):
                line = next(ln for ln in agents if ln.startswith(f"{display(name):<7} "))
                self.assertIn(f" {self.cfg.label(name)} ", line)
                self.assertTrue(line.endswith(", ".join(cli.ROLE_TR[x] for x in self.cfg.agent(name)["roller"])), line)
        self.assertTrue(next(ln for ln in agents if ln.startswith("Opus ")).endswith("planlayıcı, beyin, işçi, eskalasyon"))
        self.assertTrue(next(ln for ln in agents if ln.startswith("Sol ")).endswith("işçi, denetçi"))
        quota = [ln for ln in agents if "kota dolu" in ln]
        self.assertEqual(len(quota), 1)
        self.assertTrue(quota[0].startswith("        kota dolu: ~") and quota[0].endswith(" kadar (limit doldu)"),
                        quota[0])
        self.assertLess(agents.index(next(ln for ln in agents if ln.startswith("Sonnet "))), agents.index(quota[0]))
        self.assertEqual(agents[-4:], [
            "Son kontrol (orta, büyük iş): Sol · yedek denetçi: Opus (büyük işte kodun çoğunu Sol yazdıysa)",
            "Jev'in beyni: Opus · yedek beyin: Sol (efor xhigh)",
            "Jev modeli (TypeSafe): anahtar yok (TYPESAFE_API_KEY ortam değişkeni boş)",
            '        anahtar için: setx TYPESAFE_API_KEY "<anahtar>" (console.typesafe.ai); o zamana dek kararı '
            "beyin verir"])
        scales = self.section(r.lines, "Ölçekler (Jev işin boyunu ölçer; --olcek ile sen de seçebilirsin)")
        rows = {ln.split()[0]: ln for ln in scales}
        self.assertIn(" Sol medium ", rows["Orta"])
        self.assertIn(" Sol high* ", rows["Büyük"])
        self.assertEqual(scales[-1], "* yedek denetçi: kodun çoğunu Sol yazdıysa son kontrolü Opus yapar; "
                                     "Opus soğumadaysa Sol")
        self.assertEqual(self.section(r.lines, "CLI'lar"), [
            'claude  bulunamadı — ayarlarda [cli] claude = "tam yol" verebilirsin',
            r"codex   codex-cli 9.9.9 · C:\araclar\codex.cmd (PATH)"])
        self.assertEqual(self.section(r.lines, "Kullanım (kuru koşular hariç)"), [
            "Opus    4 çağrı · 2.0k girdi · 2.5M çıktı token · 1 sa 2 dk",
            "Sonnet  2 çağrı · 999 girdi · 1.0k çıktı token · 59 sn"])

    def test_selection_and_empty_usage(self):
        r = run_main("ajanlar", "OPUS", "sol")
        agents = self.section(r.lines, "Ajanlar")
        self.assertEqual([ln.split()[0] for ln in agents if not ln.startswith(" ")], ["Opus", "Sol", "Son", "Jev'in", "Jev"])
        self.assertEqual(self.section(r.lines, "Kullanım (kuru koşular hariç)"),
                         ["Henüz gerçek bir koşu kullanımı yok."])

    def test_dry_agent_test(self):
        r = run_main("ajanlar", "--test", "--kuru-hiz", "1000")
        self.assertEqual(r.code, 0)
        self.assertTrue(r.term.stamps)
        lines = self.section(r.lines, "Ajan testi (kuru)")
        self.assertEqual(sum(ln.endswith("yanıt: OK") for ln in lines), len(self.cfg.agents))
        self.assertTrue(lines[-1].startswith(f"✓ {len(self.cfg.agents)}/{len(self.cfg.agents)} ajan yanıt verdi. "
                                             f"Kayıtlar: {_ortak.HOME / 'ajan-testi'}"), lines[-1])
        r = run_main("ajanlar", "--test", "--kuru", "luna")
        self.assertEqual(r.code, 0)
        self.assertTrue(self.section(r.lines, "Ajan testi (kuru)")[-1].startswith("✓ 1/1 ajan yanıt verdi."))

    def test_agent_test_failures(self):
        reset = dt.datetime(2026, 9, 28, 17, 30).astimezone()
        results = {
            "opus": AgentResult(ok=False, error_kind="auth"),
            "sol": AgentResult(ok=False, error_kind="quota", reset_at=reset, duration_s=1.5),
            "sonnet": AgentResult(ok=False, error_kind="timeout", duration_s=300),
            "luna": AgentResult(ok=False, error_kind="crash", error_text="  bağlantı\nkoptu "),
        }
        with mock.patch.object(Gateway, "call", autospec=True,
                               side_effect=lambda gw, agent, *a, **kw: results[agent]) as call:
            r = run_main("ajanlar", "--test")
        self.assertEqual(r.code, 1)
        self.assertEqual({c.args[2] for c in call.call_args_list}, {"test"})
        lines = self.section(r.lines, "Ajan testi")
        self.assertEqual(lines[0], "Her ajana en düşük eforla tek satırlık bir deneme mesajı gidiyor (az miktarda "
                                   "kota harcar).")
        errors = [ln for ln in lines if ln.startswith("✗ ")]
        want = {"opus": "giriş gerekli — terminalde `claude auth login` çalıştır",
                "sol": f"kota dolu (~{ek(local_hhmm(reset), 'de')} açılır)",
                "sonnet": "zaman aşımı", "luna": "bağlantı koptu"}  # codex girişi: sonraki test
        self.assertEqual(len(errors), len(want))
        for line, (name, why) in zip(errors, want.items()):
            with self.subTest(agent=name):
                self.assertTrue(line.startswith(f"✗ {display(name)} ") and line.endswith(" · " + why), line)
        self.assertIn(" 1.5 sn · ", errors[1])
        self.assertTrue(lines[-1].startswith("! 0/4 ajan yanıt verdi. Kayıtlar: "), lines[-1])

    def test_login_hint_with_the_full_path_when_not_on_path(self):
        # Paketli masaüstü uygulamasının claude.exe'si PATH'te değil: ipucu PowerShell'e olduğu gibi yapıştırılabilmeli.
        exe = Path(r"C:\Users\u\AppData\Local\Packages\Claude_x\LocalCache\Roaming\Claude\claude-code\2.1.281"
                   r"\claude.exe")
        self.clis["claude"] = CliInfo("claude", exe, None, "bilinen klasör")
        with mock.patch.object(Gateway, "call", autospec=True, return_value=AgentResult(ok=False, error_kind="auth")):
            r = run_main("ajanlar", "--test", "opus", "sol")
        errors = [ln for ln in self.section(r.lines, "Ajan testi") if ln.startswith("✗ ")]
        self.assertEqual(len(errors), 2)
        self.assertTrue(errors[0].endswith(f' · giriş gerekli — terminalde `& "{exe}" auth login` çalıştır'),
                        errors[0])
        self.assertTrue(errors[1].endswith(" · giriş gerekli — terminalde `codex login` çalıştır"), errors[1])


if __name__ == "__main__":
    unittest.main()
