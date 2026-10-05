"""Jev'in beyni: sorunlu görev için karar (belirtim §5.7).

Beyin Opus'tur (salt okuma); Opus soğumadaysa yedek beyin Sol (xhigh, salt okuma). İkisi de yoksa basit kural:
görev bir kez başka bir ajana verilir, yine olmazsa koşu duraklatılır. Görevi yalnızca işçi ya da eskalasyon rolü
olan ajan alabilir; denetçi ve planlayıcı rolü tek başına yetmez.
"""
from __future__ import annotations

import copy

from .. import context, lanes, systemone
from ..config import cap_effort
from ..dag import link_shared_files, validate_tasks
from ..routing import candidates
from ..state import scale_level
from ..util import ek, iso, local_hhmm, shorten, suffix
from .common import RunAborted, RunPaused, init_task, task_event

EXHAUSTED_ALLOWED = {"skip", "pause", "abort"}
TASK_FIELDS = ("title", "module", "type", "description", "reads", "outputs", "contracts", "acceptance", "verify",
               "depends_on", "covers_criteria", "complexity", "suggested_agent", "notes")
BRAIN_LIMIT_EXTRA = 10  # `jev devam` beyin sınırında durmuş koşuya bu kadar ek hak verir


def brain_limit(run) -> int:
    return int(run.cfg.limit("kosu_basina_beyin_cagrisi")) + int(run.state.get("brain_limit_extra", 0) or 0)


def available_agents(run) -> list[str]:
    """Beyne gösterilen müsait ajanlar: işçiler, sonra yalnızca Jev'in kararıyla çalışan eskalasyon ajanları."""
    cfg = run.cfg
    esc = cfg.get("yonlendirme", "eskalasyon", default=[]) or []
    names = [a for a in cfg.workers() if a not in esc]
    for a in esc:
        if a in cfg.agents and a not in names:
            names.append(a)
    return [a for a in names if run.quota.available(a)]


def _worker_for(cfg, agent: str, task: dict) -> bool:
    """Ajan bu görevi kendiliğinden alır mı; almıyorsa (Opus'a kolay görev) yalnızca Jev'in kararıyla: eskalasyon."""
    return cfg.has_role(agent, "isci") and cfg.takes(agent, task.get("complexity"))


def cooling_agents(run) -> dict[str, str]:
    out = {}
    for a in run.cfg.agents:
        u = run.quota.until(a)
        if u is not None:
            out[a] = local_hhmm(u)
    return out


# --- karar ------------------------------------------------------------------------------

def handle_problem(run, task: dict, outcome: str, problem: str) -> None:
    """Görev `needs_decision` durumunda gelir; karar uygulanınca görev yeniden kuyruğa girer ya da kapanır."""
    max_att = run.cfg.attempts(scale_level(run.state))
    exhausted = int(task.get("attempt_count", 0)) >= max_att
    dec, who, call = consult(run, task, outcome, problem, exhausted)
    if dec is None:
        dec, who = simple_rule(run, task, exhausted), "kural"
    apply_decision(run, task, dec, who=who, call=call, outcome=outcome, problem=problem, exhausted=exhausted)


def _count_call(run) -> None:
    st = run.state
    limit = brain_limit(run)
    if int(st.get("brain_calls", 0)) >= limit:
        raise RunPaused(f"Koşu başına beyin çağrısı sınırı ({limit}) doldu. Kararlar: "
                        f"{run.rdir / 'decisions.jsonl'}. `jev devam` {BRAIN_LIMIT_EXTRA} ek hak verir.",
                        kind="beyin_siniri")
    st["brain_calls"] = int(st.get("brain_calls", 0)) + 1
    run.save()


