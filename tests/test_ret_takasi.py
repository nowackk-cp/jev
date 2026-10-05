"""Güvenlik reddi: Opus 5.5 bir çağrıyı yanıtlamazsa aynı çağrı Opus 4.8 ile denenir; o da yanıtlamazsa yalnızca o
çağrı (plan, karar, düzeltme, görev ya da son kontrol) ret yedeğine (Sol) geçer. Opus koşudan çıkarılmaz.
Başka bir işçi (Sonnet) kartı reddederse kart Sol'a geçer; Sonnet yalnızca o kartı bir daha almaz.
"""
import re
import unittest
from unittest import mock

import _ortak
from jev import routing
from jev.adapters.base import AgentResult
from jev.adapters.claude import ClaudeAdapter, ClaudeParser
from jev.adapters.codex import CodexAdapter
from jev.adapters.mock import REFUSAL_TEXT, load_scenario
from jev.config import ConfigError
from jev.phases.common import Refused, Unavailable, call_fixed

CALL_RX = re.compile(r"^\d{3}-([a-z_]+)-(.+)-([a-z0-9]+)\.prompt\.md$")
OPUS_55 = "opus@claude-opus-5-5"


class RecognitionTests(unittest.TestCase):
    def test_refusal_text_is_its_own_kind(self):
        cfg = _ortak.make_config()
        for adapter in (ClaudeAdapter(cfg), CodexAdapter(cfg)):
            self.assertEqual(adapter.classify(REFUSAL_TEXT), ("refusal", None))
            self.assertEqual(adapter.classify("usage limit reached")[0], "quota")
            self.assertEqual(adapter.classify("Süreç çöktü")[0], "crash")

    def test_stream_stop_reason(self):
        p = ClaudeParser(lambda act: None, _ortak.temp_dir("ret"))
        p.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"..."}],"stop_reason":"end_turn"}}')
        self.assertFalse(p.refused)
        p.feed('{"type":"assistant","message":{"content":[],"stop_reason":"refusal"}}')
        self.assertTrue(p.refused)


class ConfigTests(unittest.TestCase):
    def test_settings(self):
        cfg = _ortak.make_config()
        self.assertEqual(cfg.swap_partner, "sol")
        self.assertEqual(cfg.refusal_model("opus"), "claude-opus-4-8")
        self.assertIsNone(cfg.refusal_model("sonnet"))
        self.assertIsNone(_ortak.make_config(jev={"ret_takasi": ""}).swap_partner)
        with self.assertRaises(ConfigError):
            _ortak.make_config(jev={"ret_takasi": "astra"})

    def test_opus_takes_only_hard_cards(self):
        cfg = _ortak.make_config()
        self.assertTrue(cfg.has_role("opus", "isci"))
        self.assertEqual([cfg.takes("opus", s) for s in ("S", "M", "L")], [False, False, True])
        for size in ("S", "M"):
            self.assertNotIn("opus", routing.candidates({"complexity": size, "suggested_agent": "opus"}, cfg))
        self.assertEqual(routing.candidates({"complexity": "L"}, cfg)[0], "opus")


class CallTests(unittest.TestCase):
    def test_refusal_is_not_retried_by_the_phase(self):
        run = mock.Mock(cfg=_ortak.make_config())
        run.quota.available.return_value = True
        run.gw.call.return_value = AgentResult(ok=False, error_kind="refusal", error_text=REFUSAL_TEXT)
        with self.assertRaises(Refused) as e:
            call_fixed(run, "opus", "plan", "p", schema_name="plan", effort="high", timeout_key="plan")
        self.assertEqual(e.exception.agent, "opus")
        self.assertEqual(run.gw.call.call_count, 1)  # yedek model denemesi gateway'in içinde
        with self.assertRaises(Unavailable):
            call_fixed(run, "opus", "review", "p", schema_name="review", effort="high", timeout_key="denetim",
                       optional=True)

    def test_routing_skips_only_the_cards_refusers(self):
        cfg = _ortak.make_config()
        task = {"id": "T01", "complexity": "S", "type": "code", "ret_veren": ["luna"]}
        ch = routing.choose_agent(task, {}, cfg, lambda a: True, lambda a: None)
        self.assertNotEqual(ch.agent, "luna")
        forced = {**task, "forced": {"agent": "luna"}}
        self.assertNotEqual(routing.choose_agent(forced, {}, cfg, lambda a: True, lambda a: None).agent, "luna")
        other = {"id": "T02", "complexity": "S", "type": "code"}
        self.assertEqual(routing.choose_agent(other, {"ret_veren": ["luna"]}, cfg, lambda a: True,
                                              lambda a: None).agent, "luna")  # eski koşunun koşu çapında listesi yok sayılır


def dry_run(ret: dict):
    sc = load_scenario()
    sc["ret"] = ret
    return _ortak.make_run(speed=1000.0, scenario=sc)


def calls(run) -> list[tuple[str, str, str]]:
    return [m.groups() for m in (CALL_RX.match(p.name) for p in sorted((run.rdir / "calls").glob("*.prompt.md")))
            if m]


def drive(run) -> str:
    try:
        return run.drive()
    finally:
        run.bus.close()


@_ortak.slow
class DryRunTests(unittest.TestCase):
    def test_opus_48_takes_over_the_plan(self):
        run = dry_run({"plan": [OPUS_55]})
        self.assertEqual(drive(run), "reported")
        self.assertEqual([a for p, _, a in calls(run) if p == "plan"], ["opus", "opus"])  # 5.5, sonra 4.8
        self.assertFalse(run.state.get("retler"))

    def test_sol_writes_the_plan_when_both_opus_refuse(self):
        run = dry_run({"plan": ["opus"]})
        self.assertEqual(drive(run), "reported")
        self.assertEqual([a for p, _, a in calls(run) if p == "plan"], ["opus", "opus", "sol"])
        self.assertEqual(run.cfg.planner, "opus")  # Opus koşudan çıkarılmaz
        self.assertEqual([(r["agent"], r["asama"], r["yerine"]) for r in run.state["retler"]],
                         [("opus", "plan", "sol")])
        report = (run.rdir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- Güvenlik reddi: Opus (plan) yanıtlamadı; bu işi Sol yaptı.", report)

    def test_sol_reviews_when_opus_refuses_the_review(self):
        run = dry_run({"review": ["opus"]})
        self.assertEqual(drive(run), "reported")
        self.assertEqual([r["by"] for r in run.state["reports"]], ["sol"])

    def test_sonnet_refuses_a_card(self):
        run = dry_run({"worker": ["sonnet"]})
        self.assertEqual(drive(run), "reported")
        st = run.state
        refused = [(t["id"], a) for t in st["tasks"] for a in t.get("attempts") or [] if a.get("outcome") == "refusal"]
        self.assertTrue(refused)
        self.assertTrue(all(a["agent"] == "sonnet" and not a["counted"] for _, a in refused))
        by_id = routing.by_id(st)
        for tid, _ in refused:
            self.assertEqual(by_id[tid]["ret_veren"], ["sonnet"])
            self.assertNotEqual(by_id[tid].get("agent"), "sonnet")
        self.assertNotIn("ret_veren", st)  # koşu çapında yasak yok


if __name__ == "__main__":
    unittest.main()
