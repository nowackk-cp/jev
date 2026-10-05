"""[1] PLAN: planlayıcı tek çağrıda ihtiyaçları (araştırma, veri, site…), kısa planı, sözleşmeleri ve görev kartlarını
yazar. Jev kartları kodla doğrular (kimlik, döngü, kapsam, sözleşme); yalnızca hata listesini planlayıcıya geri
gönderir. Aynı dosyaya dokunan kartları Jev kendisi sıraya koyar; plan.md, plan.json, tasks.json ve AGENTS.md/CLAUDE.md
dosyalarını yazar."""
from __future__ import annotations

import json

from .. import context, gitops
from ..config import SCALE_TR, SCALES
from ..dag import link_shared_files, validate_tasks
from ..state import scale_level
from ..util import atomic_write_json, atomic_write_text, shorten
from . import sizing
from .common import Refused, RunPaused, call_fixed, init_task, refusal_partner, refusal_pause, task_event


def run_plan(run) -> None:
    st, cfg = run.state, run.cfg
    level = scale_level(st)
    is_new = bool(st.get("new_project"))
    # yeni projede klasör taranmaz: planlayıcı araçsız düşünür, dosya ağacı da gönderilmez
    tree = "" if is_new else gitops.file_tree(run.project, 200)
    max_tasks = cfg.max_tasks(level)
    tries = int(cfg.get("sinirlar", "onarim_denemesi", default=2) or 2)
    errors: list[str] = []
    prev = ""
    planner = cfg.planner
    for n in range(tries + 1):
        while True:  # ret yedeği: planlayıcı (yedek modeliyle de) reddederse bu planı ret_takasi ajanı yazar
            name = context.display(planner)
            effort = cfg.effort(planner, "plan", cfg.step_cap(level, "plan"))
            prompt = context.plan_prompt(planner, st["request"], run.project, is_new, tree, st, max_tasks=max_tasks,
                                         prev_errors=errors or None, prev_output=prev)
            run.term.info(f"{name} isteği inceliyor; planı ve görev kartlarını yazıyor ({cfg.label(planner)}, efor "
                          f"{effort})…" if n == 0 else f"Görev kartlarında sorun var; {name} düzeltiyor ({n}/{tries}).")
            try:
                res = call_fixed(run, planner, "plan", prompt, schema_name="plan", effort=effort, timeout_key="plan",
                                 tools="" if is_new else None)
                break
            except Refused as e:
                alt = refusal_partner(run, e.agent, "plan", e.text)
                if alt is None:
                    raise refusal_pause(run, e.agent, "plan", e.text)
                planner = alt
        plan = dict(res.structured)
        tasks = plan.pop("tasks", None) or []
        errors = validate_tasks(tasks, plan, max_tasks=max_tasks)
        if not errors:
            break
        run.log("warn", "Görev kartları geçersiz: " + " | ".join(errors[:6]))
        prev = json.dumps(res.structured, ensure_ascii=False)
    else:
        raise RunPaused("Görev kartları doğrulanamadı: " + " | ".join(errors[:5]))
    links = link_shared_files(tasks)
    if links:
        run.log("info", "Jev aynı dosyalara dokunan kartları sıraya koydu: " + "; ".join(links[:8]))
    atomic_write_json(run.rdir / "plan.json", plan)
    atomic_write_json(run.rdir / "tasks.json", {"tasks": tasks})
    atomic_write_text(run.rdir / "plan.md", context.plan_document(plan, tasks))
    run.plan = plan
    st["project_name"] = plan.get("project_name")
    st["tasks"] = [init_task(t, origin="plan", round_=1) for t in tasks]
    run.save()
    # olaylar ofisin canlı akışına da gider: klasör yolu eklenmez (terminal ve state.json'da var)
    run.emit("run.info", {"project_name": plan.get("project_name"), "summary": plan.get("summary"),
                          "request": st["request"], "branch": st.get("branch"),
                          "dry_run": st.get("dry_run"), "criteria": [c.get("id") for c in plan.get("success_criteria", [])],
                          "modules": [{"id": m.get("id"), "name": m.get("name")} for m in plan.get("modules", [])]})
    for t in st["tasks"]:
        run.emit("task.update", task_event(t))
    changed = context.write_shared_context(run.project, plan)
    if changed:
        gitops.commit_paths(run.project, changed, "jev: ortak bağlam (AGENTS.md, CLAUDE.md)")
    run.term.ok(f"Plan hazır: {plan.get('project_name')} · {len(plan.get('modules', []))} modül · "
                f"{len(plan.get('success_criteria', []))} başarı ölçütü · {len(tasks)} görev kartı")
    run.term.dim("  " + shorten(plan.get("summary", ""), 200))
    needs = plan.get("needs") or []
    if needs:
        run.term.dim("  İhtiyaçlar: " + "; ".join(
            f"{context.NEED_TR.get(x.get('kind'), x.get('kind'))}: {shorten(x.get('what', ''), 80)}"
            + (f" → {x['task']}" if x.get("task") else "") for x in needs))
    raise_scale(run, plan.get("scale"))
    run.set_phase("awaiting_approval" if st.get("plan_approval") else "executing")


