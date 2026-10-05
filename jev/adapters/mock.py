"""Sahte adaptör: `--kuru` modu ve testler için. Gerçek model çağırmaz.

Senaryo (jev/mock/senaryo_varsayilan.json) her aşamanın çıktısını ve işçilerin adımlarını anlatır.
İşçiler şablonlardan gerçek dosyalar yazar ve test adımlarını gerçekten çalıştırır; böylece git,
doğrulama ve geri alma yolları da sınanır. Adımlar arasındaki gecikmeler `hiz` ile kısalır.

İşçi adımları: think, read, write (template | content), test (komutu gerçekten çalıştırır),
command (yalnızca etkinlik; çalıştırılmaz), guard (koruma yolunu gerçekten dener).
Sonuçlar (outcome): done, failed, blocked, quota, timeout, crash, transient, broken_json.

Hangi senaryo girdisinin kullanıldığı koşu klasöründeki `kuru-sayac.json` dosyasında tutulur;
`jev devam` sonrasında da aynı girdiler tekrar kullanılmaz. Yarıda kesilen çağrı girdiyi tüketmez.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import guard
from ..quota import parse_reset
from ..util import atomic_write_json, now, read_json
from .base import AgentActivity, AgentAdapter, AgentResult, ActivityFn, CallSpec, base_paths, child_env, empty_usage

MOCK_DIR = Path(__file__).resolve().parent.parent / "mock"
DEFAULT_SCENARIO = MOCK_DIR / "senaryo_varsayilan.json"
TEMPLATE_DIR = MOCK_DIR / "sablonlar"


REFUSAL_TEXT = ("API Error: Claude Code is unable to respond to this request, which appears to violate our Usage "
                "Policy (https://www.anthropic.com/legal/aup).")


def load_scenario(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_SCENARIO
    return json.loads(p.read_text(encoding="utf-8-sig"))


CARD_KEYS = ("id", "title", "module", "type", "description", "reads", "outputs", "contracts", "acceptance", "verify",
             "depends_on", "covers_criteria", "complexity", "suggested_agent", "notes")


def card(t: dict) -> dict:
    """Görev kartı şemadaki alanlarla: eski senaryolardaki eksik reads ve contracts boş liste olur."""
    return {k: t.get(k, [] if k in ("reads", "contracts") else None) for k in CARD_KEYS}


def scenario_plan(scenario: dict) -> dict:
    """Planlayıcının tek çıktısı: plan + görev kartları. Kartları ayrı `tasks` girdisinde tutan eski senaryolar da
    okunur; eksik needs ve contracts boş liste olur."""
    plan = dict(scenario["plan"])
    tasks = plan.get("tasks")
    if tasks is None:
        tasks = (scenario.get("tasks") or {}).get("tasks") or []
    plan["tasks"] = [card(t) for t in tasks]
    plan.setdefault("needs", [])
    plan.setdefault("contracts", [])
    return plan


class _Cancelled(Exception):
    pass


class MockAdapter(AgentAdapter):
    provider = "mock"

    def __init__(self, cfg, scenario: dict | None = None, speed: float = 1.0, template_dir: Path | None = None,
                 run_tests: bool = True):
        super().__init__(cfg)
        self.scenario = scenario if scenario is not None else load_scenario()
        self.speed = max(0.01, float(speed or 1.0))
        self.template_dir = Path(template_dir) if template_dir else TEMPLATE_DIR
        self.run_tests = run_tests
        self._lock = threading.RLock()  # paralel görevler aynı sayaç dosyasını okur ve yazar
        self._mem_counts: dict = {}
        self._pending: dict[str, dict] = {}  # bozuk JSON → onarım çağrısında dönecek doğru sonuç

    # --- sayaçlar ---------------------------------------------------------------------------
    def _counter_path(self, spec: CallSpec) -> Path | None:
        return Path(spec.run_dir) / "kuru-sayac.json" if spec.run_dir else None

    def _counts(self, spec: CallSpec) -> dict:
        with self._lock:
            p = self._counter_path(spec)
            if p is None:
                return self._mem_counts
            d = read_json(p, {}) or {}
            return d if isinstance(d, dict) else {}

    def _bump(self, spec: CallSpec, group: str, key: str) -> None:
        with self._lock:
            d = self._counts(spec)
            g = d.setdefault(group, {})
            g[key] = int(g.get(key, 0)) + 1
            p = self._counter_path(spec)
            if p is None:
                self._mem_counts = d
            else:
                atomic_write_json(p, d)

    # --- yardımcılar ------------------------------------------------------------------------
    def _sleep(self, seconds: float, spec: CallSpec) -> None:
        end = time.monotonic() + max(0.0, float(seconds)) / self.speed
        while True:
            if spec.cancel is not None and spec.cancel.is_set():
                raise _Cancelled()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    def _usage(self, spec: CallSpec, out_chars: int) -> dict:
        u = empty_usage()
        u["input_tokens"] = max(1, len(spec.prompt) // 4)
        u["output_tokens"] = max(1, out_chars // 4)
        return u

    def _finish(self, spec: CallSpec, t0: float, *, ok: bool = True, structured=None, text: str = "",
                error_kind: str = "none", error_text: str = "", reset_at=None, guard_hits=None) -> AgentResult:
        paths = base_paths(spec)
        spec.call_dir.mkdir(parents=True, exist_ok=True)
        spec.path("prompt.md").write_text(spec.prompt, encoding="utf-8")
        body = text if text else (json.dumps(structured, ensure_ascii=False) if structured is not None else "")
        rec = {"type": "kuru", "phase": spec.phase, "agent": spec.agent, "task": spec.task_id, "ok": ok,
               "error_kind": error_kind, "error_text": error_text, "text": body[:4000]}
        spec.path("stdout.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        spec.path("stderr.txt").write_text(error_text + ("\n" if error_text else ""), encoding="utf-8")
        if structured is not None:
            spec.path("out.json").write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8")
        return AgentResult(ok=ok, structured=structured, text=body, exit_code=0 if ok else 1,
                           duration_s=time.monotonic() - t0, usage=self._usage(spec, len(body)),
                           quota_hit=error_kind == "quota", reset_at=reset_at, error_kind=error_kind,
                           error_text=error_text, paths=paths, guard=guard_hits or [])

    # --- giriş ------------------------------------------------------------------------------
    def run(self, spec: CallSpec, on_activity: ActivityFn) -> AgentResult:
        t0 = time.monotonic()
        handler = {
            "plan": self._plan, "brain": self._brain, "review": self._review, "fix": self._fix,
            "repair": self._repair, "worker": self._worker, "test": self._test,
        }.get(spec.phase, self._test)
        refusers = (self.scenario.get("ret") or {}).get(spec.phase) or []  # "opus": her modeli · "opus@model": yalnızca o model
        if spec.agent in refusers or f"{spec.agent}@{spec.model}" in refusers:  # senaryodaki güvenlik reddi
            on_activity(AgentActivity("message", "İsteği yanıtlamıyor (güvenlik)"))
            return self._finish(spec, t0, ok=False, error_kind="refusal", error_text=REFUSAL_TEXT)
        try:
            return handler(spec, on_activity, t0)
        except _Cancelled:
            return self._finish(spec, t0, ok=False, error_kind="cancelled", error_text="Kullanıcı durdurdu.")

    def _say(self, on_activity: ActivityFn, spec: CallSpec, kind: str, text: str, delay: float = 1.0,
             path: str | None = None) -> None:
        on_activity(AgentActivity(kind, text, path))
        self._sleep(delay, spec)

    # --- aşamalar ---------------------------------------------------------------------------
    def _test(self, spec: CallSpec, on_activity: ActivityFn, t0: float) -> AgentResult:
        self._say(on_activity, spec, "thinking", "Düşünüyor…", 0.3)
        return self._finish(spec, t0, text="OK")

    def _plan(self, spec, on_activity, t0):
        plan = scenario_plan(self.scenario)
        self._say(on_activity, spec, "thinking", "İsteği okuyor", 1.2)
        self._say(on_activity, spec, "thinking", "İhtiyaçları ve ana planı çiziyor", 1.5)
        for m in plan.get("modules", [])[:4]:
            self._say(on_activity, spec, "editing", f"Modül {m['id']}: {m['name']}", 0.6)
        self._say(on_activity, spec, "editing", "Başarı ölçütlerini ve sözleşmeleri yazıyor", 1.0)
        for t in plan["tasks"][:8]:
            self._say(on_activity, spec, "editing", f"Kart: {t['id']} {t['title']}", 0.4)
        return self._finish(spec, t0, structured=plan)

    def _brain(self, spec, on_activity, t0):
        key = spec.task_id or "-"
        used = int(self._counts(spec).get("brain", {}).get(key, 0))
        options = (self.scenario.get("brain") or {}).get(key) or []
        self._say(on_activity, spec, "reading", "Kayıtları okuyor", 1.0)
        self._say(on_activity, spec, "thinking", "Kök nedeni arıyor", 1.4)
        played = (spec.extra or {}).get("kuru_karar")  # Jev modeli (kuru) senaryodaki kararı zaten oynadı
        if isinstance(played, dict):
            decision = played
        elif used < len(options):
            decision = options[used]
        else:
            decision = self._fallback_decision(spec)
        self._say(on_activity, spec, "message",
                  f"Karar: {decision['decision']}" + (f" → {decision['agent']}" if decision.get("agent") else ""), 0.6)
        res = self._finish(spec, t0, structured=decision)
        if not isinstance(played, dict):
            self._bump(spec, "brain", key)
        return res

    def _fallback_decision(self, spec: CallSpec) -> dict:
        hints = spec.extra or {}
        tried = set(hints.get("tried") or [])
        avail = [a for a in hints.get("available") or [] if a not in tried]
        base = {"effort": None, "rollback": True, "revised_task": None, "new_tasks": [], "deviation": "none"}
        if int(hints.get("remaining_attempts", 1)) <= 0:
            return {**base, "decision": "skip", "agent": None, "deviation": "major",
                    "guidance": "", "rationale": "Deneme hakkı bitti (kuru koşu varsayılan kararı).",
                    "report_note": f"{spec.task_id}: deneme hakkı bittiği için atlandı."}
        if avail:
            return {**base, "decision": "reassign", "agent": avail[0],
                    "guidance": "Önceki denemenin çıktısını oku; hatayı gideren en küçük değişikliği yap.",
                    "rationale": "Kuru koşu varsayılan kararı: görevi başka bir ajana ver.",
                    "report_note": f"{spec.task_id}: görev {avail[0]} ajanına verildi."}
        return {**base, "decision": "retry", "agent": None,
                "guidance": "Doğrulama çıktısını oku ve hatayı düzelt.",
                "rationale": "Kuru koşu varsayılan kararı: aynı ajanla yeniden dene.",
                "report_note": f"{spec.task_id}: yeniden denendi."}

    def _review(self, spec, on_activity, t0):
        reviews = self.scenario.get("review") or {}
        rv = reviews.get(str(spec.round))
        if rv is None and reviews:
            rv = reviews[max(reviews, key=int)]
        if rv is None:
            rv = self._fallback_review(spec)
        self._say(on_activity, spec, "reading", "plan.md okuyor", 1.0)
        self._say(on_activity, spec, "reading", "Test sonuçlarını okuyor", 1.2)
        for c in rv.get("criteria", [])[:6]:
            self._say(on_activity, spec, "thinking", f"{c['id']} inceleniyor", 0.5)
        self._say(on_activity, spec, "editing", "Raporu yazıyor", 1.2)
        return self._finish(spec, t0, structured=rv)

    def _fallback_review(self, spec: CallSpec) -> dict:
        plan = self.scenario.get("plan") or {}
        crit = [{"id": c["id"], "status": "met", "evidence": "kuru koşu", "gap": None}
                for c in plan.get("success_criteria", [])]
        if not crit:  # planı olmayan senaryo (mini, küçük): Jev'in sentetik planındaki tek ölçüt
            crit = [{"id": "SC1", "status": "met", "evidence": "kuru koşu", "gap": None}]
        md = "# Son Kontrol Raporu\n\n## Genel sonuç\n**Başarılı.** (kuru koşu)\n"
        return {"verdict": "basarili", "summary": "Kuru koşu.", "report_markdown": md, "criteria": crit,
                "gaps": [], "risks": [], "next_steps": []}

    def _fix(self, spec, on_activity, t0):
        fixes = self.scenario.get("fix") or {}
        out = fixes.get(str(spec.round))
        if out is None:
            out = self._fallback_fix(spec)
        out = {"tasks": [card(t) for t in out.get("tasks", [])]}  # eski senaryolardaki coverage_notes düşer
        self._say(on_activity, spec, "reading", "Son raporu okuyor", 1.0)
        self._say(on_activity, spec, "thinking", "Eksikleri düzeltme kartlarına çeviriyor", 1.2)
        for t in out["tasks"]:
            self._say(on_activity, spec, "editing", f"Kart: {t['id']} {t['title']}", 0.5)
        return self._finish(spec, t0, structured=out)

    def _fallback_fix(self, spec: CallSpec) -> dict:
        gaps = (spec.extra or {}).get("gaps") or []
        n = max(1, spec.round - 1)
        tasks = []
        for i, g in enumerate(gaps or [{"id": "G1", "criteria": [], "description": "Kullanıcının notu"}], start=1):
            tid = f"D{n}-{i:02d}"
            path = f"notlar/{tid}.md"
            tasks.append(card({"id": tid, "title": f"{g.get('id', 'G')} düzeltmesi", "module": "M1", "type": "docs",
                               "description": str(g.get("description", "")), "outputs": [path],
                               "acceptance": [f"{path} var"],
                               "verify": [f"if (-not (Test-Path '{path}')) {{ exit 1 }}"], "depends_on": [],
                               "covers_criteria": list(g.get("criteria") or []), "complexity": "S",
                               "suggested_agent": None, "notes": str(g.get("id", ""))}))
        return {"tasks": tasks}

    def _repair(self, spec, on_activity, t0):
        self._say(on_activity, spec, "thinking", "JSON çıktısını düzeltiyor", 0.8)
        key = (spec.extra or {}).get("repair_of") or ""
        fixed = self._pending.pop(key, None)
        if fixed is None and spec.schema_name == "worker_result":
            fixed = self._generic_worker_result(spec)
        if fixed is None:
            return self._finish(spec, t0, ok=False, error_kind="schema", error_text="Onarılacak çıktı bulunamadı.")
        return self._finish(spec, t0, structured=fixed)

    # --- işçi -------------------------------------------------------------------------------
    def _pick_worker(self, spec: CallSpec) -> tuple[int | None, dict | None]:
        entries = [(i, e) for i, e in enumerate(self.scenario.get("workers") or []) if e.get("task") == spec.task_id]
        used = self._counts(spec).get("workers", {})
        for i, e in entries:
            if used.get(str(i)):
                continue
            if e.get("agent") and e["agent"] != spec.agent:
                continue
            return i, e
        done = [e for _, e in entries if e.get("outcome", "done") == "done"]
        if done:
            return None, done[-1]  # tükenmiş: son başarılı girdi yeniden kullanılır
        return None, None

    def _generic_worker_result(self, spec: CallSpec, changed=None, verification=None) -> dict:
        return {"status": "done", "summary": f"{spec.task_id} tamamlandı (kuru koşu).",
                "changed_files": changed or [], "verification": verification or [], "blocker": None,
                "notes": "", "follow_ups": []}

    def _generic_steps(self, spec: CallSpec) -> list[dict]:
        outs = [o for o in (spec.extra or {}).get("outputs") or [] if isinstance(o, str)]
        path = outs[0] if outs else f"notlar/{spec.task_id}.md"
        return [{"do": "think", "text": "Görevi okuyor", "delay": 1.0},
                {"do": "write", "path": path, "content": f"# {spec.task_id}\n\nKuru koşuda yazıldı.\n", "delay": 1.0}]

    def _worker(self, spec, on_activity, t0):
        idx, entry = self._pick_worker(spec)
        if entry is None:
            entry = {"steps": self._generic_steps(spec), "outcome": "done"}
        changed: list[str] = []
        verification: list[dict] = []
        guard_hits: list[dict] = []
        for st in entry.get("steps") or []:
            self._step(spec, on_activity, st, changed, verification, guard_hits)
        outcome = entry.get("outcome", "done")
        err = str(entry.get("error") or "")
        summary = str(entry.get("summary") or f"{spec.task_id} için çalışıldı.")
        result = {"status": "done", "summary": summary, "changed_files": changed, "verification": verification,
                  "blocker": None, "notes": str(entry.get("notes") or ""), "follow_ups": []}
        if outcome == "quota":
            on_activity(AgentActivity("message", err or "Kota doldu"))
            reset = parse_reset(err) or (now() + _dt.timedelta(minutes=60))
            res = self._finish(spec, t0, ok=False, error_kind="quota", error_text=err or "usage limit reached",
                               reset_at=reset)
        elif outcome in ("timeout", "crash", "transient"):
            msg = err or {"timeout": f"Zaman aşımı ({spec.timeout_s} sn).", "crash": "Süreç beklenmedik biçimde çıktı.",
                          "transient": "503 Service Unavailable: overloaded"}[outcome]
            res = self._finish(spec, t0, ok=False, error_kind=outcome, error_text=msg)
        elif outcome == "broken_json":
            on_activity(AgentActivity("message", "Sonucu yazıyor…"))
            self._pending[spec.call_name] = result
            broken = json.dumps(result, ensure_ascii=False)
            broken = broken[: max(20, len(broken) * 2 // 3)]  # yarıda kesilmiş JSON
            res = self._finish(spec, t0, ok=True, structured=None, text=broken, guard_hits=guard_hits)
        else:
            if outcome in ("failed", "blocked"):
                result["status"] = outcome
                result["blocker"] = err or ("Bilgi eksik." if outcome == "blocked" else None)
            on_activity(AgentActivity("message", summary))
            res = self._finish(spec, t0, structured=result, guard_hits=guard_hits)
        if idx is not None:
            self._bump(spec, "workers", str(idx))
        return res

    def _step(self, spec: CallSpec, on_activity: ActivityFn, st: dict, changed: list, verification: list,
              guard_hits: list) -> None:
        do = st.get("do")
        delay = float(st.get("delay", 1.0))
        if do == "think":
            self._say(on_activity, spec, "thinking", str(st.get("text") or "Düşünüyor…"), delay)
        elif do == "read":
            p = str(st.get("path") or "")
            self._say(on_activity, spec, "reading", f"{p} okuyor", delay, p or None)
        elif do == "write":
            rel = str(st["path"])
            on_activity(AgentActivity("editing", f"{rel} düzenliyor", rel))
            if "template" in st:
                content = (self.template_dir / st["template"]).read_text(encoding="utf-8")
            else:
                content = str(st.get("content") or "")
            target = Path(spec.cwd) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            if rel not in changed:
                changed.append(rel)
            self._sleep(delay, spec)
        elif do == "command":
            self._say(on_activity, spec, "command", str(st.get("command") or ""), delay)
        elif do == "test":
            cmd = str(st.get("command") or "")
            on_activity(AgentActivity("testing", cmd))
            if self.run_tests:
                from ..verify import run_command
                r = run_command(cmd, Path(spec.cwd), 300, cancel=spec.cancel)
                if r.cancelled:
                    raise _Cancelled()
                verification.append({"command": cmd, "passed": r.passed, "output_tail": r.tail(15)})
            else:
                verification.append({"command": cmd, "passed": True, "output_tail": ""})
            self._sleep(delay, spec)
        elif do == "guard":
            self._guard_step(spec, on_activity, st, guard_hits)
            self._sleep(delay, spec)

    def _guard_step(self, spec: CallSpec, on_activity: ActivityFn, st: dict, guard_hits: list) -> None:
        """Koruma yolunu gerçekten dener. Claude ajanında gerçek kanca betiği (kanca.py) çalıştırılır;
        Codex ajanında canlı izlemenin kullandığı kural denetimi çağrılır (kuru koşuda komut çalıştırılmaz)."""
        tool = str(st.get("tool") or "Write")
        inp = dict(st.get("input") or {})
        for k in ("file_path", "path"):
            if isinstance(inp.get(k), str) and inp[k].startswith("~"):
                inp[k] = str(Path.home()) + inp[k][1:].replace("/", "\\")
        target = inp.get("file_path") or inp.get("command") or ""
        on_activity(AgentActivity("editing" if tool in ("Write", "Edit") else "command",
                                  f"{target} yazmayı deniyor" if tool in ("Write", "Edit") else str(target)))
        project = Path(spec.project or spec.cwd)
        run_dir = Path(spec.run_dir or spec.call_dir.parent)
        if not spec.guard:
            return
        cfg_provider = self.cfg.provider(spec.agent) if spec.agent in self.cfg.agents else "claude"
        if cfg_provider == "claude":
            kanca = Path(__file__).resolve().parent.parent / "kanca.py"
            payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp, "cwd": str(spec.cwd)}
            env = child_env({"JEV_AJAN": spec.agent, "JEV_GOREV": spec.task_id or ""})
            r = subprocess.run([sys.executable, str(kanca), "--saglayici", "claude", "--kosu", str(run_dir),
                                "--proje", str(project)], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                               capture_output=True, env=env, timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 2:
                msg = r.stderr.decode("utf-8", "replace").strip().replace("JEV KORUMASI:", "Koruma:")
                on_activity(AgentActivity("guarded", msg.split(" Bu işlem")[0]))
        else:
            cmd = f"Set-Content -Path '{target}' -Value 'yedek'" if tool in ("Write", "Edit") else str(target)
            v = guard.check_command(cmd, project)
            if v is not None:
                rec = guard.record(run_dir, v, provider="codex", tool="command_execution", detail=cmd,
                                   agent=spec.agent, task=spec.task_id, action="engellendi")
                guard_hits.append(rec)
                on_activity(AgentActivity("guarded", f"Koruma: {v.reason}"))
