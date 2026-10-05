"""Jev'in kendi modeli (TypeSafe Jev, jev/systemone.py): istemci, karar ve dağıtım.

Ağa hiç çıkılmaz: gerçek istemci sahte bir `opener` ile, koşu içindeki model de sahte `ask` ile sınanır.
"""
import copy
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

import _ortak
from jev import routing, systemone
from jev.adapters.base import AgentResult, child_env
from jev.phases import brain
from jev.util import read_json, read_jsonl

A = systemone.Answer


class _Resp:
    def __init__(self, body: dict):
        self.raw = json.dumps(body).encode("utf-8")

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code: int, text: str = "hata") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", code, "x", {}, io.BytesIO(text.encode()))


class Client(unittest.TestCase):
    QS = {"karar": systemone.choice("Ne yapılmalı?", {"retry": "tekrar", "skip": "atla"}),
          "zor": systemone.score("Ne kadar zor?", ["kolay", "orta", "zor"]),
          "geri_al": systemone.noul("Geri alınsın mı?", "evet", "hayır")}
    OK = {"model": "jev-1.13.0", "usage": {"input_tokens": 2_000_000, "output_tokens": 30},
          "answers": {"karar": {"choice": "skip", "probabilities": {"retry": 0.2, "skip": 0.8}, "confidence": 0.7},
                      "zor": {"score": 1.4, "legend": {}, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
                              "confidence": 0.5},
                      "geri_al": {"noul": 0.9}}}

    def client(self, responses: list, key: str | None = "gizli-anahtar"):
        sent, sleeps = [], []

        def opener(req, timeout):
            sent.append((req, timeout))
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return _Resp(r)
        rdir = _ortak.temp_dir("model")
        env = {"TYPESAFE_API_KEY": key} if key else {}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        c = systemone.SystemOne(_ortak.make_config(), rdir, opener=opener, sleep=sleeps.append)
        return c, sent, sleeps, rdir

    def test_request_and_answers(self):
        c, sent, _, rdir = self.client([self.OK])
        res = c.ask("karar", {"gorev": "T01"}, self.QS, task_id="T01")
        self.assertTrue(res.ok)
        req, timeout = sent[0]
        self.assertEqual((req.full_url, req.get_method(), timeout),
                         ("https://api.typesafe.ai/v1/systemone", "POST", 20.0))
        self.assertEqual(req.get_header("Authorization"), "Bearer gizli-anahtar")
        body = json.loads(req.data)
        self.assertEqual((body["model"], body["state"], set(body["questions"])),
                         ("jev-latest", {"gorev": "T01"}, {"karar", "zor", "geri_al"}))
        self.assertEqual((res.answers["karar"].value, res.answers["karar"].p(), res.answers["karar"].confidence),
                         ("skip", 0.8, 0.7))
        self.assertEqual(res.answers["karar"].ranked()[0], ("skip", 0.8))
        self.assertEqual(res.answers["zor"].value, 1.4)
        self.assertAlmostEqual(res.answers["geri_al"].confidence, 0.8)
        rec = read_jsonl(rdir / "jev-model.jsonl")[-1]
        self.assertEqual((rec["purpose"], rec["task_id"], rec["model"], rec["ok"]), ("karar", "T01", "jev-1.13.0", True))
        u = read_json(rdir / "usage.json")["jev"]
        self.assertEqual((u["calls"], u["input_tokens"], u["cost_usd"]), (1, 2_000_000, 0.084))

    def test_off_without_key(self):
        c, sent, _, _ = self.client([], key=None)
        self.assertFalse(c.enabled)
        self.assertIn("TYPESAFE_API_KEY", c.status())
        res = c.ask("karar", {}, self.QS)
        self.assertEqual((res.ok, res.error_kind, sent), (False, "off", []))

    def test_busy_is_retried_with_backoff(self):
        c, sent, sleeps, _ = self.client([http_error(529), http_error(429), self.OK])
        self.assertTrue(c.ask("karar", {}, self.QS).ok)
        self.assertEqual((len(sent), sleeps), (3, [1.0, 2.0]))

    def test_gives_up_after_tries(self):
        c, sent, _, _ = self.client([http_error(529)] * 3)
        res = c.ask("karar", {}, self.QS)
        self.assertEqual((res.ok, res.error_kind, len(sent)), (False, "limit", 3))
        self.assertIn("529", c.last_error)

    def test_bad_key_is_not_retried(self):
        c, sent, _, _ = self.client([http_error(401, "unauthorized")])
        res = c.ask("karar", {}, self.QS)
        self.assertEqual((res.error_kind, len(sent)), ("auth", 1))

    def test_unknown_option_is_invalid(self):
        bad = copy.deepcopy(self.OK)
        bad["answers"]["karar"]["choice"] = "yak"
        c, _, _, _ = self.client([bad])
        self.assertEqual(c.ask("karar", {}, self.QS).error_kind, "invalid")

    def test_network_error(self):
        c, _, _, _ = self.client([urllib.error.URLError("yok")] * 3)
        self.assertEqual(c.ask("karar", {}, self.QS).error_kind, "network")

    def test_key_never_reaches_agents(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "gizli", "TYPESAFE_X": "1"}):
            env = child_env()
        self.assertFalse([k for k in env if k.upper().startswith("TYPESAFE")])

    def test_config_validation(self):
        from jev.config import ConfigError
        with self.assertRaisesRegex(ConfigError, "guven_esigi"):
            _ortak.make_config(jev={"model": {"guven_esigi": 2}})