def consult(run, task: dict, outcome: str, problem: str, exhausted: bool) -> tuple[dict | None, str, str | None]:
    """Önce Jev'in kendi modeli (TypeSafe Jev) karar verir. Split/revise görev metni ister, Jev metin yazamaz:
    metni beyin (Opus, yoksa Sol) Jev'in kararına bağlı kalarak yazar. Jev emin değilse (güven eşiğin altında)
    ya da modele ulaşılamıyorsa karar tümüyle beyne kalır."""
    problem_text = f"{context.OUTCOME_TR.get(outcome, outcome)}\n\n{shorten(problem, 6000)}"
    jev = getattr(run, "jev", None)
    if jev is None or not jev.enabled:
        return _text_brain(run, task, outcome, problem, problem_text, exhausted)
    _count_call(run)
    run.emit("jev.consult", {"task_id": task["id"], "brain": "jev", "problem": outcome,
                             "text": shorten(problem, 300)})
    run.emit("agent.state", {"agent": "jev", "state": "consulting", "task_id": task["id"], "phase": "brain",
                             "text": "Jev karar veriyor", "until": None})
    run.term.line(f"? {task['id']}: {context.OUTCOME_TR.get(outcome, outcome)} → Jev karar veriyor", "yellow")
    v = jev_decide(run, task, outcome, problem_text, exhausted)
    if v is None:
        run.log("warn", f"Jev modeli karar veremedi ({shorten(jev.last_error, 200)}); karar beyne bırakıldı.")
        return _text_brain(run, task, outcome, problem, problem_text, exhausted, counted=True)
    dec, conf = v["decision"], v["confidence"]
    if dec["decision"] in ("split", "revise"):
        note = (f"\n\nJEV'İN KARARI (TypeSafe Jev modeli, güven {conf:.2f}): {dec['decision']}. Bu kararı uygula: "
                f"decision alanına \"{dec['decision']}\" yaz. Senin işin bu kararın içeriğini (new_tasks ya da "
                f"revised_task, guidance, rationale) yazmak. Jev'in kök neden tahmini: {v['cause_tr']}.")
        got, who, call = _text_brain(run, task, outcome, problem, problem_text, exhausted, counted=True,
                                     note=note, extra={"kuru_karar": v.get("kuru")})
        if got is None:
            run.log("warn", f"{task['id']}: Jev '{dec['decision']}' dedi ama görev metnini yazacak beyin yok.")
            return None, "kural", None
        got = {**got, "decision": dec["decision"],
               "rationale": f"{dec['rationale']} Görev metnini {context.display(who)} yazdı: "
                            f"{got.get('rationale') or ''}".strip()}
        return got, "jev", call
    if conf < jev.threshold:
        note = (f"\n\nJEV'İN ÖN DEĞERLENDİRMESİ (TypeSafe Jev modeli, emin değil; güven {conf:.2f} < "
                f"{jev.threshold:.2f}): {v['ranked_tr']}. Kök neden tahmini: {v['cause_tr']}. Son kararı sen ver.")
        got, who, call = _text_brain(run, task, outcome, problem, problem_text, exhausted, counted=True,
                                     note=note, extra={"kuru_karar": v.get("kuru")})
        if got is not None:
            got = {**got, "rationale": f"Jev emin değildi ({v['ranked_tr']}); karar {context.display(who)}'a "
                                       f"bırakıldı. {got.get('rationale') or ''}".strip()}
            return got, who, call
        run.log("info", f"{task['id']}: beyin müsait değil; Jev'in düşük güvenli kararı uygulanıyor.")
    return dec, "jev", None


