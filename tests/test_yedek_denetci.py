"""Yedek denetçi: kimse kendi işini denetlemesin.

Büyük işte ([jev] yedek_denetci; varsayılan Opus, yalnızca büyük) kodun yarısından fazlasını denetçi (Sol) kendisi
yazdıysa son kontrolü yedek yapar. Kod payı Jev'in görev commit'lerinden ("jev(T01): başlık [sol]") sayılır; kilit,
küçültülmüş, veri ve ikili dosyalar, Jev'in ortak bağlamı ve görev dışı commit'ler sayılmaz. Yedek soğumadaysa ya da
çağrısı yarıda kalırsa (kota, oturum, iki başarısız deneme) beklenmez: son kontrol denetçide kalır ve rapor eki bunu
yazar.
"""
import datetime as dt
import re
import unittest
from collections import Counter
from unittest import mock

import _ortak
from jev import gitops
from jev.adapters.base import AgentResult
from jev.office import snapshot
from jev.phases import common, review
from jev.phases.common import Unavailable, call_fixed
from jev.util import now, read_jsonl

CALL_RX = re.compile(r"^\d{3}-([a-z_]+)-(.+)-([a-z0-9]+)\.prompt\.md$")


def lines(n: int, word: str = "x") -> str:
    return "".join(f"{word}{i}\n" for i in range(n))


def fake_run(cfg=None, cooled=()) -> mock.Mock:
    """pick_reviewer'ın gördüğü koşu: ayar, proje, durum ve kota defteri."""
    run = mock.Mock(cfg=cfg or _ortak.make_config(), project=_ortak.temp_dir("sec"), state={"base_commit": "abc"})
    run.quota.available.side_effect = lambda a: a not in cooled
    return run


def fail(kind: str) -> AgentResult:
    return AgentResult(ok=False, error_kind=kind, error_text=f"{kind} hatası", reset_at=now() + dt.timedelta(hours=3))


class AuthorshipTests(unittest.TestCase):
    """Kod payı: görev commit'lerinde ajan başına değişen satır (eklenen + silinen)."""

    @classmethod
    def setUpClass(cls):
        cls.project = d = _ortak.temp_dir("pay")
        cls.base = gitops.init_repo(d)

        def commit(message: str, files: dict) -> None:
            for name, body in files.items():
                p = d / name
                p.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(body, bytes):
                    p.write_bytes(body)
                else:
                    p.write_text(body, encoding="utf-8", newline="\n")
            gitops.commit_all(d, message)

        commit("jev: ortak bağlam (AGENTS.md, CLAUDE.md)", {"AGENTS.md": lines(40), "kurulum.py": lines(30)})
        commit("jev(T01): Ana kod [sol]", {"app.py": lines(10), "package-lock.json": lines(200),
                                           "static/app.min.js": lines(50)})
        commit("jev(T02): Testler ve veri [sonnet]", {"tests/test_app.py": lines(4), "veri/iller.json": lines(80),
                                                      "logo.png": b"\x89PNG\x00\x01" * 50, "AGENTS.md": lines(41)})
        commit("Kullanıcının kendi değişikliği", {"notlar.py": lines(25)})
        commit("jev(T03): Yardımcı [luna]", {"util.py": lines(3)})
        commit("jev(D1-01): Düzeltme [sol]", {"app.py": lines(8) + "y8\ny9\n"})  # iki satır değişti: +2 -2

    def test_counts_task_code_per_agent(self):
        # kilit, küçültülmüş, veri, ikili dosya, AGENTS.md ve görev dışı commit'ler sayılmaz
        self.assertEqual(review.authorship(self.project, self.base), Counter({"sol": 14, "sonnet": 4, "luna": 3}))

    def test_no_base(self):
        self.assertEqual(review.authorship(self.project, ""), Counter())
        self.assertEqual(review.authorship(self.project, "0" * 40), Counter())  # git hatası: sayılmaz


