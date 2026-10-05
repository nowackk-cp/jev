"""Jev'in beyni (belirtim §5.7). Çağrı kapısının rol denetimi test_roller.py'de.

- Müsait ajanlar: işçiler, sonra yalnızca Jev'in kararıyla çalışan eskalasyon ajanı (Opus).
- Danışma: beyin sınırı, soğuma, yedek beyne geçiş, iptal.
- Basit kural: görevi bir kez başka bir işçiye verir; eskalasyon ajanını seçmez.
- Kararların uygulanması: gerçek bir kuru koşunun (sahte plan + T01–T06) görevleri üzerinde.
"""
import copy
import datetime as dt
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import _ortak
from jev import gitops
from jev.adapters.base import AgentResult
from jev.phases import brain
from jev.phases.common import RunAborted, RunPaused
from jev.quota import QuotaBook
from jev.util import local_hhmm, now, read_jsonl

# Kullanıcı Opus'u yeniden sıradan işçi yaparsa (varsayılanda Opus kodu yalnızca Jev'in kararıyla yazar)
OPUS_WORKER = dict(ajanlar={"opus": {"roller": ["planlayici", "beyin", "isci"], "gorev_boyu": ["S", "M", "L"]}},
                   yonlendirme={"L": ["sol", "sonnet", "opus"], "eskalasyon": []})  # her boyda işçi, eskalasyon değil


def decision(kind: str, **kw) -> dict:
    d = {"decision": kind, "agent": None, "effort": None, "rollback": True, "guidance": "", "revised_task": None,
         "new_tasks": [], "rationale": f"test: {kind}", "deviation": "none", "report_note": ""}
    d.update(kw)
    return d


def ok_result(dec: dict, call: str = "007-brain-T02-opus") -> AgentResult:
    return AgentResult(ok=True, structured=dec, paths={"stdout": f"C:\\kosu\\calls\\{call}.stdout.jsonl"})


class AvailableAgents(unittest.TestCase):
    def test_workers_then_escalation_minus_cooling(self):
        q = QuotaBook(_ortak.temp_dir("kota") / "kota.json")
        run = SimpleNamespace(cfg=_ortak.make_config(), quota=q)
        self.assertEqual(brain.available_agents(run), ["sol", "sonnet", "luna", "opus"])
        until = now() + dt.timedelta(hours=1)
        q.mark("sol", until, "test")
        self.assertEqual(brain.available_agents(run), ["sonnet", "luna", "opus"])
        self.assertEqual(brain.cooling_agents(run), {"sol": local_hhmm(until)})
        run.cfg = _ortak.make_config(**OPUS_WORKER)  # işçi olan Opus işçilerle birlikte, ayardaki sırasıyla
        self.assertEqual(brain.available_agents(run), ["opus", "sonnet", "luna"])


class _PlannedRun(unittest.TestCase):
    """Testler aynı kuru koşunun görevleriyle çalışır; koşu durumu her testten önce geri yüklenir."""

    @classmethod
    def setUpClass(cls):
        cls.runner = _ortak.planned_run()
        cls.snapshot = copy.deepcopy(cls.runner.state)

    @classmethod
    def tearDownClass(cls):
        cls.runner.bus.close()

    def setUp(self):
        self.reset()

    def reset(self):
        run = self.runner
        run.state.clear()
        run.state.update(copy.deepcopy(self.snapshot))
        run.quota.clear()
        run.term.lines.clear()
        self.seq = run.bus.last_seq
        self.n_records = len(read_jsonl(run.rdir / "decisions.jsonl"))

    def task(self, tid: str, **fields) -> dict:
        t = next(t for t in self.runner.state["tasks"] if t["id"] == tid)
        t.update(fields)
        return t

    def failing(self, tid: str = "T02", agent: str = "sol", **fields) -> dict:
        """Bir denemesi başarısız olmuş, karar bekleyen görev."""
        base = dict(status="needs_decision", agent=None, attempt_count=1, failed_agents=[agent],
                    attempts=[{"n": 1, "agent": agent, "counted": True, "outcome": "verify_failed"}])
        base.update(fields)
        return self.task(tid, **base)

    def events(self, type_: str) -> list[dict]:
        return [e["data"] for e in self.runner.bus.since(self.seq) if e["type"] == type_]

    def records(self) -> list[dict]:
        return read_jsonl(self.runner.rdir / "decisions.jsonl")[self.n_records:]

    def apply(self, task: dict, dec: dict, *, exhausted: bool = False) -> None:
        brain.apply_decision(self.runner, task, dec, who="opus", call="009-brain-T02-opus", outcome="verify_failed",
                             problem="2 test kaldı", exhausted=exhausted)