JEV_DECISIONS = {
    "retry": "Aynı ajanla yeniden dene: sorun geçici ya da küçük; deneme geçmişindeki hata kolayca giderilebilir.",
    "reassign": "Görevi başka bir ajana ver: bu ajan aynı hatayı tekrarlıyor, takılıyor ya da görevi kavrayamadı.",
    "split": "Görevi daha küçük alt görevlere böl: görev tek denemede bitemeyecek kadar büyük ya da çok parçalı.",
    "revise": "Görev tanımını düzelt: kabul ölçütü, doğrulama komutu ya da açıklama yanlış, çelişkili ya da eksik.",
    "skip": "Görevi atla: görev gereksiz ya da başka görevler onu zaten karşılıyor; sapma raporlanır.",
    "pause": "Koşuyu duraklat: insan müdahalesi gerekiyor (oturum, izin, dış servis, belirsiz istek).",
    "abort": "Koşuyu iptal et: plan temelden yanlış ya da devam etmek projeye zarar verir.",
}
JEV_CAUSES = {
    "ortam": "ortam / kurulum (eksik araç, bağımlılık, yol, izin)",
    "dogrulama": "doğrulama komutu ya da kabul ölçütü hatalı",
    "kod": "koddaki bir hata (derleme, test, mantık)",
    "yaklasim": "yanlış yaklaşım; ajan görevi yanlış anladı",
    "buyuk": "görev çok büyük ya da çok parçalı",
    "ajan": "ajanın kendisi (zaman aşımı, takılma, çökme, kota)",
    "dis": "dış etken (ağ, servis, oturum)",
    "koruma": "koruma engeli (izin verilmeyen komut ya da yol)",
}
CAUSE_GUIDANCE = {
    "ortam": "Önce ortamı doğrula: gereken araç ve bağımlılıklar kurulu mu, yollar doğru mu? Kurulumu düzelt, sonra "
             "görevi yap.",
    "dogrulama": "Doğrulama komutunun kendisini incele; komut ya da beklenen çıktı yanlışsa bunu özetinde açıkça yaz.",
    "kod": "Başarısız doğrulamanın çıktısını oku; hatayı gideren en küçük değişikliği yap ve doğrulamayı yeniden çalıştır.",
    "yaklasim": "Önceki denemenin yaklaşımı işe yaramadı; görev tanımını ve kabul ölçütlerini baştan oku, farklı bir "
                "yol dene.",
    "buyuk": "Görevi adım adım ilerlet; her adımdan sonra doğrula, gereksiz dosyalara dokunma.",
    "ajan": "Önceki deneme yarıda kaldı; uzun süren komutlardan kaçın, işi küçük adımlarla bitir.",
    "dis": "Önceki denemeyi dış bir etken durdurdu; ağa ya da dış servise bağımlı adımları en aza indir.",
    "koruma": "Önceki deneme korumaya takıldı; yalnızca proje klasörü içinde ve izin verilen komutlarla çalış.",
}
JEV_EFFORTS = {"varsayilan": "Görevin karmaşıklığına göre olağan efor yeterli.",
               "medium": "Kolay bir düzeltme; orta efor yeter.",
               "high": "Dikkatli çalışma gerekiyor; yüksek efor.",
               "xhigh": "Zor bir sorun; en yüksek efor."}
JEV_DEVIATIONS = {"none": "Karar plandan sapma yaratmaz.",
                  "minor": "Küçük sapma: plan özünde korunur, raporda not edilir.",
                  "major": "Büyük sapma: bir başarı ölçütü karşılanmayabilir ya da kapsam değişir."}


def _pct(p: float) -> str:
    return f"%{round(p * 100)}"


