"""[5] DÜZELTME TURU: yalnızca kullanıcı "düzelt" deyince (kural 7). Planlayıcı rapordaki eksikleri düzeltme
kartlarına çevirir; Jev kartları planlamadaki gibi kodla doğrular ve aynı dosyaya dokunanları sıraya koyar. Mini ve
küçük işte planlayıcı çağrılmaz: kullanıcının notu tek bir düzeltme görevi olur.

Görev kimlikleri D<düzeltme no>-NN (ör. D1-01). Sonra yine uygulama → son kontrol → rapor; akış yine durur.
"""
from __future__ import annotations

import re

from .. import context, gitops
from ..dag import link_shared_files, validate_tasks
from ..state import scale_level
from ..util import ek
from . import sizing
from .common import Refused, RunPaused, call_fixed, init_task, refusal_partner, refusal_pause, task_event
from .review import latest_report


NOTHING_TO_FIX = "Raporda eksik yok; düzeltme turu için ne istediğini not olarak yaz."


def nothing_to_fix(rep: dict | None, note: str | None) -> bool:
    """Rapor başarılı, eksik yok ve kullanıcı not da yazmadıysa açılacak bir düzeltme turu yoktur."""
    return bool(rep) and rep.get("verdict") == "basarili" and not rep.get("gaps") and not (note or "").strip()


def start_fix(run, note: str | None) -> bool:
    """Düzeltme turunu hazırlar. Yapılacak bir şey yoksa False döner (tur açılmaz)."""
    st = run.state
    if st.get("phase") != "reported":
        run.term.warn("Düzeltme yalnızca rapordan sonra başlatılabilir.")
        return False
    rep, _ = latest_report(run.rdir, st)
    note = (note or "").strip()
    if rep is None:
        run.term.warn("Rapor bulunamadı.")
        return False
    if nothing_to_fix(rep, note):
        run.term.ok("Düzeltilecek eksik yok; değişiklik istiyorsan ne istediğini notla birlikte ver.")
        run.log("info", "Düzeltilecek eksik yok; tur açılmadı.")
        return False
    st["fix_note"] = note
    st["round"] = int(st.get("round", 1)) + 1
    run.save()
    run.log("info", f"Düzeltme turu başlıyor (tur {st['round']}" + (f"; not: {note}" if note else "") + ").")
    run.set_phase("fixing")
    return True


def run_fix(run) -> None:
    cfg, st = run.cfg, run.state
    rnd = int(st.get("round", 2))
    fix_no = rnd - 1
    run.term.header(f"Düzeltme · tur {rnd}")  # ofisteki "Tur 2: Düzeltme › Uygulama …" ile aynı numara
    rep, _ = latest_report(run.rdir, st)
    rep = rep or {}
    level = scale_level(st)
    if cfg.skips_step(level, "plan"):
        single_fix(run, rep, rnd, fix_no)
        return
    planner = cfg.planner
    name = context.display(planner)
    max_tasks = cfg.max_tasks(level)
    effort = cfg.effort(planner, "varsayilan", cfg.step_cap(level, "plan"))
    git_log = gitops.log_oneline(run.project, st.get("base_commit"), 60)
    tries = int(cfg.get("sinirlar", "onarim_denemesi", default=2) or 2)
    rx = re.compile(rf"^D{fix_no}-\d{{2,3}}$")
    errors: list[str] = []
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": "fixing",
                             "text": f"{ek(name, 'in')} düzeltme kartlarını bekliyor", "until": None})
    for n in range(tries + 1):
        while True:  # ret yedeği: planlayıcı (yedek modeliyle de) reddederse bu turu ret_takasi ajanı yazar
            prompt = context.fix_prompt(planner, run.plan, st, rep, st.get("fix_note") or "", git_log, fix_no)
            if errors:
                prompt += ("\n\nÖNCEKİ YANITINDAKİ HATALAR (düzelt ve listeyi yeniden ver):\n"
                           + "\n".join(f"- {e}" for e in errors[:30]) + "\n")
            run.term.info(f"{name} rapordaki eksikleri düzeltme kartlarına çeviriyor…" if n == 0 else
                          f"Düzeltme kartlarında sorun var; {ek(name, 'den')} düzeltmesi istendi ({n}/{tries}).")
            try:
                res = call_fixed(run, planner, "fix", prompt, schema_name="tasks", effort=effort,
                                 timeout_key="plan", extra={"round": rnd, "gaps": rep.get("gaps") or [],
                                                            "note": st.get("fix_note") or ""})
                break
            except Refused as e:
                alt = refusal_partner(run, e.agent, "düzeltme", e.text)
                if alt is None:
                    raise refusal_pause(run, e.agent, "düzeltme", e.text)
                planner, name = alt, context.display(alt)
                effort = cfg.effort(planner, "varsayilan", cfg.step_cap(level, "plan"))
        tasks = res.structured.get("tasks", [])
        errors = validate_tasks(tasks, run.plan, existing=st["tasks"], max_tasks=max_tasks,
                                require_coverage=False)
        errors += [f"{t.get('id')}: kimlik D{fix_no}-NN biçiminde olmalı (ör. D{fix_no}-01)."
                   for t in tasks if not rx.match(str(t.get("id") or ""))]
        if not errors:
            break
        run.log("warn", "Düzeltme kartları geçersiz: " + " | ".join(errors[:6]))
    else:
        raise RunPaused(f"{ek(name, 'in')} düzeltme kartları doğrulanamadı: " + " | ".join(errors[:5]))
    links = link_shared_files(tasks, existing=st["tasks"])
    if links:
        run.log("info", "Jev aynı dosyalara dokunan kartları sıraya koydu: " + "; ".join(links[:8]))
    new = [init_task(t, origin="fix", round_=rnd) for t in tasks]
    st["tasks"].extend(new)
    run.save()
    for t in new:
        run.emit("task.update", task_event(t))
    run.term.ok(f"{len(new)} düzeltme görevi: " + ", ".join(t["id"] for t in new))
    run.set_phase("executing")


def single_fix(run, rep: dict, rnd: int, fix_no: int) -> None:
    """Mini ve küçük iş: kullanıcının notu (yoksa rapordaki eksikler) tek düzeltme görevi D<n>-01 olur. Planlayıcı ve
    plan yok; işi zorluğun havuzundaki bir işçi yapar, doğrulama komutlarını Jev yeniden çalıştırır."""
    st = run.state
    note = (st.get("fix_note") or "").strip()
    gaps = [f"{g.get('description')}" + (f" (öneri: {g['suggested_fix']})" if g.get("suggested_fix") else "")
            for g in rep.get("gaps") or [] if g.get("description")]
    rows = [note] if note else []
    if gaps:
        rows.append("Son kontrol raporundaki eksikler:\n" + "\n".join(f"- {x}" for x in gaps[:8]))
    description = "\n\n".join(rows) or "Son kontrol raporundaki eksikleri gider."
    task = sizing.single_task(run.cfg, st, f"D{fix_no}-01", origin="fix", round_=rnd, description=description)
    st["tasks"].append(task)
    run.save()
    run.emit("task.update", task_event(task))
    run.term.ok(f"Düzeltme görevi: {task['id']} (tek görev; plan ve planlayıcı yok)")
    run.set_phase("executing")
