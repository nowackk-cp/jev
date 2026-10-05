"""Jev'in kural katmanı: aday listesi, ajan seçimi ve gerekçeleri, sağlayıcı dengesi, görev sırası."""
import datetime as dt
import unittest

import _ortak
from jev.routing import (Choice, candidates, choose_agent, dep_status, note_dispatch, ordered_ready, progress,
                         refresh)

CFG = _ortak.make_config()
T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)


def task(tid="T1", **kw):
    t = {"id": tid, "type": "code", "complexity": "M", "status": "ready", "depends_on": []}
    t.update(kw)
    return t


def everyone(_agent):
    return True


def nobody(_agent):
    return None


def only(*names):
    return lambda a: a in names


def choose(t, state=None, available=everyone, until=nobody, cfg=CFG):
    return choose_agent(t, state if state is not None else {}, cfg, available, until)


class CandidateTests(unittest.TestCase):
    def test_complexity_lists(self):
        self.assertEqual(candidates(task(complexity="M"), CFG), ["sol", "sonnet"])
        self.assertEqual(candidates(task(complexity="L"), CFG), ["opus", "sol", "sonnet"])
        self.assertEqual(candidates(task(complexity="S"), CFG), ["luna", "sonnet", "sol"])
        self.assertEqual(candidates(task(complexity=None), CFG), ["sol", "sonnet"])

    def test_type_list_wins_over_complexity(self):
        self.assertEqual(candidates(task(type="docs", complexity="L"), CFG), ["luna", "sonnet"])
        self.assertEqual(candidates(task(type="test"), CFG), ["sonnet", "sol"])
        self.assertEqual(candidates(task(type="config"), CFG), ["luna", "sol"])

    def test_suggestion_first_and_only_workers(self):
        self.assertEqual(candidates(task(suggested_agent="sonnet"), CFG), ["sonnet", "sol"])
        # Opus yalnızca L görevlerde işçi: kolay görevi plan önerse de yalnızca Jev'in kararıyla alır
        self.assertEqual(candidates(task(suggested_agent="opus", complexity="M"), CFG), ["sol", "sonnet"])
        self.assertEqual(candidates(task(suggested_agent="opus", complexity="L"), CFG), ["opus", "sol", "sonnet"])
        for bad in ("astra", "gpt-yok"):  # kaldırılan ya da hiç olmayan ajan
            self.assertEqual(candidates(task(suggested_agent=bad), CFG), ["sol", "sonnet"], bad)

    def test_opus_as_worker(self):
        # kullanıcı Opus'u her boyda işçi yaparsa önerildiği görevde listenin başına geçer
        cfg = _ortak.make_config(ajanlar={"opus": {"roller": ["planlayici", "beyin", "isci"],
                                                   "gorev_boyu": ["S", "M", "L"]}},
                                 yonlendirme={"L": ["sol", "sonnet", "opus"]})
        self.assertEqual(candidates(task(suggested_agent="opus"), cfg), ["opus", "sol", "sonnet"])
        self.assertEqual(candidates(task(complexity="L"), cfg), ["sol", "sonnet", "opus"])