def jev_decide(run, task: dict, outcome: str, problem_text: str, exhausted: bool) -> dict | None:
    """TypeSafe Jev'e sorunlu görevi sorar; kararı jev_decision biçiminde döndürür. Başarısızlıkta None."""
    cfg, st = run.cfg, run.state
    max_att = cfg.attempts(scale_level(st))
    tried = [a.get("agent") for a in task.get("attempts") or [] if a.get("counted")]
    avail = available_agents(run)
    agents = {a: (cfg.agents[a].get("tanim") or cfg.label(a)) + ("" if _worker_for(cfg, a, task) else " (eskalasyon)")
              for a in avail}
    allowed = [d for d in JEV_DECISIONS if not exhausted or d in EXHAUSTED_ALLOWED]
    if not agents:
        allowed = [d for d in allowed if d not in ("retry", "reassign", "revise")] or allowed
    state = {
        "rol": "Jev, çok ajanlı bir yazılım ofisinin ustabaşısı. Bir görev sorun çıkardı; ne yapılacağına Jev karar "
               "verir. Kod yazmaz; kararı ajanlar uygular.",
        "plan_ozeti": run.plan.get("summary", ""),
        "gorev": context.task_public(task),
        "ilgili_olcutler": context.criteria_text(run.plan, task.get("covers_criteria") or []),
        "sorun": problem_text,
        "deneme_gecmisi": shorten(context.attempt_history(task, tail=60) or "yok", 8000),
        "denenen_ajanlar": tried,
        "basarisiz_ajanlar": list(task.get("failed_agents") or []),
        "musait_ajanlar": [{"ad": a, "tanim": d} for a, d in agents.items()],
        "sogumadakiler": cooling_agents(run),
        "kalan_deneme": max(0, max_att - int(task.get("attempt_count", 0))),
        "bu_goreve_bagli_gorevler": context.dependents_of(st, task["id"]),
        "graf": context.graph_summary(st),
    }
    qs = {
        "karar": systemone.choice("Bu sorunlu görev için Jev ne yapmalı?"
                                  + (" Deneme hakkı bitti: yalnızca atla, duraklat ya da iptal." if exhausted else ""),
                                  {d: JEV_DECISIONS[d] for d in allowed}),
        "kok_neden": systemone.choice("Sorunun kök nedeni en çok hangisi?", JEV_CAUSES),
        "efor": systemone.choice("Sonraki deneme için ajan eforu ne olmalı?", JEV_EFFORTS),
        "geri_al": systemone.noul("Önceki denemenin değişiklikleri geri alınıp temiz başlanmalı mı?",
                                  "Evet: değişiklikler işe yaramıyor ya da zarar veriyor, geri al.",
                                  "Hayır: değişiklikler doğru yönde, korunarak devam edilmeli."),
        "sapma": systemone.choice("Bu karar plandan ne kadar sapma yaratır?", JEV_DEVIATIONS),
    }
    if agents:
        qs["ajan"] = systemone.choice("Görev yeniden denenecekse hangi ajan en uygun? Aynı hatayı tekrarlayan "
                                      "ajandan kaçın.", agents)
    res = run.jev.ask("karar", state, qs, task_id=task["id"])
    if not res.ok:
        return None
    A = res.answers
    d = A["karar"].value
    cause = A["kok_neden"].value
    agent = None
    if "ajan" in A and d in ("retry", "reassign", "revise"):
        agent = A["ajan"].value
        if d == "reassign" and agent in tried:
            agent = next((a for a, _ in A["ajan"].ranked() if a not in tried and a in agents), agent)
        if d == "retry" and agent in tried[-1:]:
            agent = None  # aynı ajan: kural son ajanı zaten seçer
    effort = A["efor"].value
    first_err = next((ln.strip() for ln in problem_text.splitlines()[1:] if ln.strip()), "")
    guidance = CAUSE_GUIDANCE.get(cause, "")
    if first_err and d in ("retry", "reassign", "revise"):
        guidance += f" Son sorun: {shorten(first_err, 300)}"
    conf = float(A["karar"].confidence)
    ranked = ", ".join(f"{context.DECISION_TR.get(k, k)} {_pct(p)}" for k, p in A["karar"].ranked()[:3])
    cause_tr = f"{JEV_CAUSES[cause]} ({_pct(A['kok_neden'].p())})"
    rationale = (f"Jev modeli: {context.DECISION_TR.get(d, d)} ({_pct(A['karar'].p())}, güven {conf:.2f}) · "
                 f"kök neden: {cause_tr}.")
    deviation = A["sapma"].value
    if d == "skip" and deviation == "none":
        deviation = "minor"
    dec = {"decision": d, "agent": agent, "effort": None if effort == "varsayilan" else effort,
           "rollback": A["geri_al"].value >= 0.5, "guidance": guidance, "revised_task": None, "new_tasks": [],
           "rationale": rationale, "deviation": deviation,
           "report_note": f"{task['id']}: Jev modeli '{context.DECISION_TR.get(d, d)}' kararı verdi ({cause_tr})."
                          if d in ("skip", "abort", "pause") or deviation != "none" else ""}
    kuru = res.extra.get("kuru_karar")
    if isinstance(kuru, dict):
        # kuru koşu: senaryodaki karar olduğu gibi uygulanır (senaryolar eski beyinle aynı sonucu versin)
        dec.update({k: v for k, v in kuru.items() if k != "rationale" and (v is not None or k == "agent")})
        if kuru.get("rationale"):
            dec["rationale"] = f"{rationale} {kuru['rationale']}"
    return {"decision": dec, "confidence": conf, "ranked_tr": ranked, "cause_tr": cause_tr, "kuru": kuru}


