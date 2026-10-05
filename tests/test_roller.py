"""Roller: her model çağrısının ajanını ayardaki rol belirler; kapı rolü olmayan çağrıyı yan etkisiz reddeder.

Üç katmanda denetlenir:
- Kaynak: jev/ içinde hiçbir model çağrısı ajanı adıyla anmaz; plan ve düzeltme planlayıcıya, son kontrol denetçiye
  (büyük işte kodun çoğunu denetçi yazdıysa yedek denetçiye) gider. Kaldırılan aşamalar (ayrı görev bölme, ayrı
  netleştirme) ve Astra geri gelmez.
- Kapı: plan ve düzeltme yalnızca planlayıcıya, son kontrol yalnızca denetçiye ya da yedek denetçiye, karar beyne
  (ya da yedek beyne), iş işçiye gider; eskalasyon rolü olan ajan yalnızca Jev'in kararıyla iş alır. Ayar elle
  bozulsa da kapı rolü çağrı anında yeniden okur.
- Koşu: beyin kararı, kota olayı, bozuk JSON ve düzeltme turu içeren bir kuru koşuda her ajan yalnızca rolünün
  aşamalarında çağrılır.
"""
import ast
import datetime as dt
import unittest
from unittest import mock

import _ortak
from jev import routing
from jev.adapters.base import AgentResult
from jev.gateway import ForbiddenCall, Gateway
from jev.phases import brain
from jev.phases.fix import start_fix
from jev.quota import QuotaBook
from jev.util import now, read_jsonl

JEV = _ortak.ROOT / "jev"
MODEL_CALLS = {"call": 0, "call_fixed": 1}  # çağrı → ajan argümanının sırası (call_fixed'in ilk argümanı koşu)
ROLE_PHASES = ("plan", "fix", "review", "brain", "worker")


