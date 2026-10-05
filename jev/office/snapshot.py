"""Ofis arayüzünün veri katmanı: /api/durum, /api/rapor, /api/plan, /api/ajan/<ad>/log.

Sunucu iş parçacığından çağrılır; koşu durumunu kilit altında kopyalar, orkestratörü beklemez.
Yanıtlarda dosya yolu, ham ajan çıktısı (stdout) ya da ofis erişim anahtarı (ui_url) bulunmaz.
"""
from __future__ import annotations

import json

from ..context import DECISION_TR, DISPLAY, OUTCOME_TR, display
from ..config import DIFFICULTY_TR, SCALE_TR
from ..phases import sizing
from ..phases.brain import brain_limit
from ..phases.common import task_event
from ..phases.review import CRIT_TR, VERDICT_TR, report_name
from ..routing import progress
from ..state import PHASE_TR, STATUS_TR, TYPE_TR
from ..util import iso, local_hhmm, now, parse_iso, read_json, read_jsonl, shorten
from .md import to_html

FEED_TYPES = {"log", "jev.dispatch", "jev.consult", "jev.decision", "guard.blocked", "quota", "verify",
              "report.ready", "run.phase", "task.update", "run.scale"}
FEED_TASK_STATUSES = {"done", "failed", "blocked", "skipped", "split", "needs_decision", "waiting_quota"}
IDLE_PHASES = {"paused", "reported", "aborted", "awaiting_approval"}
RESTING = {"idle", "sleeping", "done", "failed", "blocked"}
ACTIVE_TASK = {"running", "verifying"}
# decompose ve netlestir: eski koşuların çağrı kayıtları (plan ve kartlar artık tek çağrıda)
CALL_PHASE_TR = {"plan": "Plan ve kartlar", "worker": "Görev", "brain": "Jev'e karar", "repair": "Şema onarımı",
                 "review": "Son kontrol", "fix": "Düzeltme kartları", "test": "Deneme",
                 "decompose": "Görevlere bölme", "netlestir": "Netleştirme"}
ROLE_TR = {"planlayici": "Planlayıcı", "denetci": "Denetçi", "beyin": "Beyin", "eskalasyon": "Eskalasyon",
           "isci": "İşçi", "orkestrator": "Orkestratör"}
ERROR_TR = {**OUTCOME_TR, "none": "", "unknown": "bilinmeyen hata"}
DEVIATION_TR = {"none": "", "minor": "plandan küçük sapma", "major": "plandan büyük sapma"}
APPLIED_TR = {"retry": "yeniden denenecek", "reassign": "başka ajana verildi", "revise": "görev düzeltildi",
              "split": "bölündü", "skipped": "atlandı", "pause": "koşu duraklatıldı", "abort": "koşu iptal edildi",
              "failed": "başarısız sayıldı", "gecersiz": "geçersiz karar; kural uygulandı"}
FEED_MAX = 200
DECISIONS_MAX = 50


# --- yardımcılar --------------------------------------------------------------------------

def _state_copy(run) -> dict:
    """Ana iş parçacığı durumu kilitsiz de değiştirebilir; kopyalama çakışırsa birkaç kez dener."""
    for _ in range(3):
        try:
            with run._state_lock:
                return json.loads(json.dumps(run.state, ensure_ascii=False, default=str))
        except (RuntimeError, TypeError, ValueError):
            continue
    return read_json(run.rdir / "state.json", {}) or {}


def _plan(run) -> dict:
    plan = getattr(run, "plan", None)
    if isinstance(plan, dict) and plan:
        return plan
    return read_json(run.rdir / "plan.json", {}) or {}


def agent_names(run) -> list[str]:
    return list(run.cfg.agents) + ["jev"]


def _feed_worthy(ev: dict) -> bool:
    t = ev.get("type")
    if t not in FEED_TYPES:
        return False
    d = ev.get("data") or {}
    if t == "task.update":
        return d.get("status") in FEED_TASK_STATUSES
    if t == "log":
        return d.get("level") != "debug"
    return True