def _text_brain(run, task: dict, outcome: str, problem: str, problem_text: str, exhausted: bool, *,
                counted: bool = False, note: str = "", extra: dict | None = None
                ) -> tuple[dict | None, str, str | None]:
    """Metin yazan beyin: Opus, soğumadaysa yedek beyin Sol. `counted`: bu karar beyin sınırına zaten sayıldı."""
    cfg, st = run.cfg, run.state
    cap = cfg.brain_cap(scale_level(st))  # küçük işte beyin de hafif düşünür
    brains = [(cfg.brain, cfg.effort(cfg.brain, task.get("complexity") or "varsayilan", cap))]
    fb_agent, fb_effort = cfg.fallback_brain
    if fb_agent and fb_agent != cfg.brain:
        brains.append((fb_agent, cap_effort(fb_effort, cap)))
    max_att = cfg.attempts(scale_level(st))
    for agent, effort in brains:
        if not run.quota.available(agent):
            run.log("info", f"Beyin {context.display(agent)} soğumada (~{local_hhmm(run.quota.until(agent))}).")
            continue
        if counted:
            counted = False  # yedek beyne geçilirse o çağrı ayrıca sayılır (eski davranış)
        else:
            _count_call(run)
        avail = available_agents(run)
        run.emit("jev.consult", {"task_id": task["id"], "brain": agent, "problem": outcome,
                                 "text": shorten(problem, 300)})
        run.emit("agent.state", {"agent": "jev", "state": "consulting", "task_id": task["id"], "phase": "brain",
                                 "text": f"Jev, {ek(context.display(agent), 'e')} danışıyor", "until": None})
        run.term.line(f"? {task['id']}: {context.OUTCOME_TR.get(outcome, outcome)} → Jev, "
                      f"{run.term.agent(context.display(agent))}{suffix(context.display(agent), 'e')} danışıyor", "yellow")
        shown = [a + ("" if _worker_for(cfg, a, task) else " (eskalasyon)") for a in avail]
        prompt = context.brain_prompt(cfg, st, run.plan, task, problem_text, run.rdir, shown, cooling_agents(run))
        prompt = prompt.rstrip() + note + "\n" if note else prompt
        tried = [a.get("agent") for a in task.get("attempts") or [] if a.get("counted")]
        res = run.gw.call(agent, "brain", prompt, schema_name="jev_decision", readonly=True, effort=effort,
                          timeout_key="beyin", task_id=task["id"], attempt=int(task.get("attempt_count", 0)),
                          extra={"tried": tried, "available": avail, "problem": outcome,
                                 "remaining_attempts": max(0, max_att - int(task.get("attempt_count", 0))),
                                 **{k: v for k, v in (extra or {}).items() if v is not None}},
                          state_text="Jev'e karar veriyor", workdir=lanes.workdir(run, task))
        if res.ok and isinstance(res.structured, dict):
            call = (res.paths.get("stdout") or "").replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".stdout.jsonl")
            return res.structured, agent, call or None
        if res.error_kind == "cancelled":
            raise KeyboardInterrupt
        if res.error_kind == "refusal":  # Opus (4.8 ile de) reddetti: yalnızca bu karar yedek beyne (Sol) geçer
            st.setdefault("retler", []).append({"agent": agent, "asama": "karar", "yerine": fb_agent,
                                                "gorev": task["id"], "neden": shorten(res.error_text, 300)})
        run.log("warn", f"Beyin ({context.display(agent)}) karar veremedi: {res.error_kind} "
                        f"{shorten(res.error_text, 200)}")
    return None, "kural", None