class Picker(unittest.TestCase):
    def choose(self, task, picker, **kw):
        return routing.choose_agent(task, {"tasks": []}, _ortak.make_config(), available=lambda a: True,
                                    until=lambda a: None, picker=picker, **kw)

    def test_jev_picks_among_free(self):
        seen = []

        def picker(task, free, rule, why):
            seen.append((free, rule))
            return "sonnet", "Jev modeli seçti"
        c = self.choose({"id": "T01", "complexity": "M"}, picker)
        self.assertEqual((c.agent, c.reason), ("sonnet", "Jev modeli seçti"))
        self.assertEqual(seen[0], (["sol", "sonnet"], "sol"))

    def test_rule_stays_when_jev_declines_or_is_not_asked(self):
        c = self.choose({"id": "T01", "complexity": "M"}, lambda *a: None)
        self.assertEqual(c.agent, "sol")
        called = []
        forced = {"id": "T01", "complexity": "M", "forced": {"agent": "sonnet", "effort": None}}
        c = self.choose(forced, lambda *a: called.append(a))
        self.assertEqual((c.agent, called), ("sonnet", []))
        # Jev'in listede olmayan bir ajanı (eskalasyon ajanı Opus ya da hiç olmayan bir ajan) seçmesi yok sayılır
        for bad in ("opus", "gpt-yok"):
            c = self.choose({"id": "T01", "complexity": "M"}, lambda *a, b=bad: (b, "x"))
            self.assertEqual(c.agent, "sol", bad)


def answers(karar="reassign", conf=0.9, ajan="sonnet", ajan_probs=None, kok="kod", efor="varsayilan",
            geri_al=0.9, sapma="none") -> systemone.Result:
    probs = {karar: conf, "retry": round(1 - conf, 2)} if karar != "retry" else {"retry": conf}
    return systemone.Result(True, {
        "karar": A("choice", karar, probs, conf),
        "kok_neden": A("choice", kok, {kok: 0.7}, 0.6),
        "efor": A("choice", efor, {efor: 0.8}, 0.8),
        "geri_al": A("noul", geri_al, {"true": geri_al, "false": 1 - geri_al}, abs(2 * geri_al - 1)),
        "sapma": A("choice", sapma, {sapma: 0.9}, 0.9),
        "ajan": A("choice", ajan, ajan_probs or {ajan: 0.8}, 0.8)}, model="jev-test")