def _parse(rel: str) -> ast.AST:
    p = JEV / rel
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def model_calls() -> list[tuple[str, str, str, ast.expr]]:
    """jev/ altındaki model çağrıları: (dosya, ajan, aşama, ajan ifadesi); sabit olmayan değer '*'."""
    out = []
    for path in sorted(JEV.rglob("*.py")):
        rel = path.relative_to(JEV).as_posix()
        for node in ast.walk(_parse(rel)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name not in MODEL_CALLS or len(node.args) < MODEL_CALLS[name] + 2:
                continue
            i = MODEL_CALLS[name]
            agent, phase = (a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else "*"
                            for a in node.args[i:i + 2])
            out.append((rel, agent, phase, node.args[i]))
    return out


def _source(expr: ast.expr) -> str:
    """Atanan değerin kaynağı: cfg.reviewer → "reviewer", pick_reviewer(run, level) → "pick_reviewer"."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    return expr.attr if isinstance(expr, ast.Attribute) else getattr(expr, "id", ast.dump(expr))


def agent_sources(tree: ast.AST, fn: ast.FunctionDef, name: str, depth: int = 3) -> set[str]:
    """`fn` içinde `name` değişkenine atanan değerlerin kaynakları. Değer başka bir değişkense o da izlenir;
    değişken fonksiyonun parametresiyse aynı modülde fonksiyonu çağıranların verdiği değer izlenir."""
    if depth < 0:
        return {"?"}
    out: set[str] = set()

    def follow(scope: ast.FunctionDef, value: ast.expr) -> None:
        out.update(agent_sources(tree, scope, value.id, depth - 1) if isinstance(value, ast.Name)
                   else {_source(value)})

    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                pairs = (zip(t.elts, n.value.elts) if isinstance(t, ast.Tuple) and isinstance(n.value, ast.Tuple)
                         else [(t, n.value)])
                for target, value in pairs:
                    if isinstance(target, ast.Name) and target.id == name:
                        follow(fn, value)
    params = [a.arg for a in fn.args.args]
    if name in params:
        i = params.index(name)
        for caller in (f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)):
            for c in ast.walk(caller):
                if isinstance(c, ast.Call) and _source(c.func) == fn.name:
                    arg = c.args[i] if i < len(c.args) else next((k.value for k in c.keywords if k.arg == name), None)
                    if arg is not None:
                        follow(caller, arg)
    return out


def str_constants(value: str) -> set[str]:
    """`value` dizgesini sabit olarak içeren jev/ modülleri."""
    out = set()
    for path in sorted(JEV.rglob("*.py")):
        rel = path.relative_to(JEV).as_posix()
        if any(isinstance(n, ast.Constant) and n.value == value for n in ast.walk(_parse(rel))):
            out.add(rel)
    return out


class SourceLevel(unittest.TestCase):
    def test_model_calls_take_agent_from_config(self):
        calls = model_calls()
        self.assertEqual({a for _, a, _, _ in calls}, {"*"}, "bir model çağrısı ajanı adıyla anıyor")
        # beyin, işçi, deneme mesajı, sabit ajanlı çağrıların ortak yardımcısı, şema onarımı (kapı onarımı özgün
        # aşamayla denetler), plan, düzeltme ve son kontrol. Yeni bir çağrı yeri eklenirse bu liste gözden geçirilir.
        self.assertEqual({(f, p) for f, _, p, _ in calls},
                         {("phases/brain.py", "brain"), ("phases/execute.py", "worker"), ("cli.py", "test"),
                          ("phases/common.py", "*"), ("gateway.py", "repair"), ("phases/plan.py", "plan"),
                          ("phases/fix.py", "fix"), ("phases/review.py", "review")})

    def test_fixed_role_calls_use_their_role(self):
        """Plan ve düzeltme planlayıcının, son kontrol denetçinin (ya da yedek denetçinin) ayardaki adıyla çağrılır."""
        # son kontrol: seçen fonksiyondan (pick_reviewer) gelir; yedek çağrılamazsa denetçiye, güvenlik reddinde
        # reddetmemiş ilk ajana (refusal_reviewer: ret takasının ortağı, yedek denetçi, denetçi) döner
        want = {"plan": {"planner", "refusal_partner"}, "fix": {"planner", "refusal_partner"}, "review": {"reviewer", "pick_reviewer", "refusal_reviewer"}}
        for rel, _, phase, arg in model_calls():
            if phase not in want:
                continue
            tree = _parse(rel)
            fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.lineno <= arg.lineno <= n.end_lineno)
            with self.subTest(rel=rel, phase=phase):
                self.assertIsInstance(arg, ast.Name)
                self.assertEqual(agent_sources(tree, fn, arg.id), want[phase])
        tree = _parse("phases/review.py")
        pick = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "pick_reviewer")
        self.assertEqual(agent_sources(tree, pick, "agent"), {"reviewer", "backup_review"})
        self.assertEqual({ast.unparse(n.value) for n in ast.walk(pick) if isinstance(n, ast.Return)},
                         {"'jev'", "agent"})

    def test_removed_steps_stay_removed(self):
        self.assertFalse((JEV / "phases" / "decompose.py").exists())
        self.assertFalse((JEV / "prompts" / "decompose.md").exists())
        self.assertFalse((JEV / "schemas" / "clarification.schema.json").exists())
        # yalnızca eski koşuların kayıtlarını göstermek için (ofisteki çağrı adları ve raporu yazan)
        self.assertEqual(str_constants("decompose"), {"office/snapshot.py"})
        self.assertEqual(str_constants("netlestir"), {"office/snapshot.py"})
        self.assertEqual(str_constants("astra"), {"office/snapshot.py"})
        cfg = _ortak.make_config()
        self.assertNotIn("astra", cfg.agents)
        self.assertFalse(any(cfg.has_role(a, "parcalayici") for a in cfg.agents))


class GatewayRoles(unittest.TestCase):
    def gateway(self, cfg=None) -> Gateway:
        d = _ortak.temp_dir("kapi")
        return Gateway(cfg or _ortak.make_config(), rdir=d / "kosu", project=d, state={}, bus=mock.Mock(),
                       quota=QuotaBook(d / "kota.json"))

    def allowed(self, gw: Gateway, phase: str, escalation: bool = False) -> set[str]:
        out = set()
        for a in gw.cfg.agents:
            try:
                gw.check_allowed(a, phase, escalation=escalation)
            except ForbiddenCall:
                continue
            out.add(a)
        return out

    def test_default_roles(self):
        gw = self.gateway()
        self.assertEqual(self.allowed(gw, "plan"), {"opus", "sol"})  # sol: ret yedeği (yalnızca reddedilen çağrı)
        self.assertEqual(self.allowed(gw, "fix"), {"opus", "sol"})
        self.assertEqual(self.allowed(gw, "review"), {"sol", "opus"})  # opus: yedek denetçi (büyük işte)
        self.assertEqual(self.allowed(gw, "brain"), {"opus", "sol"})  # sol: yedek beyin
        self.assertEqual(self.allowed(gw, "worker"), {"opus", "sol", "sonnet", "luna"})
        self.assertEqual(self.allowed(gw, "worker", escalation=True), {"opus", "sol", "sonnet", "luna"})
        self.assertEqual(self.allowed(gw, "test"), {"opus", "sol", "sonnet", "luna"})
        gw = self.gateway(_ortak.make_config(jev={"yedek_denetci": {"olcekler": []}}))  # yedek denetçi kapalı
        self.assertEqual(self.allowed(gw, "review"), {"sol"})

    def test_messages(self):
        gw = self.gateway()
        cases = [
            (("zeta", "plan"), {}, r"^Bilinmeyen ajan: zeta$"),
            (("sol", "decompose"), {}, r"^Bilinmeyen aşama: decompose$"),
            (("luna", "netlestir"), {}, r"^Bilinmeyen aşama: netlestir$"),
            (("luna", "plan"), {}, r"^luna bu aşamada çağrılamaz \(plan; gereken rol: planlayici\)\.$"),
            (("sonnet", "fix"), {}, r"^sonnet bu aşamada çağrılamaz \(fix; gereken rol: planlayici\)\.$"),
            (("sonnet", "review"), {}, r"^sonnet bu aşamada çağrılamaz \(review; gereken rol: denetci\)\.$"),
            (("luna", "brain"), {}, r"^luna Jev'in beyni olamaz\.$"),
        ]
        for args, kw, pattern in cases:
            with self.subTest(args=args):
                with self.assertRaisesRegex(ForbiddenCall, pattern):
                    gw.check_allowed(*args, **kw)

    def test_roles_are_read_at_call_time(self):
        """Ayar elle bozulsa da kapı rolü her çağrıda yeniden okur."""
        cfg = _ortak.make_config()
        cfg.raw["ajanlar"]["opus"]["roller"] = ["beyin"]
        cfg.raw["ajanlar"]["luna"]["roller"] = ["isci", "denetci"]
        gw = self.gateway(cfg)
        self.assertEqual(self.allowed(gw, "plan"), {"sol"})  # ret yedeği rolden değil ayardan
        self.assertEqual(self.allowed(gw, "review"), {"sol", "luna", "opus"})  # yedek denetçi rolden değil ayardan
        with self.assertRaisesRegex(ForbiddenCall, r"^opus işçi olarak çağrılamaz\.$"):
            gw.check_allowed("opus", "worker", escalation=True)

    def test_refused_call_has_no_side_effects(self):
        gw = self.gateway()
        with mock.patch.object(Gateway, "adapter") as adapter:
            for agent, phase, esc in (("luna", "plan", True), ("sonnet", "review", False), ("luna", "brain", False),
                                      ("sol", "decompose", False)):
                with self.subTest(agent=agent, phase=phase):
                    with self.assertRaises(ForbiddenCall):
                        gw.call(agent, phase, "Bu hatayı düzelt", readonly=False, escalation=esc, task_id="T02")
        adapter.assert_not_called()
        gw.bus.emit.assert_not_called()
        self.assertEqual(gw.state, {})
        self.assertFalse(gw.rdir.exists())

    def quota_call(self, gw: Gateway, reset, shared: bool) -> list[dict]:
        res = AgentResult(ok=False, error_kind="quota", error_text="session limit", reset_at=reset, quota_shared=shared)
        with mock.patch.object(Gateway, "_run", return_value=res):
            gw.call("sonnet", "worker", "iş", task_id="T01")
        return [c.args[1] for c in gw.bus.emit.call_args_list if c.args[0] == "quota"]

    def cooled(self, gw: Gateway) -> set[str]:
        return {a for a in gw.cfg.agents if gw.quota.until(a)}

    def test_shared_quota_cools_provider_mates(self):
        """Claude'un ortak penceresi Sonnet'te dolunca Opus da çağrılmadan soğur; Codex ajanları (Sol, Luna) etkilenmez."""
        reset = now() + dt.timedelta(hours=2)
        gw = self.gateway()
        events = self.quota_call(gw, reset, shared=True)
        self.assertEqual(self.cooled(gw), {"sonnet", "opus"})
        self.assertAlmostEqual((gw.quota.until("opus") - reset).total_seconds(), 0, delta=1)
        self.assertEqual([(e["agent"], e.get("shared_from")) for e in events], [("sonnet", None), ("opus", "sonnet")])
        self.assertIn("Ortak kota doldu", events[1]["text"])
        gw = self.gateway()
        self.quota_call(gw, reset, shared=False)  # modele özgü pencere: yalnızca çağrılan ajan
        self.assertEqual(self.cooled(gw), {"sonnet"})

    def test_shared_quota_keeps_longer_cooldowns(self):
        gw = self.gateway()
        later = now() + dt.timedelta(days=2)
        gw.quota.mark("opus", later, "haftalık Opus sınırı")
        events = self.quota_call(gw, now() + dt.timedelta(hours=2), shared=True)
        self.assertAlmostEqual((gw.quota.until("opus") - later).total_seconds(), 0, delta=1)
        self.assertEqual([e["agent"] for e in events], ["sonnet"])

    def test_past_reset_still_cools_for_a_minute(self):
        """Geçmişte kalmış bir sıfırlanma saati ajanı hemen müsait gösterip çağrıyı döngüye sokmamalı."""
        gw = self.gateway()
        self.quota_call(gw, now() - dt.timedelta(minutes=5), shared=False)
        self.assertGreater((gw.quota.until("sonnet") - now()).total_seconds(), 50)


class DecisionTargets(unittest.TestCase):
    """Jev'in kararı ve yönlendirme işi yalnızca işçiye ya da eskalasyon ajanına verir."""

    @classmethod
    def setUpClass(cls):
        cls.runner = _ortak.planned_run()

    @classmethod
    def tearDownClass(cls):
        cls.runner.bus.close()

    def failing_task(self) -> dict:
        t = next(t for t in self.runner.state["tasks"] if t["id"] == "T02")
        t.update(status="needs_decision", attempt_count=1, failed_agents=["sol"], simple_rule_used=False, forced=None,
                 attempts=[{"n": 1, "agent": "sol", "counted": True, "outcome": "verify_failed"}])
        return t

    def decide(self, agent: str) -> dict:
        t = self.failing_task()
        brain.apply_decision(self.runner, t, {"decision": "reassign", "agent": agent, "rationale": "x"},
                             who="opus", call=None, outcome="verify_failed", problem="x", exhausted=False)
        return t

    def test_decision_targets(self):
        self.assertIsNone(self.decide("zeta")["forced"])  # bilinmeyen ajan
        self.assertEqual(self.decide("opus")["forced"]["agent"], "opus")  # eskalasyon: Jev'in kararıyla
        self.assertEqual(self.decide("sonnet")["forced"]["agent"], "sonnet")
        roles = self.runner.cfg.raw["ajanlar"]["luna"]
        with mock.patch.dict(roles, {"roller": ["denetci"]}):
            self.assertIsNone(self.decide("luna")["forced"])  # işçi ya da eskalasyon rolü yok

    def test_routing_needs_worker_role(self):
        cfg = _ortak.make_config()
        t = {"id": "T09", "complexity": "M", "suggested_agent": "opus"}
        self.assertNotIn("opus", routing.candidates(t, cfg))  # plan önerse de Opus kodu yalnızca takılınca yazar
        c = routing.choose_agent(t, {"tasks": []}, cfg, available=lambda a: True, until=lambda a: None)
        self.assertEqual((c.agent, c.reason), ("sol", "M listesinde ilk müsait ajan"))
        t["forced"] = {"agent": "opus", "effort": "high"}
        c = routing.choose_agent(t, {"tasks": []}, cfg, available=lambda a: True, until=lambda a: None)
        self.assertEqual((c.agent, c.reason), ("opus", "Jev'in kararı"))
        cfg.raw["ajanlar"]["opus"]["roller"] = ["planlayici", "beyin"]  # eskalasyon rolü yoksa zorlanamaz
        c = routing.choose_agent(t, {"tasks": []}, cfg, available=lambda a: True, until=lambda a: None)
        self.assertEqual(c.agent, "sol")


@_ortak.slow
class DryRun(unittest.TestCase):
    def test_every_call_matches_its_role(self):
        seen: list[tuple[str, str, bool]] = []
        real = Gateway.check_allowed

        def spy(gw, agent, phase, *, escalation=False):
            seen.append((agent, phase, escalation))
            return real(gw, agent, phase, escalation=escalation)

        with mock.patch.object(Gateway, "check_allowed", autospec=True, side_effect=spy):
            run = _ortak.make_run(speed=1000.0)
            try:
                self.assertEqual(run.drive(), "reported")
                self.assertTrue(start_fix(run, "Silmeden önce onay sor"))
                self.assertEqual(run.drive(), "reported")
            finally:
                run.bus.close()

        cfg = run.cfg
        self.assertLessEqual({p for _, p, _ in seen}, set(ROLE_PHASES))  # onarımlar özgün aşamayla denetlenir
        self.assertEqual({a for a, p, _ in seen if p in ("plan", "fix")}, {cfg.planner})
        self.assertEqual({a for a, p, _ in seen if p == "review"}, {cfg.reviewer})
        self.assertEqual([p for a, p, _ in seen if p in ("plan", "fix", "review")].count("review"), 2)  # iki tur
        self.assertIn((cfg.planner, "fix", False), seen)
        self.assertLessEqual({a for a, p, _ in seen if p == "brain"}, {cfg.brain, cfg.fallback_brain[0]})
        for a, p, esc in seen:
            if p == "worker":
                self.assertTrue(cfg.has_role(a, "isci") or (esc and cfg.has_role(a, "eskalasyon")), (a, esc))
        recs = read_jsonl(run.rdir / "decisions.jsonl")
        self.assertIn("jev", {r["brain"] for r in recs})  # sorun çıktı ve Jev modeli karar verdi
        self.assertLessEqual({r["brain"] for r in recs}, {"jev", "kural", cfg.brain, cfg.fallback_brain[0]})
        names = {p.name.split("-")[1] for p in (run.rdir / "calls").glob("*.prompt.md")}
        self.assertLessEqual(names, {"plan", "fix", "review", "brain", "worker", "repair"})
        self.assertLessEqual({t.get("agent") for t in run.state["tasks"]} - {None},
                             set(cfg.workers()) | set(cfg.get("yonlendirme", "eskalasyon", default=[])))


class ConfigRoles(unittest.TestCase):
    def test_default_config_roles(self):
        cfg = _ortak.make_config()
        self.assertEqual((cfg.planner, cfg.reviewer, cfg.brain), ("opus", "sol", "opus"))
        self.assertEqual(cfg.workers(), ["opus", "sol", "sonnet", "luna"])
        self.assertTrue(cfg.has_role("opus", "isci"))
        self.assertTrue(cfg.has_role("opus", "eskalasyon"))


if __name__ == "__main__":
    unittest.main()