def simple_rule(run, task: dict, exhausted: bool) -> dict:
    """Beyin yok: görevi bir kez başka bir işçiye ver; yine olmazsa duraklat."""
    cfg = run.cfg
    base = {"effort": None, "rollback": True, "guidance": "", "revised_task": None, "new_tasks": [],
            "deviation": "none", "report_note": ""}
    if not exhausted and not task.get("simple_rule_used"):
        failed = set(task.get("failed_agents") or [])
        pool = candidates(task, cfg) + [a for a in cfg.workers() if cfg.takes(a, task.get("complexity"))]
        pick = next((a for a in pool if a not in failed and run.quota.available(a)), None)
        if pick:
            task["simple_rule_used"] = True
            return {**base, "decision": "reassign", "agent": pick,
                    "guidance": "Önceki denemenin sorununu (deneme geçmişinde) oku ve gider.",
                    "rationale": "Jev'in beyni (Opus ve yedek Sol) müsait değil; basit kural: görev bir kez başka "
                                 "bir ajana verildi."}
    waits = [u for u in (run.quota.until(a) for a in (cfg.brain, cfg.fallback_brain[0])) if u]
    until = min(waits) if waits else None
    note = f" Beyin ~{ek(local_hhmm(until), 'de')} müsait olur." if until else ""
    return {**base, "decision": "pause", "agent": None,
            "rationale": "Jev'in beyni müsait değil ve basit kural tükendi; koşu duraklatıldı." + note}


# --- uygulama ---------------------------------------------------------------------------

def apply_decision(run, task: dict, dec: dict, *, who: str, call: str | None, outcome: str, problem: str,
                   exhausted: bool) -> None:
    cfg, st = run.cfg, run.state
    d = dec.get("decision")
    notes: list[str] = []
    deviation = dec.get("deviation") or "none"
    target = dec.get("agent")
    if target and target not in cfg.agents:
        notes.append(f"Bilinmeyen ajan {target!r}; ajan seçimi Jev'e bırakıldı.")
        target = None
    elif target and not (cfg.has_role(target, "isci") or cfg.has_role(target, "eskalasyon")):
        notes.append(f"{context.display(target)} işçi olarak çalışamaz; ajan seçimi Jev'e bırakıldı.")
        target = None
    if target != dec.get("agent"):
        dec = {**dec, "agent": target}  # kayıt ve ofis geçersiz hedefi göstermesin; not nedenini açıklar

    if exhausted and d not in EXHAUSTED_ALLOWED:
        notes.append(f"Deneme hakkı bittiği hâlde '{d}' kararı verildi; görev başarısız sayıldı.")
        _rollback(run, task)
        _log(run, task, dec, who, call, outcome, problem, notes, final="failed")
        _set(run, task, "failed", forced=None)
        run.term.error(f"{task['id']} başarısız: deneme hakkı bitti.")
        return

    if d == "split":
        ok, err = _apply_split(run, task, dec)
        if not ok:
            notes.append("Bölme geçersiz: " + err)
            run.log("warn", f"{task['id']}: bölme kararı geçersiz ({shorten(err, 200)}); basit kural uygulanıyor.")
            _log(run, task, dec, who, call, outcome, problem, notes, final="gecersiz")
            alt = simple_rule(run, task, exhausted)
            return apply_decision(run, task, alt, who="kural", call=None, outcome=outcome, problem=problem,
                                  exhausted=exhausted)
        _log(run, task, dec, who, call, outcome, problem, notes, final="split")
        return

    if d == "revise":
        ok, err = _apply_revise(run, task, dec.get("revised_task"), notes)
        if not ok:
            notes.append("Düzeltilmiş görev geçersiz: " + err + " Görev aynı tanımla yeniden denenecek.")

    if d in ("retry", "reassign", "revise"):
        if dec.get("rollback", True):
            _rollback(run, task)
        else:
            task["keep_changes"] = True
            notes.append("Önceki denemenin değişiklikleri korunarak devam ediliyor.")
        last = next((a.get("agent") for a in reversed(task.get("attempts") or []) if a.get("counted")), None)
        if d == "retry":
            agent = target or last
        else:
            agent = target  # None → Jev'in kuralı seçer
        eff = dec.get("effort")
        task["forced"] = {"agent": agent, "effort": eff} if (agent or eff) else None
        task["guidance"] = dec.get("guidance") or ""
        _log(run, task, dec, who, call, outcome, problem, notes, final=d)
        _set(run, task, "ready")
        return

    if d == "skip":
        if (task.get("covers_criteria") or []) and deviation != "major":
            deviation = "major"
            dec = {**dec, "deviation": "major"}
            notes.append("Başarı ölçütü karşılayan görev atlandı: sapma 'major' yapıldı.")
        _rollback(run, task)
        _log(run, task, dec, who, call, outcome, problem, notes, final="skipped")
        _set(run, task, "skipped", forced=None)
        run.term.warn(f"{task['id']} atlandı (sapma: {deviation}).")
        return

    if d == "pause":
        _rollback(run, task)
        _log(run, task, dec, who, call, outcome, problem, notes, final="pause")
        task["guidance"] = dec.get("guidance") or task.get("guidance") or ""
        _set(run, task, "ready")
        raise RunPaused(f"Jev koşuyu duraklattı ({task['id']}): {shorten(dec.get('rationale') or '', 400)}",
                        kind="karar")

    if d == "abort":
        if dec.get("rollback", True):
            _rollback(run, task)
        _log(run, task, dec, who, call, outcome, problem, notes, final="abort")
        _set(run, task, "failed", forced=None)
        raise RunAborted(f"Jev koşuyu iptal etti ({task['id']}): {shorten(dec.get('rationale') or '', 400)}")

    # bilinmeyen karar: şema buna izin vermez; yine de güvenli taraf
    notes.append(f"Bilinmeyen karar {d!r}; basit kural uygulandı.")
    _log(run, task, dec, who, call, outcome, problem, notes, final="gecersiz")
    alt = simple_rule(run, task, exhausted)
    apply_decision(run, task, alt, who="kural", call=None, outcome=outcome, problem=problem, exhausted=exhausted)