def _usage(run) -> dict:
    raw = read_json(run.rdir / "usage.json", {}) or {}
    out = {}
    for a, u in raw.items():
        if not isinstance(u, dict):
            continue
        out[a] = {"calls": int(u.get("calls", 0) or 0),
                  "input_tokens": int(u.get("input_tokens", 0) or 0),
                  "output_tokens": int(u.get("output_tokens", 0) or 0),
                  "cost_usd": round(float(u.get("cost_usd", 0) or 0), 4),
                  "duration_s": round(float(u.get("duration_s", 0) or 0), 1)}
    return out


def _quotas(run) -> dict:
    out = {}
    try:
        recs = run.quota.all()
    except (OSError, ValueError, AttributeError):
        recs = {}
    for a, rec in recs.items():
        t = parse_iso(rec.get("until"))
        out[a] = {"until": rec.get("until"), "hhmm": local_hhmm(t), "reason": shorten(rec.get("reason") or "", 200)}
    return out


def _attempt_view(a: dict) -> dict:
    rows = a.get("verify") or []
    outcome = a.get("outcome")
    return {"n": a.get("n"), "agent": a.get("agent"), "outcome": outcome, "outcome_tr": OUTCOME_TR.get(outcome or "", ""),
            "summary": shorten(a.get("summary") or a.get("problem") or "", 200),
            "duration_s": a.get("duration_s"), "reason": shorten(a.get("reason") or "", 200),
            "effort": a.get("effort"), "counted": a.get("counted"), "started": a.get("started"),
            "verify_pass": sum(1 for r in rows if r.get("passed")), "verify_total": len(rows)}


def _deviation(d: dict) -> str:
    v = d.get("deviation")
    if v is True:
        return "minor"
    return v if v in ("minor", "major") else "none"


def _decision_view(d: dict) -> dict:
    dec = d.get("decision")
    dev = _deviation(d)
    return {"decision": dec, "decision_tr": DECISION_TR.get(dec or "", dec or ""), "agent": d.get("agent"),
            "brain": d.get("brain"), "rationale": shorten(d.get("rationale") or "", 300),
            "deviation": dev, "deviation_tr": DEVIATION_TR[dev], "applied": d.get("applied"),
            "applied_tr": APPLIED_TR.get(d.get("applied") or "", d.get("applied") or "")}


def _task_view(t: dict) -> dict:
    v = task_event(t)
    v.update({
        "status_tr": STATUS_TR.get(t.get("status") or "", t.get("status") or ""),
        "summary": shorten(t.get("summary") or "", 300),
        "origin": t.get("origin", "plan"),
        "files": [str(f) for f in (t.get("files") or [])[:20]],
        "description": shorten(t.get("description") or "", 400),
        "acceptance": [shorten(str(x), 200) for x in (t.get("acceptance") or [])[:8]],
        "attempts": [_attempt_view(a) for a in (t.get("attempts") or [])[-6:]],
        "decisions": [_decision_view(d) for d in (t.get("decisions") or [])[-6:]],
        "waiting_until": t.get("waiting_until"),
        "commit": (t.get("commit") or "")[:8] or None,
        "suggested_agent": t.get("suggested_agent"),
    })
    return v


def _reports(st: dict) -> list[dict]:
    out = []
    for r in st.get("reports") or []:
        verdict = r.get("verdict")
        out.append({"round": int(r.get("round", 1) or 1), "verdict": verdict,
                    "verdict_tr": VERDICT_TR.get(verdict or "", verdict or ""),
                    "summary": shorten(r.get("summary") or "", 400), "ts": r.get("ts"),
                    "by": r.get("by") or "astra"})  # "by" alanı olmayan eski raporları Astra yazmıştı
    return out


def _scale_view(cfg, st: dict) -> dict:
    """Ölçek rozeti ve ölçeğe göre akış şeridi (ölçeği olmayan eski koşu: tam hat)."""
    sc = st.get("scale") or {}
    view = None
    if sc.get("level"):
        lvl, diff = sc["level"], sc.get("difficulty") or "orta"
        view = {"level": lvl, "level_tr": SCALE_TR.get(lvl, lvl), "difficulty": diff,
                "difficulty_tr": DIFFICULTY_TR.get(diff, diff), "source": sc.get("source"),
                "source_tr": sizing.source_label(sc), "p": sc.get("p"),
                "from": sc.get("from"), "jev_review": cfg.jev_review(lvl),
                "flow": sizing.flow_note(cfg, lvl, diff, bool(st.get("plan_approval")))}
    try:
        flow = sizing.flow_phases(cfg, st)
    except (KeyError, ValueError, TypeError):
        flow = None
    return {"scale": view, "flow": flow}