class ApplyDecision(_PlannedRun):
    def test_retry_keeps_last_agent(self):
        t = self.failing()
        self.apply(t, decision("retry", effort="xhigh", guidance="Aralık kontrolünü düzelt"))
        self.assertEqual((t["status"], t["forced"], t["guidance"]),
                         ("ready", {"agent": "sol", "effort": "xhigh"}, "Aralık kontrolünü düzelt"))
        self.assertEqual(t["decisions"][-1], {"decision": "retry", "agent": None, "brain": "opus",
                                              "rationale": "test: retry", "deviation": "none", "applied": "retry"})
        rec = self.records()[-1]
        self.assertEqual((rec["task_id"], rec["attempt"], rec["problem"], rec["call"], rec["notes"]),
                         ("T02", 1, "verify_failed", "009-brain-T02-opus", []))
        ev = self.events("jev.decision")[-1]
        self.assertEqual((ev["task_id"], ev["applied"], ev["brain"]), ("T02", "retry", "opus"))
        self.assertEqual(self.events("task.update")[-1]["status"], "ready")
        self.assertIn("◆ Jev'in kararı (Opus): T02 tekrar dene — test: retry", self.runner.term.text())
        saved = json.loads((self.runner.rdir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(next(x for x in saved["tasks"] if x["id"] == "T02")["status"], "ready")

    def test_reassign_rolls_back(self):
        p = self.runner.project
        t = self.failing(checkpoint=gitops.head(p))
        (p / "yarim.py").write_text("x = 1\n", encoding="utf-8")
        (p / "AGENTS.md").write_text("bozuldu\n", encoding="utf-8")
        self.apply(t, decision("reassign", agent="sonnet", effort="high"))
        self.assertEqual(t["forced"], {"agent": "sonnet", "effort": "high"})
        self.assertFalse((p / "yarim.py").exists())
        self.assertNotEqual((p / "AGENTS.md").read_text(encoding="utf-8"), "bozuldu\n")
        self.assertTrue(gitops.is_clean(p))
        self.assertFalse(t["keep_changes"])
        self.assertIn("◆ Jev'in kararı (Opus): T02 başka ajana ver → Sonnet", self.runner.term.text())

    def test_keep_changes(self):
        p = self.runner.project
        head = gitops.head(p)
        self.addCleanup(gitops.rollback, p, head, self.runner.state.get("branch"))
        t = self.failing(checkpoint=head)
        (p / "yarim.py").write_text("x = 1\n", encoding="utf-8")
        self.apply(t, decision("reassign", agent="opus", rollback=False))
        self.assertTrue((p / "yarim.py").exists())
        self.assertTrue(t["keep_changes"])
        self.assertEqual(t["forced"], {"agent": "opus", "effort": None})
        self.assertEqual(self.records()[-1]["notes"], ["Önceki denemenin değişiklikleri korunarak devam ediliyor."])

    def test_invalid_targets_are_dropped(self):
        real = self.runner.cfg.has_role
        cases = (("gpt-yok", "Bilinmeyen ajan 'gpt-yok'; ajan seçimi Jev'e bırakıldı."),
                 ("luna", "Luna işçi olarak çalışamaz; ajan seçimi Jev'e bırakıldı."))
        for target, note in cases:
            with self.subTest(target=target):
                self.reset()
                t = self.failing()
                # son durum: Luna'nın işçi rolü ayardan kaldırılmış gibi
                with mock.patch.object(self.runner.cfg, "has_role",
                                       side_effect=lambda a, r: False if a == "luna" else real(a, r)):
                    self.apply(t, decision("reassign", agent=target))
                self.assertEqual((t["status"], t["forced"]), ("ready", None))
                rec = self.records()[-1]
                self.assertEqual((rec["agent"], rec["notes"]), (None, [note]))
                self.assertIsNone(self.events("jev.decision")[-1]["agent"])
                self.assertNotIn("→", self.runner.term.text())

    def test_exhausted_allows_only_skip_pause_abort(self):
        t = self.failing(attempt_count=4)
        self.apply(t, decision("reassign", agent="sonnet"), exhausted=True)
        self.assertEqual((t["status"], t["forced"]), ("failed", None))
        rec = self.records()[-1]
        self.assertEqual(rec["applied"], "failed")
        self.assertEqual(rec["notes"], ["Deneme hakkı bittiği hâlde 'reassign' kararı verildi; görev başarısız sayıldı."])
        self.assertIn("✗ T02 başarısız: deneme hakkı bitti.", self.runner.term.text())

    def test_skip_covering_task_becomes_major(self):
        t = self.failing()  # T02, SC2'yi karşılıyor
        self.apply(t, decision("skip", deviation="minor", report_note="Depolama eksik kaldı."))
        self.assertEqual(t["status"], "skipped")
        rec = self.records()[-1]
        self.assertEqual((rec["deviation"], rec["applied"]), ("major", "skipped"))
        self.assertIn("Başarı ölçütü karşılayan görev atlandı: sapma 'major' yapıldı.", rec["notes"])
        self.assertEqual(self.runner.state["deviations"][-1], {"task_id": "T02", "decision": "skip", "deviation": "major",
                                                            "note": "Depolama eksik kaldı."})
        self.assertIn("! T02 atlandı (sapma: major).", self.runner.term.text())

    def test_skip_without_criteria_keeps_deviation(self):
        t = self.failing("T01", agent="luna")  # hiçbir ölçütü karşılamıyor
        self.apply(t, decision("skip"))
        self.assertEqual(t["status"], "skipped")
        self.assertEqual(self.records()[-1]["deviation"], "none")
        self.assertEqual(self.runner.state.get("deviations") or [], [])

    def test_pause(self):
        t = self.failing()
        with self.assertRaises(RunPaused) as cm:
            self.apply(t, decision("pause", guidance="Kullanıcıya sor", rationale="Gereksinim belirsiz."))
        self.assertEqual((str(cm.exception), cm.exception.kind),
                         ("Jev koşuyu duraklattı (T02): Gereksinim belirsiz.", "karar"))
        self.assertEqual((t["status"], t["guidance"]), ("ready", "Kullanıcıya sor"))
        self.assertEqual(self.records()[-1]["applied"], "pause")

    def test_abort(self):
        t = self.failing()
        with self.assertRaises(RunAborted) as cm:
            self.apply(t, decision("abort", rationale="Plan uygulanamaz."))
        self.assertEqual(str(cm.exception), "Jev koşuyu iptal etti (T02): Plan uygulanamaz.")
        self.assertEqual((t["status"], self.records()[-1]["applied"]), ("failed", "abort"))

    def test_unknown_decision_uses_simple_rule(self):
        t = self.failing()
        self.apply(t, decision("dans"))
        recs = self.records()
        self.assertEqual([r["applied"] for r in recs], ["gecersiz", "reassign"])
        self.assertEqual(recs[0]["notes"], ["Bilinmeyen karar 'dans'; basit kural uygulandı."])
        self.assertEqual((recs[1]["brain"], recs[1]["agent"]), ("kural", "sonnet"))
        self.assertEqual((t["status"], t["forced"]), ("ready", {"agent": "sonnet", "effort": None}))
        self.assertTrue(t["simple_rule_used"])


class SplitAndRevise(_PlannedRun):
    def sub(self, tid: str, *, covers: list, deps: list) -> dict:
        t03 = self.task("T03")
        s = {k: copy.deepcopy(t03[k]) for k in brain.TASK_FIELDS if k in t03}
        s.update(id=tid, title=f"{tid} alt görevi", covers_criteria=covers, depends_on=deps)
        return s

    def test_split(self):
        st = self.runner.state
        t = self.failing("T03")
        subs = [self.sub("T03a", covers=["SC1", "SC3"], deps=["T02"]), self.sub("T03b", covers=["SC4"], deps=["T03a"])]
        self.apply(t, decision("split", new_tasks=subs, rationale="Görev çok büyük."))
        self.assertEqual([x["id"] for x in st["tasks"]], ["T01", "T02", "T03", "T03a", "T03b", "T04", "T05", "T06"])
        self.assertEqual((t["status"], t["children"], t["forced"]), ("split", ["T03a", "T03b"], None))
        for s in st["tasks"][3:5]:
            self.assertEqual((s["status"], s["origin"], s["parent"], s["attempt_count"]), ("pending", "split", "T03", 0))
        for tid in ("T04", "T05", "T06"):
            self.assertEqual(self.task(tid)["depends_on"], ["T03a", "T03b"])
        rec = self.records()[-1]
        self.assertEqual((rec["applied"], rec["new_tasks"]), ("split", ["T03a", "T03b"]))
        self.assertEqual([e["task_id"] for e in self.events("task.update")], ["T03", "T03a", "T03b"])
        self.assertIn("T03 bölündü: T03a, T03b", self.runner.term.text())

    def test_split_losing_criteria_falls_back(self):
        t = self.failing("T03")
        self.apply(t, decision("split", new_tasks=[self.sub("T03a", covers=["SC1"], deps=["T02"])]))
        recs = self.records()
        self.assertEqual([r["applied"] for r in recs], ["gecersiz", "reassign"])
        self.assertTrue(recs[0]["notes"][0].startswith("Bölme geçersiz: "))
        self.assertIn("Alt görevler şu ölçütleri kapsamıyor: SC3, SC4.", recs[0]["notes"][0])
        self.assertEqual(t["status"], "ready")
        self.assertNotIn("T03a", [x["id"] for x in self.runner.state["tasks"]])
        self.assertEqual(self.task("T04")["depends_on"], ["T03"])

    def test_split_child_cannot_depend_on_parent(self):
        t = self.failing("T03")
        self.apply(t, decision("split", new_tasks=[self.sub("T03a", covers=["SC1", "SC3", "SC4"], deps=["T03"])]))
        self.assertIn("T03a bölünen göreve (T03) bağımlı olamaz.", self.records()[0]["notes"][0])
        self.assertEqual(t["status"], "ready")

    def test_split_without_tasks(self):
        t = self.failing("T03")
        self.apply(t, decision("split", new_tasks=[]))
        self.assertEqual(self.records()[0]["notes"], ["Bölme geçersiz: new_tasks boş."])

    def test_revise_keeps_criteria_and_id(self):
        t = self.failing()
        self.apply(t, decision("revise", revised_task={"id": "X99", "description": "Yeni tanım", "complexity": "L",
                                                        "covers_criteria": []}))
        self.assertEqual((t["id"], t["description"], t["complexity"], t["covers_criteria"], t["revised"], t["status"]),
                         ("T02", "Yeni tanım", "L", ["SC2"], 1, "ready"))
        self.assertIn("covers_criteria daraltılamaz; eski ölçütler korundu.", self.records()[-1]["notes"])

    def test_revise_with_bad_dependencies_keeps_old_ones(self):
        t = self.failing()
        self.apply(t, decision("revise", revised_task={"description": "Yeni tanım", "depends_on": ["T99"]}))
        self.assertEqual((t["description"], t["depends_on"]), ("Yeni tanım", ["T01"]))
        self.assertIn("Yeni bağımlılıklar geçersizdi; eski bağımlılıklar korundu.", self.records()[-1]["notes"])
        # döngü kuran bağımlılık da geri alınır (T04 → T03 → T02)
        self.apply(t, decision("revise", revised_task={"depends_on": ["T04"]}))
        self.assertEqual((t["depends_on"], t["revised"]), (["T01"], 2))

    def test_revise_without_task_retries_same_definition(self):
        t = self.failing()
        before = {k: copy.deepcopy(t.get(k)) for k in brain.TASK_FIELDS}
        self.apply(t, decision("revise", revised_task=None, agent="luna"))
        self.assertEqual({k: t.get(k) for k in brain.TASK_FIELDS}, before)
        self.assertEqual(self.records()[-1]["notes"],
                         ["Düzeltilmiş görev geçersiz: revised_task boş. Görev aynı tanımla yeniden denenecek."])
        self.assertEqual((t["status"], t["forced"]), ("ready", {"agent": "luna", "effort": None}))


class SimpleRule(_PlannedRun):
    def test_reassigns_once_widening_to_all_workers(self):
        for failed, pick in ((["sol"], "sonnet"), (["sol", "sonnet"], "luna")):
            with self.subTest(failed=failed):
                self.reset()
                t = self.failing(failed_agents=failed)
                dec = brain.simple_rule(self.runner, t, exhausted=False)
                self.assertEqual((dec["decision"], dec["agent"]), ("reassign", pick))
                self.assertTrue(t["simple_rule_used"])
                self.assertEqual(brain.simple_rule(self.runner, t, exhausted=False)["decision"], "pause")  # bir kez

    def test_never_escalates(self):
        """Opus'a (eskalasyon) yalnızca Jev'in kararı iş verir; işçiler tükenince basit kural duraklatır."""
        t = self.failing(failed_agents=["sol", "sonnet", "luna"])
        dec = brain.simple_rule(self.runner, t, exhausted=False)
        self.assertEqual((dec["decision"], dec["agent"]), ("pause", None))
        self.assertFalse(t.get("simple_rule_used"))

    def test_skips_cooling_agents(self):
        self.runner.quota.mark("sonnet", now() + dt.timedelta(hours=1), "test")
        self.assertEqual(brain.simple_rule(self.runner, self.failing(), exhausted=False)["agent"], "luna")

    def test_pause_names_when_brain_returns(self):
        run = self.runner
        t = self.failing()
        dec = brain.simple_rule(run, t, exhausted=True)
        self.assertEqual((dec["decision"], dec["agent"]), ("pause", None))
        self.assertEqual(dec["rationale"], "Jev'in beyni müsait değil ve basit kural tükendi; koşu duraklatıldı.")
        soon, later = now() + dt.timedelta(minutes=50), now() + dt.timedelta(hours=3)
        run.quota.mark("opus", later, "test")
        run.quota.mark("sol", soon, "test")
        dec = brain.simple_rule(run, t, exhausted=True)
        self.assertIn(f" Beyin ~{local_hhmm(soon)}", dec["rationale"])
        self.assertTrue(dec["rationale"].endswith(" müsait olur."))


class Consult(_PlannedRun):
    def consult(self, results: list, exhausted: bool = False):
        calls = []

        def fake(agent, phase, prompt, **kw):
            calls.append((agent, phase, prompt, kw))
            return results.pop(0)

        # metin yazan beyin yolu (Jev modeli kapalı); Jev modelli yol: JevModel sınıfı
        with mock.patch.object(self.runner.gw, "call", side_effect=fake), \
                mock.patch.object(self.runner.jev, "on", False):
            out = brain.consult(self.runner, self.failing(), "verify_failed", "2 test kaldı", exhausted)
        return out, calls

    def test_opus_decides(self):
        dec = decision("reassign", agent="sonnet")
        out, calls = self.consult([ok_result(dec)])
        self.assertEqual(out, (dec, "opus", "007-brain-T02-opus"))
        agent, phase, prompt, kw = calls[0]
        self.assertEqual((agent, phase, kw["schema_name"], kw["readonly"], kw["effort"], kw["task_id"]),
                         ("opus", "brain", "jev_decision", True, "high", "T02"))
        self.assertEqual(kw["extra"], {"tried": ["sol"], "available": ["sol", "sonnet", "luna", "opus"],
                                       "problem": "verify_failed",
                                       "remaining_attempts": self.runner.cfg.limit("gorev_basina_deneme") - 1})
        self.assertIn("MÜSAİT AJANLAR: sol, sonnet, luna, opus (eskalasyon)", prompt)
        self.assertEqual(self.runner.state["brain_calls"], 1)
        self.assertEqual(self.events("jev.consult")[-1]["brain"], "opus")
        self.assertIn("? T02: doğrulama başarısız → Jev, Opus'a danışıyor", self.runner.term.text())

    def test_worker_opus_is_not_marked(self):
        with mock.patch.object(self.runner, "cfg", _ortak.make_config(**OPUS_WORKER)):
            _, calls = self.consult([ok_result(decision("retry"))])
        self.assertIn("MÜSAİT AJANLAR: opus, sol, sonnet, luna · ", calls[0][2])

    def test_falls_back_to_sol(self):
        out, calls = self.consult([AgentResult(ok=False, error_kind="crash", error_text="çöktü"),
                                   ok_result(decision("retry"), "009-brain-T02-sol")])
        self.assertEqual(out[1:], ("sol", "009-brain-T02-sol"))
        self.assertEqual([(a, kw["effort"]) for a, _, _, kw in calls], [("opus", "high"), ("sol", "xhigh")])
        self.assertEqual(self.runner.state["brain_calls"], 2)
        self.assertTrue(any(e["text"].startswith("Beyin (Opus) karar veremedi: crash") for e in self.events("log")))

    def test_no_decision_from_either_brain(self):
        out, calls = self.consult([AgentResult(ok=False, error_kind="schema"), AgentResult(ok=False, error_kind="timeout")])
        self.assertEqual((out, len(calls)), ((None, "kural", None), 2))

    def test_cooling_brains_are_skipped(self):
        run = self.runner
        run.quota.mark("opus", now() + dt.timedelta(hours=2), "test")
        out, calls = self.consult([ok_result(decision("retry"), "005-brain-T02-sol")])
        self.assertEqual(([c[0] for c in calls], out[1]), (["sol"], "sol"))
        self.assertNotIn("opus", calls[0][3]["extra"]["available"])
        run.quota.mark("sol", now() + dt.timedelta(hours=2), "test")
        out, calls = self.consult([])
        self.assertEqual((out, calls), ((None, "kural", None), []))
        logs = [e["text"] for e in self.events("log")]
        for name in ("Opus", "Sol"):
            self.assertTrue(any(x.startswith(f"Beyin {name} soğumada") for x in logs), name)

    def test_brain_limit_pauses_run(self):
        run = self.runner
        limit = run.cfg.limit("kosu_basina_beyin_cagrisi")
        run.state["brain_calls"] = limit
        with self.assertRaises(RunPaused) as cm:
            self.consult([])
        self.assertEqual(cm.exception.kind, "beyin_siniri")
        self.assertIn(f"Koşu başına beyin çağrısı sınırı ({limit}) doldu.", cm.exception.reason)
        run.state["brain_limit_extra"] = brain.BRAIN_LIMIT_EXTRA  # `jev devam` ek hak verir
        out, _ = self.consult([ok_result(decision("retry"))])
        self.assertEqual((out[1], run.state["brain_calls"]), ("opus", limit + 1))

    def test_cancelled_call_interrupts(self):
        with self.assertRaises(KeyboardInterrupt):
            self.consult([AgentResult(ok=False, error_kind="cancelled")])


class HandleProblem(_PlannedRun):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(self.runner.jev, "on", False)  # metin yazan beyin yolu
        p.start()
        self.addCleanup(p.stop)

    def test_mock_brain_decision_is_applied(self):
        t = self.failing()
        brain.handle_problem(self.runner, t, "verify_failed", "test_remove kaldı")
        self.assertEqual((t["status"], t["forced"]), ("ready", {"agent": "sonnet", "effort": "high"}))
        rec = self.records()[-1]
        self.assertEqual((rec["brain"], rec["decision"], rec["applied"]), ("opus", "reassign", "reassign"))
        self.assertRegex(rec["call"], r"^\d{3}-brain-T02-opus$")
        self.assertTrue((self.runner.rdir / "calls" / f"{rec['call']}.prompt.md").exists())
        self.assertEqual(self.runner.state["brain_calls"], 1)

    def test_without_brain_rule_pauses(self):
        run = self.runner
        until = now() + dt.timedelta(hours=2)
        for a in ("opus", "sol"):
            run.quota.mark(a, until, "test")
        t = self.failing(simple_rule_used=True)
        with self.assertRaises(RunPaused) as cm:
            brain.handle_problem(run, t, "verify_failed", "x")
        self.assertTrue(cm.exception.reason.startswith("Jev koşuyu duraklattı (T02): Jev'in beyni müsait değil"))
        self.assertIn(local_hhmm(until), cm.exception.reason)
        self.assertEqual(cm.exception.kind, "karar")
        self.assertEqual((self.records()[-1]["brain"], run.state.get("brain_calls", 0)), ("kural", 0))


if __name__ == "__main__":
    unittest.main()