def _set(run, task: dict, status: str, **fields) -> None:
    task["status"] = status
    task.update(fields)
    run.save()
    run.emit("task.update", task_event(task))


def _rollback(run, task: dict) -> None:
    lanes.rollback(run, task)  # paralel görevde şerit kaldırılır; ana projeye dokunulmaz


def _log(run, task: dict, dec: dict, who: str, call: str | None, outcome: str, problem: str, notes: list[str],
         final: str) -> None:
    st = run.state
    rec = {"ts": iso(), "round": st.get("round", 1), "task_id": task["id"],
           "attempt": int(task.get("attempt_count", 0)), "problem": outcome, "problem_text": shorten(problem, 800),
           "decision": dec.get("decision"), "agent": dec.get("agent"), "effort": dec.get("effort"),
           "rollback": bool(dec.get("rollback", True)), "guidance": dec.get("guidance") or "",
           "rationale": dec.get("rationale") or "", "deviation": dec.get("deviation") or "none",
           "report_note": dec.get("report_note") or "", "brain": who, "call": call, "applied": final,
           "new_tasks": [t.get("id") for t in dec.get("new_tasks") or []] if dec.get("decision") == "split" else [],
           "notes": notes}
    context.log_decision(run.rdir, rec)
    task.setdefault("decisions", []).append({k: rec[k] for k in ("decision", "agent", "brain", "rationale",
                                                                 "deviation", "applied")})
    if rec["deviation"] != "none":
        st.setdefault("deviations", []).append({"task_id": task["id"], "decision": rec["decision"],
                                                "deviation": rec["deviation"],
                                                "note": rec["report_note"] or rec["rationale"]})
    run.save()
    run.emit("jev.decision", {"task_id": task["id"], "decision": rec["decision"], "agent": rec["agent"],
                              "rationale": shorten(rec["rationale"], 600), "deviation": rec["deviation"],
                              "brain": who, "applied": final, "notes": notes, "problem": outcome,
                              "guidance": shorten(rec["guidance"], 300)})
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": "executing",
                             "text": "", "until": None})
    label = context.DECISION_TR.get(rec["decision"], rec["decision"])
    target = f" → {context.display(rec['agent'])}" if rec.get("agent") else ""
    who_txt = {"kural": "basit kural", "jev": "Jev modeli"}.get(who) or context.display(who)
    run.term.line(f"◆ Jev'in kararı ({who_txt}): {task['id']} {label}{target} — {shorten(rec['rationale'], 160)}",
                  "orange")
    for n in notes:
        run.term.dim("  " + n)