class ChooseTests(unittest.TestCase):
    def test_first_free_in_complexity_list(self):
        self.assertEqual(choose(task()), Choice("sol", "high", "M listesinde ilk müsait ajan"))
        c = choose(task(complexity="S"))
        self.assertEqual((c.agent, c.effort, c.reason), ("luna", "medium", "S listesinde ilk müsait ajan"))

    def test_type_list_reason(self):
        c = choose(task(type="docs", complexity="S"))
        self.assertEqual((c.agent, c.effort, c.reason), ("luna", "medium", "tür listesinde ilk müsait ajan"))

    def test_suggested_agent(self):
        c = choose(task(suggested_agent="sonnet"))
        self.assertEqual((c.agent, c.reason), ("sonnet", "planın önerisi"))

    def test_plain_suggestion_still_balanced(self):
        c = choose(task(suggested_agent="sonnet"), {"provider_streak": {"provider": "claude", "count": 2}})
        self.assertEqual((c.agent, c.reason), ("sol", "sağlayıcı dengesi (claude art arda 2 görev aldı)"))

    def test_forced_opus(self):
        c = choose(task(complexity="L", forced={"agent": "opus", "effort": None}))
        self.assertEqual(c, Choice("opus", "xhigh", "Jev'in kararı"))

    def test_cooling_agent_is_skipped(self):
        c = choose(task(), available=only("sonnet", "luna"))
        self.assertEqual((c.agent, c.reason), ("sonnet", "M listesinde ilk müsait ajan"))

    def test_forced_agent_and_effort(self):
        c = choose(task(forced={"agent": "sonnet", "effort": "xhigh"}))
        self.assertEqual(c, Choice("sonnet", "xhigh", "Jev'in kararı"))
        c = choose(task(complexity="L", forced={"agent": "sol", "effort": None}))
        self.assertEqual((c.agent, c.effort), ("sol", "xhigh"))

    def test_forced_agent_cooling(self):
        c = choose(task(forced={"agent": "sonnet", "effort": "low"}), available=only("sol", "luna"))
        self.assertEqual((c.agent, c.reason), ("sol", "Jev'in seçtiği sonnet soğumada; sıradaki uygun ajan"))
        self.assertEqual(c.effort, "high")  # seçilen ajan başka; zorlanan efor ona geçmez

    def test_forced_effort_only(self):
        c = choose(task(forced={"agent": None, "effort": "xhigh"}))
        self.assertEqual((c.agent, c.effort, c.reason), ("sol", "xhigh", "M listesinde ilk müsait ajan"))

    def test_failed_agents(self):
        c = choose(task(failed_agents=["sol"]))
        self.assertEqual((c.agent, c.reason), ("sonnet", "M listesinde ilk müsait ajan"))
        c = choose(task(failed_agents=["sol", "sonnet"]))
        # listenin dışındaki işçiler ayar sırasıyla; eskalasyon ajanı (Opus) yalnızca Jev'in kararıyla
        self.assertEqual((c.agent, c.reason), ("luna", "Listedeki ajanlar bu görevde başarısız oldu; başka bir işçi"))

    def test_everyone_cooling(self):
        waits = {"sol": T0 + dt.timedelta(minutes=50), "sonnet": T0 + dt.timedelta(minutes=20)}
        c = choose(task(), available=only(), until=waits.get)
        self.assertEqual(c, Choice(None, "", "Uygun ajanların hepsi soğumada.", T0 + dt.timedelta(minutes=20)))
        c = choose(task(), available=only(), until=nobody)
        self.assertIsNone(c.agent)
        self.assertIsNone(c.wait_until)

    def test_forced_agent_wait_counts(self):
        waits = {"sol": T0 + dt.timedelta(hours=2), "sonnet": T0 + dt.timedelta(hours=3),
                 "luna": T0 + dt.timedelta(minutes=5)}
        c = choose(task(forced={"agent": "luna"}), available=only(), until=waits.get)
        self.assertEqual(c.wait_until, T0 + dt.timedelta(minutes=5))

    def test_provider_balance(self):
        state = {"provider_streak": {"provider": "codex", "count": 2}}
        c = choose(task(), state)
        self.assertEqual((c.agent, c.effort), ("sonnet", "high"))
        self.assertEqual(c.reason, "sağlayıcı dengesi (codex art arda 2 görev aldı)")

    def test_provider_balance_not_yet_or_no_alternative(self):
        c = choose(task(), {"provider_streak": {"provider": "codex", "count": 1}})
        self.assertEqual(c.agent, "sol")
        c = choose(task(), {"provider_streak": {"provider": "codex", "count": 5}}, available=only("sol", "luna"))
        self.assertEqual((c.agent, c.reason), ("sol", "M listesinde ilk müsait ajan"))

    def test_provider_balance_disabled(self):
        cfg = _ortak.make_config(yonlendirme={"saglayici_dengesi": 0})
        c = choose(task(), {"provider_streak": {"provider": "codex", "count": 9}}, cfg=cfg)
        self.assertEqual(c.agent, "sol")

    def test_note_dispatch(self):
        st = {}
        note_dispatch(st, "codex")
        note_dispatch(st, "codex")
        self.assertEqual(st["provider_streak"], {"provider": "codex", "count": 2})
        note_dispatch(st, "claude")
        self.assertEqual(st["provider_streak"], {"provider": "claude", "count": 1})


class QueueTests(unittest.TestCase):
    def test_dep_status(self):
        tasks = {"A": {"status": "done"}, "B": {"status": "skipped"}, "C": {"status": "running"},
                 "D": {"status": "failed"}, "E": {"status": "blocked"}}
        self.assertEqual(dep_status(task(depends_on=["A", "B", "YOK"]), tasks), "ok")
        self.assertEqual(dep_status(task(depends_on=["A", "C"]), tasks), "wait")
        self.assertEqual(dep_status(task(depends_on=["C", "D"]), tasks), "broken")
        self.assertEqual(dep_status(task(depends_on=["E"]), tasks), "broken")

    def test_refresh(self):
        st = {"tasks": [
            task("A", status="pending"),
            task("B", status="pending", depends_on=["A"]),
            task("C", status="ready", depends_on=["D"]),
            task("D", status="running"),
            task("E", status="pending", depends_on=["F"]),
            task("F", status="failed"),
            task("G", status="waiting_quota", depends_on=["H"]),
            task("H", status="blocked"),
            task("I", status="pending", depends_on=["J"]),
            task("J", status="skipped"),
            task("K", status="pending", depends_on=["YOK"]),
            task("L", status="done", depends_on=["F"]),
        ]}
        changed = refresh(st)
        self.assertEqual(changed, [("A", "ready"), ("C", "pending"), ("E", "blocked"), ("G", "blocked"),
                                   ("I", "ready"), ("K", "ready")])
        by = {t["id"]: t for t in st["tasks"]}
        self.assertEqual(by["B"]["status"], "pending")
        self.assertEqual(by["L"]["status"], "done")
        self.assertEqual(by["E"]["blocked_reason"], "Bağımlı olduğu görev başarısız oldu ya da engellendi.")
        self.assertEqual(refresh(st), [])

    def test_ordered_ready(self):
        st = {"tasks": [
            task("T1", complexity="M"),
            task("T2", complexity="S"),
            task("T3", complexity="L"),
            task("T4", status="pending", depends_on=["T3"]),
            task("T5", status="waiting_quota", complexity="M"),
            task("T6", status="done"),
        ]}
        self.assertEqual([t["id"] for t in ordered_ready(st)], ["T3", "T2", "T1", "T5"])

    def test_progress(self):
        st = {"tasks": [task(str(i), status=s) for i, s in enumerate(
            ["done", "skipped", "failed", "blocked", "split", "ready", "running"])]}
        self.assertEqual(progress(st), (4, 6))
        self.assertEqual(progress({}), (0, 0))


if __name__ == "__main__":
    unittest.main()