class PickReviewerTests(unittest.TestCase):
    def pick(self, share: dict, level: str = "buyuk", **kw) -> tuple[str, mock.Mock]:
        run = fake_run(**kw)
        with mock.patch.object(review, "authorship", return_value=Counter(share)):
            return review.pick_reviewer(run, level), run

    def test_sol_wrote_most_of_a_big_job(self):
        got, run = self.pick({"sol": 70, "sonnet": 20, "luna": 10})
        self.assertEqual(got, "opus")
        self.assertEqual((run.state["reviewer"], run.state["review_note"]), ("opus", "kodun çoğunu Sol yazdı (%70)"))
        run.save.assert_called_once_with()
        run.emit.assert_called_once_with("run.info", {"reviewer": "opus"})
        run.log.assert_called_once_with("info", "Kodun çoğunu Sol yazdı (%70); son kontrolü Opus yapacak.")

    def test_sol_reviews_otherwise(self):
        for share, level in (({"sol": 50, "sonnet": 50}, "buyuk"),  # yarı yarıya: yarısından fazlası değil
                             ({"sonnet": 254, "sol": 50, "luna": 18}, "buyuk"),  # kuru koşunun payları
                             ({}, "buyuk"),  # görev commit'i yok
                             ({"sol": 90, "luna": 10}, "orta")):  # orta işte yedek denetçi kapalı (varsayılan)
            with self.subTest(share=share, level=level):
                got, run = self.pick(share, level)
                self.assertEqual((got, run.state["reviewer"], run.state["review_note"]), ("sol", "sol", ""))
                run.emit.assert_called_once_with("run.info", {"reviewer": "sol"})
                run.log.assert_not_called()

    def test_backup_cooling_down(self):
        got, run = self.pick({"sol": 80, "sonnet": 20}, cooled={"opus"})
        self.assertEqual((got, run.state["review_note"]), ("sol", "kodun çoğunu Sol yazdı (%80) ama Opus soğumada"))
        run.log.assert_called_once_with("info", "Kodun çoğunu Sol yazdı (%80) ama Opus soğumada; son kontrolü Sol "
                                                "yapacak.")

    def test_mini_and_small_jobs_are_reviewed_by_jev(self):
        for level in ("mini", "kucuk"):
            with self.subTest(level=level):
                got, run = self.pick({"sol": 100}, level)
                self.assertEqual(got, "jev")
                self.assertNotIn("reviewer", run.state)

    def test_config(self):
        mid = _ortak.make_config(jev={"yedek_denetci": {"olcekler": ["orta", "buyuk"]}})
        self.assertEqual(self.pick({"sol": 9, "luna": 1}, "orta", cfg=mid)[0], "opus")
        off = _ortak.make_config(jev={"yedek_denetci": {"olcekler": []}})
        self.assertEqual(self.pick({"sol": 9, "luna": 1}, cfg=off)[0], "sol")
        # denetçi Sonnet ise sayılan Sonnet'in payıdır
        cfg = _ortak.make_config(ajanlar={"sonnet": {"roller": ["isci", "denetci"]}}, jev={"denetci": "sonnet"})
        self.assertEqual(self.pick({"sol": 9, "sonnet": 1}, cfg=cfg)[0], "sonnet")
        self.assertEqual(self.pick({"sol": 1, "sonnet": 9}, cfg=cfg)[0], "opus")


class OptionalCallTests(unittest.TestCase):
    """Yedek denetçinin çağrısı (optional): kota beklenmez, koşu duraklatılmaz; Unavailable yükselir."""

    def call(self, results: list, available: bool = True):
        run = mock.Mock()
        run.quota.available.return_value = available
        run.gw.call.side_effect = results
        with mock.patch.object(common, "wait_or_pause") as wait, mock.patch.object(common.time, "sleep"):
            try:
                return call_fixed(run, "opus", "review", "p", schema_name="review", effort="high",
                                  timeout_key="denetim", optional=True)
            finally:
                wait.assert_not_called()
                self.calls = run.gw.call.call_count

    def test_unavailable(self):
        for results, available, reason, calls in (
                ([], False, "soğumada", 0),
                ([fail("quota")], True, "kotası doldu", 1),
                ([fail("auth")], True, "oturum kapalı", 1),
                ([fail("crash"), fail("timeout")], True, "iki kez başarısız oldu: timeout", 2)):
            with self.subTest(reason=reason):
                with self.assertRaises(Unavailable) as cm:
                    self.call(results, available)
                self.assertEqual((cm.exception.reason, self.calls), (reason, calls))

    def test_retry_and_cancel(self):
        ok = AgentResult(ok=True, structured={"verdict": "basarili"})
        self.assertIs(self.call([fail("crash"), ok]), ok)  # bir kez yeniden denenir
        with self.assertRaises(KeyboardInterrupt):
            self.call([fail("cancelled")])