def _apply_revise(run, task: dict, revised: dict | None, notes: list[str]) -> tuple[bool, str]:
    if not isinstance(revised, dict):
        return False, "revised_task boş."
    new = copy.deepcopy(task)
    for k in TASK_FIELDS:
        if k in revised and revised[k] is not None:
            new[k] = copy.deepcopy(revised[k])
    new["id"] = task["id"]
    old_cov = list(task.get("covers_criteria") or [])
    if set(old_cov) - set(new.get("covers_criteria") or []):
        new["covers_criteria"] = old_cov + [c for c in new.get("covers_criteria") or [] if c not in old_cov]
        notes.append("covers_criteria daraltılamaz; eski ölçütler korundu.")
    others = [t for t in run.state["tasks"] if t["id"] != task["id"]]
    errs = validate_tasks([new], run.plan, existing=others, require_coverage=False)
    if errs and new.get("depends_on") != task.get("depends_on"):
        new["depends_on"] = list(task.get("depends_on") or [])
        errs = validate_tasks([new], run.plan, existing=others, require_coverage=False)
        if not errs:
            notes.append("Yeni bağımlılıklar geçersizdi; eski bağımlılıklar korundu.")
    if errs:
        return False, "; ".join(errs[:6])
    links = link_shared_files([new], existing=others)
    if links:
        notes.append("Jev aynı dosyaya dokunan kartları sıraya koydu: " + "; ".join(links[:6]))
    for k in TASK_FIELDS:
        task[k] = new.get(k)
    task["revised"] = int(task.get("revised", 0) or 0) + 1
    return True, ""


def _apply_split(run, task: dict, dec: dict) -> tuple[bool, str]:
    st = run.state
    new = [dict(t) for t in dec.get("new_tasks") or []]
    if not new:
        return False, "new_tasks boş."
    new_ids = [t.get("id") for t in new]
    others = []
    for t in st["tasks"]:
        if t["id"] == task["id"]:
            continue
        c = dict(t)
        deps = list(c.get("depends_on") or [])
        if task["id"] in deps:
            deps = [d for d in deps if d != task["id"]] + [i for i in new_ids if i not in deps]
            c["depends_on"] = deps
        others.append(c)
    errs = validate_tasks(new, run.plan, existing=others, require_coverage=False)
    for t in new:
        if task["id"] in (t.get("depends_on") or []):
            errs.append(f"{t.get('id')} bölünen göreve ({task['id']}) bağımlı olamaz.")
    lost = set(task.get("covers_criteria") or []) - {c for t in new for c in t.get("covers_criteria") or []}
    if lost:
        errs.append("Alt görevler şu ölçütleri kapsamıyor: " + ", ".join(sorted(lost)) + ".")
    if errs:
        return False, "; ".join(errs[:8])
    links = link_shared_files(new, existing=others)
    if links:
        run.log("info", "Jev aynı dosyalara dokunan kartları sıraya koydu: " + "; ".join(links[:8]))
    _rollback(run, task)
    # bağımlıları yeniden bağla, alt görevleri eski görevin hemen arkasına ekle
    for t in st["tasks"]:
        deps = list(t.get("depends_on") or [])
        if task["id"] in deps and t["id"] != task["id"]:
            t["depends_on"] = [d for d in deps if d != task["id"]] + [i for i in new_ids if i not in deps]
    idx = st["tasks"].index(task)
    subs = [init_task(t, origin="split", round_=int(task.get("round", st.get("round", 1)))) for t in new]
    for s in subs:
        s["parent"] = task["id"]
    st["tasks"][idx + 1:idx + 1] = subs
    task["status"] = "split"
    task["children"] = new_ids
    task["forced"] = None
    run.save()
    run.emit("task.update", task_event(task))
    for s in subs:
        run.emit("task.update", task_event(s))
    run.term.line(f"  {task['id']} bölündü: {', '.join(new_ids)}", "orange")
    return True, ""