class JevDecides(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = _ortak.planned_run()
        cls.snapshot = copy.deepcopy(cls.runner.state)

    @classmethod
    def tearDownClass(cls):
        cls.runner.bus.close()

    def setUp(self):
        run = self.runner
        run.state.clear()
        run.state.update(copy.deepcopy(self.snapshot))
        run.quota.clear()

    def failing(self, **fields) -> dict:
        t = next(t for t in self.runner.state["tasks"] if t["id"] == "T02")
        t.update(status="needs_decision", agent=None, attempt_count=1, failed_agents=["sol"],
                 attempts=[{"n": 1, "agent": "sol", "counted": True, "outcome": "verify_failed"}])
        t.update(fields)
        return t

    def consult(self, jev_result, brain_results=(), exhausted=False):
        asked, calls = [], []
        results = list(brain_results)

        def ask(purpose, state, questions, **kw):
            asked.append((purpose, state, questions, kw))
            return jev_result

        def call(agent, phase, prompt, **kw):
            calls.append((agent, prompt, kw))
            return results.pop(0)
        with mock.patch.object(self.runner.jev, "ask", side_effect=ask), \
                mock.patch.object(self.runner.gw, "call", side_effect=call):
            out = brain.consult(self.runner, self.failing(), "verify_failed", "2 test kaldı\nAssertionError: 3 != 4",
                                exhausted)
        return out, asked, calls

    def test_jev_decides_alone(self):
        (dec, who, call), asked, calls = self.consult(answers("reassign", ajan="sol",
                                                              ajan_probs={"sol": 0.5, "sonnet": 0.3, "luna": 0.2}))
        self.assertEqual((who, call, calls), ("jev", None, []))
        self.assertEqual((dec["decision"], dec["agent"], dec["rollback"], dec["effort"]),
                         ("reassign", "sonnet", True, None))  # Sol zaten denendi: sıradaki en olası ajan
        self.assertIn("AssertionError: 3 != 4", dec["guidance"])
        self.assertTrue(dec["rationale"].startswith("Jev modeli: başka ajana ver (%90, güven 0.90)"))
        purpose, state, qs, kw = asked[0]
        self.assertEqual((purpose, kw["task_id"]), ("karar", "T02"))
        self.assertEqual(set(qs), {"karar", "kok_neden", "efor", "geri_al", "sapma", "ajan"})
        self.assertTrue(qs["ajan"]["criteria"]["opus"].endswith(" (eskalasyon)"))  # Opus yalnızca Jev'in kararıyla
        self.assertEqual(state["denenen_ajanlar"], ["sol"])
        self.assertEqual(self.runner.state["brain_calls"], 1)
        self.assertEqual([e["data"]["brain"] for e in self.runner.bus.since(0) if e["type"] == "jev.consult"][-1],
                         "jev")

    def test_decision_is_applied_by_jev(self):
        with mock.patch.object(self.runner.jev, "ask", return_value=answers("reassign", ajan="luna")):
            t = self.failing()
            n = len(read_jsonl(self.runner.rdir / "decisions.jsonl"))
            brain.handle_problem(self.runner, t, "verify_failed", "x kaldı")
        rec = read_jsonl(self.runner.rdir / "decisions.jsonl")[n]
        self.assertEqual((rec["brain"], rec["decision"], rec["agent"], rec["call"]), ("jev", "reassign", "luna", None))
        self.assertEqual((t["status"], t["forced"]["agent"]), ("ready", "luna"))

    def test_split_text_is_written_by_brain(self):
        text = {"decision": "retry", "agent": None, "effort": None, "rollback": True, "guidance": "g",
                "revised_task": None, "new_tasks": [{"id": "T02a"}], "rationale": "iki parça", "deviation": "none",
                "report_note": ""}
        res = AgentResult(ok=True, structured=text, paths={"stdout": "C:\\k\\calls\\008-brain-T02-opus.stdout.jsonl"})
        (dec, who, call), _, calls = self.consult(answers("split", conf=0.8), [res])
        self.assertEqual((who, call, dec["decision"], dec["new_tasks"]), ("jev", "008-brain-T02-opus", "split",
                                                                          [{"id": "T02a"}]))
        agent, prompt, _ = calls[0]
        self.assertEqual(agent, "opus")
        self.assertIn('decision alanına "split" yaz', prompt)
        self.assertIn("Görev metnini Opus yazdı: iki parça", dec["rationale"])
        self.assertEqual(self.runner.state["brain_calls"], 1)  # Jev + metin tek karar sayılır

    def test_low_confidence_goes_to_brain(self):
        text = {"decision": "retry", "agent": None, "effort": None, "rollback": True, "guidance": "",
                "revised_task": None, "new_tasks": [], "rationale": "geçici hata", "deviation": "none",
                "report_note": ""}
        res = AgentResult(ok=True, structured=text, paths={})
        (dec, who, _), _, calls = self.consult(answers("skip", conf=0.4), [res])
        self.assertEqual((who, dec["decision"]), ("opus", "retry"))
        self.assertIn("JEV'İN ÖN DEĞERLENDİRMESİ", calls[0][1])
        self.assertIn("Jev emin değildi", dec["rationale"])

    def test_low_confidence_without_brain_keeps_jev(self):
        from jev.util import now
        import datetime as dt
        for a in ("opus", "sol"):
            self.runner.quota.mark(a, now() + dt.timedelta(hours=1), "test")
        (dec, who, _), _, calls = self.consult(answers("retry", conf=0.4))
        self.assertEqual((who, dec["decision"], calls), ("jev", "retry", []))

    def test_jev_failure_falls_back_to_brain(self):
        text = {"decision": "retry", "agent": None, "rationale": "r"}
        (dec, who, _), _, calls = self.consult(systemone.Result(False, error_kind="network", error_text="yok"),
                                               [AgentResult(ok=True, structured=text, paths={})])
        self.assertEqual((who, len(calls), self.runner.state["brain_calls"]), ("opus", 1, 1))

    def test_exhausted_limits_choices(self):
        _, asked, _ = self.consult(answers("skip", sapma="major"), exhausted=True)
        self.assertEqual(set(asked[0][2]["karar"]["criteria"]), brain.EXHAUSTED_ALLOWED)

    def test_dry_run_plays_scenario(self):
        """Kuru koşuda sahte Jev modeli senaryodaki beyin kararını oynar; ağa çıkılmaz."""
        self.assertIsInstance(self.runner.jev, systemone.MockSystemOne)
        t = self.failing()
        n = len(read_jsonl(self.runner.rdir / "decisions.jsonl"))
        with mock.patch.object(self.runner.gw, "call") as call:
            brain.handle_problem(self.runner, t, "verify_failed", "test_remove kaldı")
        call.assert_not_called()
        rec = read_jsonl(self.runner.rdir / "decisions.jsonl")[n]
        self.assertEqual((rec["brain"], rec["decision"]), ("jev", "reassign"))


class Dispatch(unittest.TestCase):
    def test_execute_picker(self):
        from jev.phases.execute import _jev_picker
        run = _ortak.planned_run()
        self.addCleanup(run.bus.close)
        pick = _jev_picker(run)
        t = run.state["tasks"][0]
        res = systemone.Result(True, {"ajan": A("choice", "luna", {"luna": 0.7, "sol": 0.3}, 0.75)})
        with mock.patch.object(run.jev, "ask", return_value=res) as ask:
            self.assertEqual(pick(t, ["sol", "luna"], "sol", "kural"), ("luna", "Jev modeli seçti (%70, güven 0.75)"))
        self.assertEqual(ask.call_args.kwargs["hint"], {"ajan": "sol"})
        low = systemone.Result(True, {"ajan": A("choice", "luna", {"luna": 0.4}, 0.3)})
        with mock.patch.object(run.jev, "ask", return_value=low):
            self.assertIsNone(pick(t, ["sol", "luna"], "sol", "kural"))
        with mock.patch.object(run.jev, "dispatch", False):
            self.assertIsNone(_jev_picker(run))


if __name__ == "__main__":
    unittest.main()