def _last_by_agent(hist: list[dict], type_: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ev in hist:
        if ev.get("type") == type_:
            d = ev.get("data") or {}
            a = d.get("agent")
            if a:
                out[a] = {**d, "seq": ev.get("seq"), "ts": ev.get("ts")}
    return out


def _normalize_state(s: dict | None, phase: str, quota: dict | None) -> dict:
    """Son durum olayını ekrana uygun hâle getirir. Koşu beklerken (rapor, duraklama, onay) kotası dolan
    uyur, herkes dinlenir: eski "T04 bitti!" ya da "raporu bekliyor" yazıları kalmaz. office.js aynısını yapar."""
    s = dict(s) if s else {"state": "idle", "text": "", "task_id": None, "until": None, "seq": 0, "ts": None}
    state = s.get("state") or "idle"
    resting_phase = phase in IDLE_PHASES
    if resting_phase and state != "sleeping":
        state = "idle"
    if state == "sleeping":
        u = parse_iso(s.get("until"))
        if u is None or u <= now():
            state = "idle"
    if quota and state == "idle":
        state = "sleeping"
        s["until"] = quota["until"]
        s["text"] = "Kota doldu"
    if state == "idle" and (state != s.get("state") or resting_phase):
        s["text"] = ""
        s["task_id"] = None
    s["state"] = state
    return s


# --- /api/durum ---------------------------------------------------------------------------

def build(run) -> dict:
    hist = run.bus.history()  # önce geçmiş: istemci akışa last_seq'ten bağlanır, arada olay kaçmaz
    last_seq = hist[-1]["seq"] if hist else 0
    st = _state_copy(run)
    cfg = run.cfg
    phase = st.get("phase") or "planning"
    tasks = st.get("tasks") or []

    run_block = {k: st.get(k) for k in ("run_id", "request", "project_name", "branch", "dry_run", "speed",
                                         "created", "updated", "resume_phase", "paused_reason", "pause_kind",
                                         "quota_until", "plan_approval", "fix_note")}
    run_block.update({"round": int(st.get("round", 1) or 1), "phase": phase,
                      "phase_tr": PHASE_TR.get(phase, phase),
                      "resume_phase_tr": PHASE_TR.get(st.get("resume_phase") or "", ""),
                      "brain_calls": int(st.get("brain_calls", 0) or 0),
                      # süre sayacı: kapanmış çalışma aralıkları + (çalışma aşamasındaysa) phase_since'ten beri
                      "phase_since": st.get("phase_since"), "work_s": float(st.get("work_s") or 0)})
    try:
        run_block["brain_limit"] = brain_limit(run)
    except (KeyError, ValueError, TypeError):
        run_block["brain_limit"] = None
    run_block.update(_scale_view(cfg, st))
    run_block["reviewer"] = st.get("reviewer") or cfg.reviewer  # son kontrolü yapan (büyük işte yedek denetçi olabilir)

    usage = _usage(run)
    quotas = _quotas(run)
    states = _last_by_agent(hist, "agent.state")
    acts = _last_by_agent(hist, "agent.activity")
    active = {t.get("agent"): t["id"] for t in tasks if t.get("status") in ACTIVE_TASK and t.get("agent")}

    agents = []
    for name, a in cfg.agents.items():
        roles = list(a.get("roller") or [])
        agents.append({
            "name": name, "display": display(name), "label": cfg.label(name), "model": a.get("model"),
            "provider": a.get("saglayici"), "roles": roles, "roles_tr": [ROLE_TR.get(r, r) for r in roles],
            "state": _normalize_state(states.get(name), phase, quotas.get(name)),
            "activity": acts.get(name), "usage": usage.get(name) or {"calls": 0, "input_tokens": 0,
                                                                      "output_tokens": 0, "cost_usd": 0.0,
                                                                      "duration_s": 0.0},
            "quota": quotas.get(name), "task": active.get(name),
        })
    fb_agent, fb_effort = cfg.fallback_brain
    jm = cfg.get("jev", "model", default={}) or {}
    agents.append({
        "name": "jev", "display": "Jev", "label": "Orkestratör",
        "model": (jm.get("model") or "jev-latest") if jm.get("acik", True) else None,
        "provider": "typesafe" if jm.get("acik", True) else "yerel", "model_usage": usage.get("jev"),
        "roles": ["orkestrator"], "roles_tr": [ROLE_TR["orkestrator"]],
        "brain": cfg.label(cfg.brain) if cfg.brain in cfg.agents else cfg.brain,
        "brain_display": display(cfg.brain) if cfg.brain in cfg.agents else cfg.brain,
        "fallback": f"{cfg.label(fb_agent) if fb_agent in cfg.agents else fb_agent} · {fb_effort}",
        "state": _normalize_state(states.get("jev"), phase, None), "activity": acts.get("jev"),
        "usage": {"calls": int(st.get("brain_calls", 0) or 0)}, "quota": None, "task": None,
    })

    plan = _plan(run)
    plan_block = None
    if plan:
        plan_block = {
            "project_name": plan.get("project_name"), "summary": shorten(plan.get("summary") or "", 600),
            "stack": plan.get("stack"),
            "modules": [{"id": m.get("id"), "name": m.get("name"), "responsibility": shorten(m.get("responsibility") or "", 200)}
                        for m in plan.get("modules") or [] if isinstance(m, dict)],
            "success_criteria": [{"id": c.get("id"), "statement": shorten(c.get("statement") or "", 300)}
                                 for c in plan.get("success_criteria") or [] if isinstance(c, dict)],
            "commands": {k: v for k, v in (plan.get("commands") or {}).items() if isinstance(v, str)},
        }

    reports = _reports(st)
    report_block = None
    if reports:
        latest = reports[-1]
        data = read_json(run.rdir / f"{report_name(latest['round'])}.json", {}) or {}
        report_block = {**latest,
                        "criteria": [{"id": c.get("id"), "status": c.get("status"),
                                      "status_tr": CRIT_TR.get(c.get("status") or "", c.get("status") or "")}
                                     for c in data.get("criteria") or [] if isinstance(c, dict)],
                        "gaps": [{"id": g.get("id"), "criteria": g.get("criteria") or [],
                                  "description": shorten(g.get("description") or "", 300)}
                                 for g in data.get("gaps") or [] if isinstance(g, dict)]}

    feed = [ev for ev in hist if _feed_worthy(ev)][-FEED_MAX:]
    decisions = [ev for ev in hist if ev.get("type") == "jev.decision"][-DECISIONS_MAX:]
    done, total = progress(st)
    pending = bool(getattr(run, "_pending", False))
    return {
        "ok": True,
        "last_seq": last_seq,
        "server_now": iso(),
        "run": run_block,
        "tasks": [_task_view(t) for t in tasks],
        "agents": agents,
        "plan": plan_block,
        "report": report_block,
        "reports": reports,
        "feed": feed,
        "decisions": decisions,
        "progress": {"done": done, "total": total, "pct": round(100 * done / total) if total else 0},
        "controls": {"duzelt": phase == "reported", "onayla": phase == "awaiting_approval",
                     "reddet": phase == "awaiting_approval", "devam": phase == "paused",
                     "pending": pending, "interactive": not getattr(run, "exit_after", False)},
        "labels": {"status": STATUS_TR, "phase": PHASE_TR, "verdict": VERDICT_TR, "criteria": CRIT_TR,
                   "outcome": OUTCOME_TR, "decision": DECISION_TR, "display": DISPLAY, "role": ROLE_TR,
                   "call_phase": CALL_PHASE_TR, "deviation": DEVIATION_TR, "applied": APPLIED_TR, "type": TYPE_TR},
    }


# --- /api/rapor ---------------------------------------------------------------------------

def report_view(run, tur: str | int | None = None) -> dict:
    st = _state_copy(run)
    reports = _reports(st)
    if not reports:
        return {"ok": False, "mesaj": "Henüz rapor yok.", "reports": []}
    chosen = reports[-1]
    if tur not in (None, ""):
        try:
            want = int(tur)
        except (TypeError, ValueError):
            want = None
        for r in reports:
            if r["round"] == want:
                chosen = r
                break
    name = report_name(chosen["round"])
    data = read_json(run.rdir / f"{name}.json", {}) or {}
    md_path = run.rdir / f"{name}.md"
    try:
        md = md_path.read_text(encoding="utf-8-sig") if md_path.exists() else ""
    except OSError:
        md = ""
    md = md or data.get("report_markdown") or ""
    return {
        "ok": True, "round": chosen["round"], "verdict": chosen["verdict"], "verdict_tr": chosen["verdict_tr"],
        "by": chosen["by"], "summary": data.get("summary") or chosen["summary"],
        "criteria": [{"id": c.get("id"), "status": c.get("status"),
                      "status_tr": CRIT_TR.get(c.get("status") or "", c.get("status") or ""),
                      "evidence": shorten(c.get("evidence") or "", 400), "gap": shorten(c.get("gap") or "", 400)}
                     for c in data.get("criteria") or [] if isinstance(c, dict)],
        "gaps": [{"id": g.get("id"), "criteria": g.get("criteria") or [],
                  "description": shorten(g.get("description") or "", 500),
                  "suggested_fix": shorten(g.get("suggested_fix") or "", 500)}
                 for g in data.get("gaps") or [] if isinstance(g, dict)],
        "risks": [shorten(str(x), 300) for x in data.get("risks") or []],
        "next_steps": [shorten(str(x), 300) for x in data.get("next_steps") or []],
        "html": to_html(md),
        "reports": reports,
        "fix_note": st.get("fix_note") or "",
    }


# --- /api/plan ----------------------------------------------------------------------------

def plan_view(run) -> dict:
    plan = _plan(run)
    if not plan:
        return {"ok": False, "mesaj": "Plan henüz hazır değil."}
    md_path = run.rdir / "plan.md"
    try:
        md = md_path.read_text(encoding="utf-8-sig") if md_path.exists() else ""
    except OSError:
        md = ""
    md = md or plan.get("plan_markdown") or ""

    def strs(key: str, n: int = 300) -> list[str]:
        return [shorten(str(x), n) for x in plan.get(key) or []]

    return {
        "ok": True, "html": to_html(md), "project_name": plan.get("project_name"),
        "summary": plan.get("summary") or "", "stack": plan.get("stack"),
        "modules": [{"id": m.get("id"), "name": m.get("name"), "responsibility": shorten(m.get("responsibility") or "", 300),
                     "depends_on": m.get("depends_on") or []}
                    for m in plan.get("modules") or [] if isinstance(m, dict)],
        "success_criteria": [{"id": c.get("id"), "statement": shorten(c.get("statement") or "", 300),
                              "verification": shorten(c.get("verification") or "", 300)}
                             for c in plan.get("success_criteria") or [] if isinstance(c, dict)],
        "assumptions": strs("assumptions"), "out_of_scope": strs("out_of_scope"), "risks": strs("risks"),
        "commands": {k: v for k, v in (plan.get("commands") or {}).items() if isinstance(v, str)},
    }


# --- /api/ajan/<ad>/log -------------------------------------------------------------------

def _line(ev: dict, text: str, kind: str | None = None) -> dict:
    d = ev.get("data") or {}
    return {"seq": ev.get("seq"), "ts": ev.get("ts"), "type": ev.get("type"),
            "kind": kind or d.get("kind") or d.get("state"), "text": shorten(text, 300), "task_id": d.get("task_id")}


def _log_lines(hist: list[dict], name: str) -> list[dict]:
    out = []
    for ev in hist:
        t, d = ev.get("type"), ev.get("data") or {}
        if t in ("agent.state", "agent.activity") and d.get("agent") == name:
            out.append(_line(ev, d.get("text") or ""))
        elif t == "quota" and d.get("agent") == name:
            out.append(_line(ev, d.get("text") or "Kota doldu", "quota"))
        elif t == "guard.blocked" and d.get("agent") == name:
            out.append(_line(ev, f"Koruma durdurdu: {d.get('category_tr') or d.get('category') or ''} — "
                                 f"{d.get('reason') or ''}", "guarded"))
        elif name == "jev" and t == "jev.dispatch":
            why = f" ({d.get('reason')})" if d.get("reason") else ""
            out.append(_line(ev, f"{d.get('task_id')} → {display(d.get('agent') or '')}{why}", "dispatch"))
        elif name == "jev" and t == "jev.consult":
            who = "Jev modeli düşünüyor" if d.get("brain") == "jev" else f"{display(d.get('brain') or '')} danışılıyor"
            out.append(_line(ev, f"{who}: {d.get('text') or d.get('problem') or ''}", "consulting"))
        elif name == "jev" and t == "jev.decision":
            dec = DECISION_TR.get(d.get("decision") or "", d.get("decision") or "")
            to = f" → {display(d.get('agent'))}" if d.get("agent") else ""
            out.append(_line(ev, f"Karar: {dec}{to}. {d.get('rationale') or ''}", "decision"))
    return out


def _calls(run, name: str) -> list[dict]:
    cdir = run.rdir / "calls"
    out = []
    try:
        files = sorted(cdir.glob("*.result.json"))
    except OSError:
        files = []
    for f in files:
        stem = f.name[: -len(".result.json")]
        parts = stem.split("-")
        if len(parts) < 4:
            continue
        agent, phase = parts[-1], parts[1]
        task = "-".join(parts[2:-1])
        if name == "jev":
            if phase != "brain":
                continue
        elif agent != name:
            continue
        r = read_json(f, {}) or {}
        u = r.get("usage") or {}
        kind = r.get("error_kind") or "none"
        out.append({"seq": parts[0], "phase": phase, "phase_tr": CALL_PHASE_TR.get(phase, phase),
                    "task": None if task in ("", "-") else task, "agent": agent, "ok": bool(r.get("ok")),
                    "duration_s": r.get("duration_s"), "error_kind": None if kind == "none" else kind,
                    "error_tr": ERROR_TR.get(kind, kind), "error_text": shorten(r.get("error_text") or "", 300),
                    "input_tokens": int(u.get("input_tokens", 0) or 0),
                    "output_tokens": int(u.get("output_tokens", 0) or 0),
                    "cost_usd": round(float(u.get("cost_usd", 0) or 0), 4), "quota_hit": bool(r.get("quota_hit"))})
    return out[-30:]


def agent_log(run, name: str, tail: int = 60) -> dict:
    hist = run.bus.history()
    st = _state_copy(run)
    tasks = st.get("tasks") or []
    attempts = []
    for t in tasks:
        for a in t.get("attempts") or []:
            if a.get("agent") == name:
                attempts.append({**_attempt_view(a), "task_id": t["id"], "title": shorten(t.get("title") or "", 80)})
    attempts.sort(key=lambda a: a.get("started") or "")
    current = next((t for t in tasks if t.get("status") in ACTIVE_TASK and t.get("agent") == name), None)
    out = {
        "ok": True, "name": name, "display": display(name),
        "lines": _log_lines(hist, name)[-tail:],
        "calls": _calls(run, name),
        "attempts": attempts[-20:],
        "current": _task_view(current) if current else None,
        "usage": _usage(run).get(name) if name != "jev" else {"calls": int(st.get("brain_calls", 0) or 0)},
        "quota": _quotas(run).get(name),
    }
    if name == "jev":
        recs = read_jsonl(run.rdir / "decisions.jsonl")[-30:]
        out["decisions"] = [{
            "ts": d.get("ts"), "round": d.get("round"), "task_id": d.get("task_id"), "attempt": d.get("attempt"),
            "problem": d.get("problem"), "problem_tr": OUTCOME_TR.get(d.get("problem") or "", d.get("problem") or ""),
            "problem_text": shorten(d.get("problem_text") or "", 300),
            "decision": d.get("decision"), "decision_tr": DECISION_TR.get(d.get("decision") or "", d.get("decision") or ""),
            "agent": d.get("agent"), "effort": d.get("effort"), "brain": d.get("brain"),
            "rationale": shorten(d.get("rationale") or "", 400), "guidance": shorten(d.get("guidance") or "", 300),
            "deviation": _deviation(d), "deviation_tr": DEVIATION_TR[_deviation(d)], "applied": d.get("applied"),
            "applied_tr": APPLIED_TR.get(d.get("applied") or "", d.get("applied") or ""),
            "notes": [shorten(str(n), 200) for n in (d.get("notes") or [])[:5]],
        } for d in recs]
    return out