def raise_scale(run, proposed: str | None) -> None:
    """Planlayıcı isteği inceledikten sonra işi daha büyük bulduysa kalan aşamalar (eforlar, denetim, onay) yeni
    ölçekle çalışır. Yalnızca yukarı: plan zaten yazıldı. Kullanıcının sözü (--olcek, açık ifade) değişmez."""
    st = run.state
    sc = st.get("scale")
    if not sc or proposed not in SCALES or sc.get("source") in ("kullanici", "ifade"):
        return
    old = sc.get("level") or "buyuk"
    if SCALES.index(proposed) <= SCALES.index(old):
        return
    st["scale"] = {**sc, "level": proposed, "source": "plan", "from": old, "p": None}
    if run.cfg.profile(proposed).get("plan_onayi") and not st.get("dry_run"):
        st["plan_approval"] = True
    run.save()
    run.log("info", f"{context.display(run.cfg.planner)} işi daha büyük buldu: ölçek {SCALE_TR[old]} → "
                    f"{SCALE_TR[proposed]}.")
    sizing.announce(run)
    sizing.record_history(run, {"tur": "yukseltme", "olcek": proposed, "zorluk": sc.get("difficulty"),
                                "onceki": old, "sorun": "plan"})


def await_approval(run) -> None:
    """--onay: plan kullanıcıya gösterilir; terminalden ya da arayüzden onay beklenir.

    Onay verilmeden çıkılırsa koşu duraklatılır (plan kaybolmaz); yalnızca `reddet` koşuyu iptal eder."""
    from ..runner import _stdin_is_tty
    from .common import RunAborted
    run.term.header("Plan onay bekliyor")
    run.term.line(f"Plan: {run.rdir / 'plan.md'}")
    if run.url:
        run.term.line(f"Arayüz: {run.url}")
    if run.server is None and not _stdin_is_tty():
        raise RunPaused("Plan onayı için etkileşimli terminal ya da ofis arayüzü gerekiyor.", kind="kullanici")
    run.term.line("Onaylamak için `onayla`, reddetmek için `reddet` yaz (ya da arayüzdeki düğmeleri kullan).")
    run.start_input()
    while True:
        cmd, _ = run.next_command()
        if cmd == "onayla":
            run.log("info", "Plan onaylandı.")
            run.set_phase("executing")
            return
        if cmd == "reddet":
            raise RunAborted("Kullanıcı planı reddetti.")
        if cmd == "cikis":
            raise RunPaused("Plan onay bekliyor; onay verilmeden çıkıldı.", kind="kullanici")
        if cmd == "durum":
            run.print_status()
        elif cmd:
            run.term.warn("Şu an yalnızca `onayla` ya da `reddet` kabul ediliyor.")