class FallbackTests(unittest.TestCase):
    """Yedek denetçi çağrının ortasında kotaya takılırsa son kontrol beklemeden denetçiye döner."""

    @classmethod
    def setUpClass(cls):
        cls.run_ = _ortak.planned_run()

    @classmethod
    def tearDownClass(cls):
        cls.run_.bus.close()

    def test_backup_fails_mid_call(self):
        run, rep, seen = self.run_, {"verdict": "basarili", "summary": "tamam"}, []

        def fake(_run, agent, phase, prompt, **kw):
            seen.append((agent, phase, kw["optional"]))
            if agent == "opus":
                raise Unavailable("kotası doldu")
            return AgentResult(ok=True, structured=rep)

        with mock.patch.object(review, "call_fixed", side_effect=fake), \
                mock.patch.object(run, "log", wraps=run.log) as log:
            self.assertEqual(review.reviewer_review(run, [], "buyuk", 1, [], "opus"), (rep, "sol"))
        self.assertEqual(seen, [("opus", "review", True), ("sol", "review", False)])
        self.assertEqual((run.state["reviewer"], run.state["review_note"]),
                         ("sol", "Opus son kontrolü yapamadı (kotası doldu)"))
        log.assert_called_once_with("info", "Opus son kontrolü yapamadı (kotası doldu); son kontrolü Sol yapacak.")
        text = run.term.text()
        self.assertLess(text.index("Opus son kontrolü yapıyor (Claude Opus 5.5, efor high"),
                        text.index("Sol son kontrolü yapıyor"))


@_ortak.slow
class DryRunTests(unittest.TestCase):
    """Kuru koşu: kodun çoğunu Sol yazmış sayılır; son kontrolü Opus yapar, rapor, ofis ve kayıtlar bunu gösterir."""

    def test_opus_reviews_when_sol_wrote_most(self):
        run = _ortak.make_run(speed=1000.0)
        try:
            with mock.patch.object(review, "authorship", return_value=Counter({"sol": 90, "sonnet": 10})):
                self.assertEqual(run.drive(), "reported")
            view = snapshot.build(run)["run"]
        finally:
            run.bus.close()
        st = run.state
        self.assertEqual((st["reviewer"], st["review_note"], view["reviewer"]),
                         ("opus", "kodun çoğunu Sol yazdı (%90)", "opus"))
        calls = [m.groups() for m in (CALL_RX.match(p.name) for p in sorted((run.rdir / "calls").glob("*.prompt.md")))
                 if m]
        self.assertEqual([(p, a) for p, _, a in calls if p in ("plan", "review")], [("plan", "opus"),
                                                                                   ("review", "opus")])
        self.assertEqual([r["by"] for r in st["reports"]], ["opus"])
        self.assertIn("- Son kontrol: Opus — kodun çoğunu Sol yazdı (%90)\n",
                      (run.rdir / "report.md").read_text(encoding="utf-8"))
        events = read_jsonl(run.rdir / "events.jsonl")
        self.assertIn({"reviewer": "opus"}, [e["data"] for e in events if e["type"] == "run.info"])
        self.assertIn("Kodun çoğunu Sol yazdı (%90); son kontrolü Opus yapacak.",
                      [e["data"].get("text") for e in events if e["type"] == "log"])


if __name__ == "__main__":
    unittest.main()
