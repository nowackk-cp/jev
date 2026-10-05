"""Tek çağrı kapısı: her model çağrısı buradan geçer.

- Rol ve aşama denetimi: her aşama yalnızca o rolü taşıyan ajana açıktır (plan ve düzeltme → planlayıcı,
  son kontrol → denetçi ya da yedek denetçi, karar → beyin ya da yedek beyin; beyin salt okumadır). İşçi çağrısı
  yalnızca `isci` rolündeki ajanlara (varsayılanda Sol, Sonnet, Luna) yapılır; `eskalasyon` rolündeki ajan (Opus)
  yalnızca Jev'in kararıyla, görev takılınca işçi olur.
- Şema: prompta "ÇIKTI ŞEMASI:" eklenir; çıktı doğrulanır, geçersizse en fazla `onarim_denemesi` kez onarım istenir.
- Geçici hata: aynı ajanla bir kez daha denenir. Kota: ajan soğumaya alınır, olay yayınlanır.
- Kullanım (token) sayıları usage.json'a yazılır ve arayüze gönderilir.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from pathlib import Path
from typing import Callable

from .adapters.base import AgentActivity, AgentAdapter, AgentResult, CallSpec, empty_usage
from .quota import QuotaBook, log_unknown_error
from .sema import extract_json, load_schema, schema_text, validate
from .util import atomic_write_json, iso, now, read_json, shorten

PHASE_ROLE = {"plan": "planlayici", "fix": "planlayici", "review": "denetci", "brain": "beyin"}
PHASE_STATE = {"plan": "planning", "fix": "planning", "review": "reviewing", "brain": "thinking",
               "worker": "thinking", "repair": "thinking", "test": "thinking"}
PHASE_TEXT = {"plan": "Planı ve görev kartlarını yazıyor", "review": "Son kontrolü yapıyor",
              "fix": "Düzeltme kartlarını yazıyor", "brain": "Jev'e karar veriyor", "worker": "Göreve başlıyor",
              "repair": "Çıktısını şemaya uyduruyor", "test": "Deneme mesajı"}
TIMEOUT_KEY = {"plan": "plan", "fix": "plan", "brain": "beyin", "review": "denetim", "test": "test", "repair": "beyin"}


class ForbiddenCall(RuntimeError):
    """Kurallara aykırı çağrı (ör. planlayıcı rolü olmayan ajana plan yazdırmak)."""


class Gateway:
    def __init__(self, cfg, *, rdir: Path, project: Path, state: dict, bus, quota: QuotaBook,
                 save: Callable[[], None] | None = None, dry: bool = False, scenario: dict | None = None,
                 speed: float = 1.0, cancel: threading.Event | None = None):
        self.cfg = cfg
        self.rdir = Path(rdir)
        self.project = Path(project)
        self.state = state
        self.bus = bus
        self.quota = quota
        self.save = save or (lambda: None)
        self.dry = dry
        self.scenario = scenario
        self.speed = speed
        self.cancel = cancel or threading.Event()
        self._adapters: dict[str, AgentAdapter] = {}
        self._lock = threading.Lock()
        self._adapter_lock = threading.Lock()  # paralel görevler aynı sağlayıcının adaptörünü aynı anda isteyebilir
        self._in_flight: dict[str, int] = {}  # ajan başına süren çağrı (es_zamanli > 1 ise birden çok)

    # --- adaptörler ----------------------------------------------------------------------
    def adapter(self, agent: str) -> AgentAdapter:
        key = "mock" if self.dry else self.cfg.provider(agent)
        with self._adapter_lock:
            if key not in self._adapters:
                if key == "mock":
                    from .adapters.mock import MockAdapter, load_scenario
                    sc = self.scenario
                    if sc is None:
                        path = self.cfg.get("kuru", "senaryo", default="") or None
                        sc = load_scenario(path)
                    self._adapters[key] = MockAdapter(self.cfg, sc, self.speed)
                elif key == "codex":
                    from .adapters.codex import CodexAdapter
                    self._adapters[key] = CodexAdapter(self.cfg)
                else:
                    from .adapters.claude import ClaudeAdapter
                    self._adapters[key] = ClaudeAdapter(self.cfg)
            return self._adapters[key]

    # --- kurallar --------------------------------------------------------------------------
    def check_allowed(self, agent: str, phase: str, *, escalation: bool = False) -> None:
        cfg = self.cfg
        if agent not in cfg.agents:
            raise ForbiddenCall(f"Bilinmeyen ajan: {agent}")
        if phase == "test":
            return
        if phase == "brain":  # beyin soğumadaysa yedek beyin
            fb_agent, _ = cfg.fallback_brain
            if not (cfg.has_role(agent, "beyin") or agent == fb_agent):
                raise ForbiddenCall(f"{agent} Jev'in beyni olamaz.")
            return
        if phase == "worker":
            if cfg.has_role(agent, "isci"):
                return
            if escalation and cfg.has_role(agent, "eskalasyon"):
                return
            raise ForbiddenCall(f"{agent} işçi olarak çağrılamaz" + ("." if escalation else " (eskalasyon yalnızca Jev'in kararıyla)."))
        if phase == "review" and agent == cfg.backup_reviewer:  # yedek denetçi: kodun çoğunu denetçi yazdıysa
            return
        if phase in ("plan", "fix", "review") and agent == cfg.swap_partner:  # ret yedeği: reddedilen tek çağrıyı alır
            return
        role = PHASE_ROLE.get(phase)
        if role is None:
            raise ForbiddenCall(f"Bilinmeyen aşama: {phase}")
        if not cfg.has_role(agent, role):
            raise ForbiddenCall(f"{agent} bu aşamada çağrılamaz ({phase}; gereken rol: {role}).")

    # --- çağrı -------------------------------------------------------------------------------
    def _next_name(self, phase: str, task_id: str | None, agent: str) -> str:
        with self._lock:
            self.state["call_seq"] = int(self.state.get("call_seq", 0)) + 1
            seq = self.state["call_seq"]
        self.save()
        return f"{seq:03d}-{phase}-{task_id or '-'}-{agent}"

    def call(self, agent: str, phase: str, prompt: str, *, schema_name: str | None = None, readonly: bool = True,
             effort: str | None = None, timeout_key: str | None = None, task_id: str | None = None,
             attempt: int = 0, extra: dict | None = None, escalation: bool = False,
             end_state: str | None = "idle", state_text: str | None = None, workdir: Path | None = None,
             images: list | None = None, tools: str | None = None, web: bool = False,
             _repair_phase: str | None = None) -> AgentResult:
        """`workdir`: ajanın çalıştığı klasör (paralel görevde görevin kendi git çalışma ağacı); verilmezse proje.
        `images`: ajanın bakacağı resimler · `tools`: "" ise araçsız çağrı · `web`: internette arama izni."""
        self.check_allowed(agent, _repair_phase or phase, escalation=escalation)
        cfg = self.cfg
        schema = load_schema(schema_name) if schema_name else None
        full_prompt = prompt.rstrip() + ("\n\nÇIKTI ŞEMASI:\n" + schema_text(schema) + "\n" if schema else "\n")
        effort = effort or cfg.effort(agent, "varsayilan")
        tkey = timeout_key or TIMEOUT_KEY.get(phase, "isci_M")
        call_name = self._next_name(phase, task_id, agent)
        cwd = Path(workdir) if workdir else self.project
        spec = CallSpec(
            agent=agent, model=cfg.agents[agent]["model"], provider=cfg.provider(agent), prompt=full_prompt,
            cwd=cwd, mode="readonly" if readonly else "full", effort=effort,
            timeout_s=cfg.timeout_s(tkey), call_dir=self.rdir / "calls", call_name=call_name, phase=phase,
            schema=schema, schema_name=schema_name, task_id=task_id, attempt=attempt,
            round=int(self.state.get("round", 1)), run_dir=self.rdir, project=cwd,
            guard=bool(cfg.get("guvenlik", "koruma", default=False)),
            full_access=bool(cfg.get("guvenlik", "tam_yetki", default=True)), cancel=self.cancel,
            extra=dict(extra or {}), images=[str(p) for p in images or []], tools=tools, web=bool(web))
        base_state = PHASE_STATE.get(phase, "thinking")
        self.bus.emit("agent.state", {"agent": agent, "state": base_state, "task_id": task_id, "phase": phase,
                                      "text": state_text or PHASE_TEXT.get(phase, ""), "until": None})

        def on_activity(act: AgentActivity) -> None:
            self.bus.emit("agent.activity", {"agent": agent, "kind": act.kind, "text": shorten(act.text, 300),
                                             "path": act.path, "task_id": task_id, "phase": phase})

        with self._lock:
            self._in_flight[agent] = self._in_flight.get(agent, 0) + 1
        try:
            res = self._call_body(spec, on_activity, schema, escalation, _repair_phase)
        finally:
            with self._lock:
                self._in_flight[agent] -= 1
                busy_elsewhere = self._in_flight[agent] > 0
        if res.error_kind != "quota" and end_state and not busy_elsewhere:  # ajanın başka görevi sürüyorsa boşta görünmez
            self.bus.emit("agent.state", {"agent": agent, "state": end_state, "task_id": None, "phase": phase,
                                          "text": "", "until": None})
        return res

    def _call_body(self, spec: CallSpec, on_activity, schema, escalation: bool, _repair_phase) -> AgentResult:
        cfg, agent, phase, task_id = self.cfg, spec.agent, spec.phase, spec.task_id
        res = self._run(spec, on_activity)
        if res.error_kind == "transient":
            wait = float(cfg.get("sinirlar", "gecici_hata_bekleme_sn", default=45) or 45)
            if self.dry:
                wait = min(wait, 3.0) / max(0.01, self.speed)
            self.bus.emit("log", {"level": "warn", "text": f"{agent}: geçici hata; {int(wait)} sn sonra bir kez daha denenecek."})
            self._sleep(wait)
            call_name2 = self._next_name(phase, task_id, agent)
            spec.call_name = call_name2
            res = self._run(spec, on_activity)
        alt_model = cfg.refusal_model(agent)
        if res.error_kind == "refusal" and alt_model and alt_model != spec.model:
            # güvenlik reddi: aynı çağrı bir kez yedek modelle (Opus 5.5 → 4.8); o da reddederse çağıran işi devreder
            self.bus.emit("log", {"level": "warn", "text": f"{cfg.label(agent)} isteği güvenlik gerekçesiyle yanıtlamadı; "
                                                          f"aynı iş {alt_model} ile deneniyor."})
            spec.model = alt_model
            spec.call_name = self._next_name(phase, task_id, agent)
            res = self._run(spec, on_activity)

        if res.ok and schema is not None and not _repair_phase:
            res = self._ensure_schema(spec, res, schema, on_activity, escalation)

        if res.error_kind == "quota":
            until = res.reset_at or (now() + _dt.timedelta(minutes=float(cfg.get("kota", "varsayilan_soguma_dk", default=60))))
            until = max(until, now() + _dt.timedelta(minutes=1))  # geçmişte kalmış bir saat çağrıyı döngüye sokmasın
            res.reset_at = until
            self.quota.mark(agent, until, res.error_text)
            self.bus.emit("quota", {"agent": agent, "until": iso(until), "text": shorten(res.error_text, 200)})
            if res.quota_shared:  # hesabın ortak penceresi: aynı sağlayıcının diğer ajanları da çağrılmadan soğur
                why = f"Ortak kota doldu ({cfg.label(agent)} çağrısında)"
                for mate in cfg.agents:
                    cur = self.quota.until(mate)
                    if mate == agent or cfg.provider(mate) != spec.provider or (cur and cur >= until):
                        continue
                    self.quota.mark(mate, until, why)
                    self.bus.emit("quota", {"agent": mate, "until": iso(until), "text": why, "shared_from": agent})
            self.bus.emit("agent.state", {"agent": agent, "state": "sleeping", "task_id": None, "phase": phase,
                                          "text": "Kota doldu", "until": iso(until)})
        elif res.error_kind == "crash":
            log_unknown_error(agent, res.error_text)
        if res.error_kind == "auth":
            self.bus.emit("log", {"level": "error", "text": f"{agent}: oturum kapalı ya da giriş gerekli."})
        elif res.error_kind == "refusal":
            self.bus.emit("log", {"level": "warn", "text": f"{agent}: istek güvenlik gerekçesiyle yanıtlanmadı."})
        return res

    def _run(self, spec: CallSpec, on_activity) -> AgentResult:
        t0 = time.monotonic()
        try:
            res = self.adapter(spec.agent).run(spec, on_activity)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # adaptör hatası koşuyu düşürmesin
            res = AgentResult(ok=False, error_kind="crash", error_text=f"Adaptör hatası: {e!r}",
                              duration_s=time.monotonic() - t0)
        self._record_usage(spec, res)
        try:
            atomic_write_json(spec.path("result.json"), res.summary())
        except OSError:
            pass
        return res

    def _ensure_schema(self, spec: CallSpec, res: AgentResult, schema: dict, on_activity,
                       escalation: bool) -> AgentResult:
        value = res.structured if res.structured is not None else extract_json(res.text)
        errs = validate(value, schema) if value is not None else ["Yanıtta JSON bulunamadı."]
        tries = int(self.cfg.get("sinirlar", "onarim_denemesi", default=2) or 2)
        last_text = res.text if res.structured is None else json.dumps(res.structured, ensure_ascii=False)
        original = spec.call_name
        n = 0
        while errs and n < tries:
            n += 1
            self.bus.emit("log", {"level": "warn",
                                  "text": f"{spec.agent}: çıktı şemaya uymuyor; onarım isteniyor ({n}/{tries})."})
            prompt = ("Önceki yanıtın istenen JSON şemasına uymuyor. Aşağıdaki hataları düzelt ve YALNIZCA şemaya uyan "
                      "JSON nesnesini ver; açıklama yazma, içeriği değiştirme.\n\nHATALAR:\n"
                      + "\n".join(f"- {e}" for e in errs[:30])
                      + "\n\nÖNCEKİ YANIT:\n" + (last_text or "(boş)")[-30000:])
            rep = self.call(spec.agent, "repair", prompt, schema_name=spec.schema_name, readonly=True,
                            effort=self.cfg.effort(spec.agent, "test") if not self.dry else "low",
                            task_id=spec.task_id, attempt=spec.attempt, extra={"repair_of": original},
                            escalation=escalation, end_state=None, workdir=spec.cwd, tools="",
                            _repair_phase=spec.phase)
            if not rep.ok:
                if rep.error_kind in ("quota", "auth", "cancelled"):
                    return rep
                continue
            value = rep.structured if rep.structured is not None else extract_json(rep.text)
            errs = validate(value, schema) if value is not None else ["Yanıtta JSON bulunamadı."]
            last_text = rep.text
        if errs:
            res.ok = False
            res.error_kind = "schema"
            res.error_text = "Çıktı şemaya uymuyor: " + "; ".join(errs[:10])
            res.structured = None
            return res
        res.structured = value
        return res

    # --- kullanım ------------------------------------------------------------------------------
    def _record_usage(self, spec: CallSpec, res: AgentResult) -> None:
        p = self.rdir / "usage.json"
        with self._lock:
            data = read_json(p, {}) or {}
            u = data.setdefault(spec.agent, {**empty_usage(), "calls": 0, "duration_s": 0.0})
            u["calls"] = int(u.get("calls", 0)) + 1
            u["duration_s"] = round(float(u.get("duration_s", 0.0)) + float(res.duration_s or 0.0), 1)
            for k, v in (res.usage or {}).items():
                if isinstance(v, (int, float)):
                    u[k] = (u.get(k) or 0) + v
            atomic_write_json(p, data)
        self.bus.emit("agent.usage", {"agent": spec.agent, "calls": u["calls"],
                                      "input_tokens": int(u.get("input_tokens", 0) or 0),
                                      "output_tokens": int(u.get("output_tokens", 0) or 0),
                                      "cost_usd": round(float(u.get("cost_usd", 0) or 0), 4)})

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.cancel.is_set():
            time.sleep(min(0.1, end - time.monotonic()) if end > time.monotonic() else 0)
