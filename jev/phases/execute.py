"""[3] UYGULAMA: Jev hazır görevi seçer, ajana verir, sonucu KENDİSİ doğrular (belirtim §5.6).

Bir deneme üç adımdır: `begin` (çalışma klasörü, checkpoint, prompt), `work` (ajan ve doğrulama; paralel görevde kendi
iş parçacığında) ve `conclude` (başarı, birleştirme ya da sorun). Birbirini beklemeyen görevler aynı anda, her biri
kendi git çalışma ağacında (şerit, lanes.py) çalışır; dağıtımı parallel.py yapar.

Sorunlarda önce kural katmanı (kota, oturum); diğer her durum Jev'in beynine gider (brain.py).
Bu modül planlayıcıyı ve denetçiyi çağırmaz. İnternet izni yalnızca araştırma kartına ve tek görevli işe verilir.
"""
from __future__ import annotations

import re
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import context, gitops, guard, lanes, routing
from ..dag import CODE_TYPES
from ..state import STATUS_TR, TERMINAL_TASK, scale_level
from ..util import ek, iso, local_hhmm, now, read_jsonl, refresh_path, shorten, tail_lines
from ..verify import run_command
from . import sizing
from .brain import handle_problem
from .common import RunPaused, refusal_partner, task_event, wait_or_pause

PROTECTED_FILES = {"AGENTS.md", "CLAUDE.md", ".gitignore"}
# birden çok sorun varsa beyne gösterilen ana sorun (önem sırası)
OUTCOME_PRIORITY = ("guard", "audit", "verify_failed", "missing_outputs", "no_diff")
# Tek görevli işte Jev'in benimsediği işçi doğrulama komutları: en fazla bu kadar
MAX_ADOPTED = 5
# Doğrulamada yeniden çalıştırılmayan komutlar: pencere, tarayıcı, düzenleyici ya da sunucu açıp bekleyenler
_OPENS = re.compile(
    r"(?:^|[\s;&|(])(?:start|start-process|saps|invoke-item|ii|explorer(?:\.exe)?|code|notepad(?:\.exe)?|"
    r"xdg-open|serve|live-server|http-server)(?=$|[\s;&|)])"
    r"|http\.server|\bnpm\s+(?:start|run\s+(?:dev|start|serve|preview))\b"
    r"|\bnpx\s+(?:serve|http-server|live-server|vite)\b|\bflask\s+run\b|\buvicorn\b|\brunserver\b"
    r"|-m\s+webbrowser\b", re.I)
# Jev'in kabul sorusunda örnek dosya içeriği: en çok bu kadar dosya ve dosya başına karakter
SAMPLE_EXT = (".html", ".htm", ".py", ".js", ".mjs", ".ts", ".md", ".svg", ".css", ".ps1", ".json", ".txt")
SAMPLE_FILES, SAMPLE_CHARS = 3, 2500
PROBLEM_TR = {"bicim": "biçim alışılmışın dışında", "eksik": "isteğin önemli bir parçası eksik",
              "calismaz": "çalışır görünmüyor ya da doğrulama yüzeysel",
              "kalite": "gerçek kaynak yerine kaba taklit ya da özensiz sonuç", "yok": "belirgin bir sorun yok"}
# Paralel görevin işi aynı anda biten görevlerle bu kadar kez çakışırsa karar beyne gider
MAX_CONFLICTS = 2
# Paralel görevlerin doğrulama komutları sırayla çalışır (aynı bağlantı noktası, önbellek ve işlemci için yarışmasınlar)
_VERIFY_LOCK = threading.Lock()
_CONFLICT_MARK = re.compile(r"^(?:<{7}|>{7})(?: |$)", re.M)


def run_execute(run) -> None:
    st = run.state
    run.term.header(f"Uygulama · tur {st.get('round', 1)}")
    limit = parallel_limit(run)
    lanes_ok = True  # görev şeridi açılamazsa koşunun geri kalanı sırayla çalışır
    while True:
        if lanes_ok and wants_lanes(st, limit):
            from .parallel import run_parallel
            lanes_ok = run_parallel(run, limit)
        elif _run_sequential(run, limit if lanes_ok else 1):
            break
    _finish(run)


def parallel_limit(run) -> int:
    """Aynı anda en fazla kaç görev: ölçek profilinin `paralel` değeri. Kuru koşuda senaryonun `paralel` değeri de
    sınırdır (yoksa 1: mevcut senaryolar sırayla çalışır)."""
    n = run.cfg.parallel(scale_level(run.state))
    if run.state.get("dry_run"):
        n = min(n, int(sizing.scenario(run).get("paralel") or 1))
    return max(1, n)


def wants_lanes(st: dict, limit: int) -> bool:
    """Paralel çalışma: şeridi süren bir görev varsa ya da aynı anda başlayabilecek (hazır) iki görev varsa. Tek
    hazır görev proje klasöründe çalışır (şerit açma ve birleştirme maliyeti yok). Proje klasöründe korunan yarım
    iş varken (sırayla çalışmada beynin kararı) önce o görev biter."""
    open_tasks = [t for t in st.get("tasks", []) if t.get("status") not in TERMINAL_TASK]
    if any(t.get("lane") for t in open_tasks):
        return True
    if limit <= 1 or any(t.get("keep_changes") for t in open_tasks):
        return False
    return sum(1 for t in open_tasks if t.get("status") in ("ready", "waiting_quota")) >= 2


def _run_sequential(run, limit: int) -> bool:
    """Görevler tek tek, proje klasöründe. Bütün görevler bitince True; aynı anda başlayabilecek iki görev hazır
    olunca (ör. ortak bağımlılıkları bitti ya da görev bölündü) False döner ve uygulama paralel çalışmaya geçer."""
    st = run.state
    while True:
        run.poll_commands()
        for tid, status in routing.refresh(st):
            t = routing.by_id(st)[tid]
            run.emit("task.update", task_event(t))
            if status == "blocked":
                run.term.warn(f"{tid} engellendi: {t.get('blocked_reason', '')}")
        run.save()
        open_tasks = [t for t in st["tasks"] if t.get("status") not in TERMINAL_TASK]
        if not open_tasks:
            return True
        if limit > 1 and wants_lanes(st, limit):
            return False
        pick, waits = None, []
        for t in routing.ordered_ready(st):
            ch = routing.choose_agent(t, st, run.cfg, run.quota.available, run.quota.until,
                                      picker=_jev_picker(run))
            if ch.agent:
                pick = (t, ch)
                break
            if ch.wait_until:
                waits.append(ch.wait_until)
            mark_waiting(run, t, ch)
        if pick is None:
            if not [t for t in open_tasks if t.get("status") in ("ready", "waiting_quota")]:
                raise RunPaused("Görev grafiği ilerleyemiyor: " + ", ".join(
                    f"{t['id']}[{STATUS_TR.get(t.get('status'), t.get('status'))}]" for t in open_tasks))
            wait_or_pause(run, min(waits) if waits else None, "uygun işçilerin hepsi")
            continue
        task, choice = pick
        _drop_foreign_leftovers(run, task)
        attempt(run, task, choice)


def mark_waiting(run, task: dict, choice) -> None:
    """Uygun ajanların hepsi soğumada: görev kotanın açılmasını bekler."""
    if task.get("status") != "waiting_quota":
        task["status"] = "waiting_quota"
        task["waiting_until"] = iso(choice.wait_until) if choice.wait_until else None
        run.save()
        run.emit("task.update", task_event(task))


def _jev_picker(run):
    """Görevi hangi ajanın alacağını Jev modeli seçer. Çağrı başarısızsa ya da Jev emin değilse kural kalır."""
    jev = getattr(run, "jev", None)
    if jev is None or not jev.enabled or not jev.dispatch:
        return None
    from .. import systemone

    def pick(task: dict, free: list[str], rule_pick: str, rule_reason: str):
        cfg = run.cfg
        done = [t for t in run.state.get("tasks", []) if t.get("status") == "done" and t.get("agent")]
        state = {
            "rol": "Jev, çok ajanlı bir yazılım ofisinin ustabaşısı. Sıradaki görevi müsait ajanlardan birine verir.",
            "plan_ozeti": run.plan.get("summary", ""),
            "gorev": context.task_public(task),
            "basarisiz_ajanlar": list(task.get("failed_agents") or []),
            "kuralin_onerisi": {"ajan": rule_pick, "neden": rule_reason},
            "son_dagitimlar": [{"gorev": t["id"], "tur": t.get("type"), "ajan": t.get("agent")} for t in done[-8:]],
            "saglayici_serisi": run.state.get("provider_streak") or {},
        }
        crit = {a: f"{cfg.agents[a].get('tanim') or cfg.label(a)} (sağlayıcı: {cfg.provider(a)})" for a in free}
        q = {"ajan": systemone.choice("Bu görevi hangi ajan almalı? Görevin türüne, büyüklüğüne ve ajanların "
                                      "güçlü yanlarına bak; yükü sağlayıcılar arasında dengele.", crit)}
        res = jev.ask("dagitim", state, q, task_id=task["id"], hint={"ajan": rule_pick})
        if not res.ok:
            run.log("info", f"{task['id']}: Jev modeli ajan seçemedi ({shorten(res.error_text, 160)}); kural seçti.")
            return None
        a = res.answers["ajan"]
        if a.confidence < jev.threshold:
            return None
        return a.value, f"Jev modeli seçti (%{round(a.p() * 100)}, güven {a.confidence:.2f})"
    return pick


def _drop_foreign_leftovers(run, task: dict) -> None:
    """Başka bir görevin proje klasöründe korunmuş yarım değişiklikleri bu görevin commit'ine karışmasın (şeritteki
    yarım işler ana projeye dokunmaz)."""
    for t in run.state["tasks"]:
        if t is not task and t.get("keep_changes") and t.get("checkpoint") and not t.get("lane"):
            lanes.rollback(run, t)
            run.log("warn", f"{t['id']} görevinin korunan yarım değişiklikleri geri alındı ({task['id']} başlamadan önce).")


def _finish(run) -> None:
    st = run.state
    c = Counter(t.get("status") for t in st["tasks"])
    parts = [f"{n} {STATUS_TR.get(s, s).lower()}" for s, n in c.items() if n]
    run.term.ok("Görevler tamamlandı: " + ", ".join(parts))
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": "executing",
                             "text": "Görevler bitti; son kontrole geçiliyor", "until": None})
    run.set_phase("reviewing")


# --- tek deneme ---------------------------------------------------------------------------

@dataclass
class Job:
    """Bir görev denemesi. `begin` kurar, `work` ajanı çalıştırıp doğrular, `conclude` sonucu uygular."""
    task: dict
    choice: Any
    n: int
    cp: str
    escalation: bool
    guidance: str
    prompt: str
    guard_mark: int
    started: Any
    workdir: Path
    parallel: bool = False
    res: Any = None
    result: dict | None = None
    problems: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    full_ok: bool | None = None
    notes: list = field(default_factory=list)
    error: BaseException | None = None
    thread: threading.Thread | None = None

    @property
    def agent(self) -> str:
        return self.choice.agent


def _call_name(res) -> str | None:
    p = (res.paths or {}).get("stdout") or ""
    name = p.replace("\\", "/").rsplit("/", 1)[-1]
    return name.removesuffix(".stdout.jsonl") or None


def attempt(run, task: dict, choice) -> None:
    """Tek deneme, proje klasöründe (sırayla çalışma)."""
    job = begin(run, task, choice)
    work(run, job)
    conclude(run, job)


def begin(run, task: dict, choice, parallel: bool = False) -> Job:
    """Denemeyi hazırlar: çalışma klasörü (paralel görevde görevin şeridi), checkpoint, durum, olaylar ve prompt."""
    cfg, st = run.cfg, run.state
    agent = choice.agent
    n = int(task.get("attempt_count", 0)) + 1
    keep = bool(task.get("keep_changes") and task.get("checkpoint"))  # beyin değişiklikleri korumaya karar verdi
    if parallel:
        before = (task.get("lane") or {}).get("path")
        workdir = lanes.open_lane(run, task)
        keep = keep and bool(task.get("checkpoint")) and before == str(workdir)
    else:
        if task.get("lane"):  # şeritte kalmış yarım iş sırayla çalışmada kullanılamaz
            lanes.rollback(run, task)
            run.log("warn", f"{task['id']} görevinin şeritteki yarım değişiklikleri geri alındı (görevler sırayla "
                            "çalışıyor).")
            keep = False
        workdir = run.project
    cp = task["checkpoint"] if keep else gitops.checkpoint(workdir)
    if not keep:
        task.pop("conflict_files", None)
    task["keep_changes"] = False
    escalation = not cfg.has_role(agent, "isci")
    guidance = task.get("guidance") or ""
    task.update(status="running", agent=agent, checkpoint=cp, waiting_until=None)
    routing.note_dispatch(st, cfg.provider(agent))
    run.save()
    run.emit("task.update", task_event(task))
    run.emit("jev.dispatch", {"task_id": task["id"], "agent": agent, "reason": choice.reason,
                              "effort": choice.effort, "attempt": n, "escalation": escalation})
    run.emit("agent.state", {"agent": "jev", "state": "dispatch", "task_id": task["id"], "phase": "executing",
                             "text": f"{task['id']} → {context.display(agent)}", "until": None})
    extra_txt = (" · eskalasyon" if escalation else "") + (" · paralel" if parallel else "")
    run.term.line(f"▶ {task['id']} {shorten(task.get('title', ''), 60)} → {run.term.agent(context.display(agent))}"
                  f" (efor {choice.effort}, deneme {n}{extra_txt}) · {choice.reason}")

    if task.get("verify_from_worker"):  # tek görevli iş (mini, küçük): plan ve başka ajan yok
        prompt = context.mini_prompt(cfg, st, task, agent, run.rdir, workdir, _project_tree(workdir), guidance)
    else:
        prompt = context.worker_prompt(cfg, st, run.plan, task, agent, run.rdir, guidance,
                                       workdir=workdir if parallel else None)
    return Job(task=task, choice=choice, n=n, cp=cp, escalation=escalation, guidance=guidance, prompt=prompt,
               guard_mark=len(read_jsonl(run.rdir / "guard.jsonl")), started=now(), workdir=workdir,
               parallel=parallel)


def work(run, job: Job) -> None:
    """Ajanı çalıştırır ve biten işi doğrular. Paralel görevde kendi iş parçacığında çalışır: yalnızca kendi görevine
    dokunur; kararları ana iş parçacığı `conclude` ile uygular."""
    task, agent = job.task, job.agent
    res = run.gw.call(agent, "worker", job.prompt, schema_name="worker_result", readonly=False,
                      effort=job.choice.effort, timeout_key=f"isci_{task.get('complexity') or 'M'}",
                      task_id=task["id"], attempt=job.n,
                      extra={"outputs": list(task.get("outputs") or []), "type": task.get("type")},
                      escalation=job.escalation, end_state=None, workdir=job.workdir,
                      web=task.get("type") == "research" or bool(task.get("verify_from_worker")),
                      state_text=f"{task['id']}: {shorten(task.get('title', ''), 50)}")
    job.res = res
    if res.error_kind in ("cancelled", "quota", "auth", "refusal"):
        return
    result = res.structured if res.ok and isinstance(res.structured, dict) else None
    job.result = result
    if result is None:
        kind = res.error_kind if res.error_kind in context.OUTCOME_TR else "crash"
        job.problems.append((kind, res.error_text or "Ajan geçerli bir sonuç vermedi."))
    elif result.get("status") != "done":
        kind = "agent_failed" if result.get("status") == "failed" else "agent_blocked"
        job.problems.append((kind, (result.get("blocker") or "") + "\n" + (result.get("summary") or "")))
    else:
        job.problems, job.rows, job.full_ok, job.notes = verify_task(run, task, agent, job.cp, result,
                                                                     job.guard_mark)


def _refused(run, task: dict, agent: str, rec: dict, res) -> None:
    """İşçi kartı (yedek modeliyle de) güvenlik gerekçesiyle yanıtlamadı: deneme hakkından sayılmaz, beyin çağrılmaz.
    Kart ret yedeğine (Sol) ya da sıradaki işçiye geçer; reddeden yalnızca bu kartı bir daha almaz."""
    cfg = run.cfg
    lanes.rollback(run, task)
    rec.update(outcome="refusal", counted=False, problem=shorten(res.error_text, 400))
    task.setdefault("attempts", []).append(rec)
    if (task.get("forced") or {}).get("agent") == agent:
        task["forced"] = None
    task.update(status="ready", agent=None)
    refused = task.setdefault("ret_veren", [])
    if agent not in refused:
        refused.append(agent)
    partner = refusal_partner(run, agent, "görev", res.error_text, task["id"])
    if partner in refused:
        partner = None
    if partner:
        task["forced"] = {"agent": partner, "effort": None}
    run.save()
    run.emit("task.update", task_event(task))
    left = [a for a in cfg.workers() if a not in refused]
    to = f"{ek(context.display(partner), 'e')}" if partner else "sıradaki uygun ajana"
    run.term.warn(f"{task['id']}: {context.display(agent)} kartı güvenlik gerekçesiyle yanıtlamadı; kart {to} "
                  "verilecek (deneme hakkı eksilmedi).")
    if not left:
        raise RunPaused("Bütün işçiler görevleri güvenlik gerekçesiyle yanıtlamadı. İsteği netleştirip `jev devam` yaz.")


def conclude(run, job: Job) -> None:
    """Denemenin sonucunu uygular: başarı (paralel görevde koşu dalına birleştirme), kabul, çakışma ya da sorun."""
    cfg = run.cfg
    task, agent, res, cp = job.task, job.agent, job.res, job.cp
    rec = {"n": job.n, "agent": agent, "effort": job.choice.effort, "reason": job.choice.reason,
           "started": iso(job.started), "duration_s": round(res.duration_s, 1), "call": _call_name(res),
           "guidance": job.guidance}

    if res.error_kind == "cancelled":
        raise KeyboardInterrupt
    if res.error_kind == "refusal":
        _refused(run, task, agent, rec, res)
        return
    if res.error_kind in ("quota", "auth"):
        # kural katmanı: deneme hakkından sayılmaz, beyin çağrılmaz
        lanes.rollback(run, task)
        rec.update(outcome=res.error_kind, counted=False, problem=shorten(res.error_text, 400))
        task.setdefault("attempts", []).append(rec)
        task.update(status="ready", agent=None)
        run.save()
        run.emit("task.update", task_event(task))
        if res.error_kind == "auth":
            raise RunPaused(f"{cfg.label(agent)} için oturum kapalı ya da giriş gerekli. Giriş yapıp `jev devam` yaz.",
                            kind="oturum")
        run.term.warn(f"{task['id']}: {context.display(agent)} kotaya takıldı (~{ek(local_hhmm(res.reset_at), 'e')} kadar); "
                      "görev sıradaki uygun ajana verilecek (deneme hakkı eksilmedi).")
        return

    task["attempt_count"] = job.n
    rec["counted"] = True
    result = job.result
    problems: list[tuple[str, str]] = list(job.problems)
    full_ok = job.full_ok
    if result is None:
        rec["error"] = tail_lines(res.error_text or "", 150)
    else:
        rec["summary"] = result.get("summary", "")
        if result.get("notes"):
            rec["worker_notes"] = shorten(str(result["notes"]), 2000)
        if result.get("status") == "done":
            rec["verify"] = job.rows
            if job.notes:
                rec["notes"] = job.notes
    wd = lanes.workdir(run, task)
    rec["diffstat"] = gitops.shortstat_since(wd, cp) if gitops.current_branch(wd) == lanes.branch(run, task) else ""
    hits = _guard_hits(run, task, job.guard_mark)
    if hits:
        rec["guard"] = [{"category": h.get("category"), "reason": h.get("reason"), "serious": h.get("serious"),
                         "action": h.get("eylem")} for h in hits]

    if not problems:
        why = acceptance(run, task, result, rec.get("verify") or [], cp) if wants_acceptance(run, task) else None
        if why is not None:
            _not_accepted(run, task, agent, rec, why)
            return
        if not task.get("lane"):
            _succeed(run, task, agent, cp, result, rec, full_ok)
            return
        try:
            more, merged_ok = _integrate(run, task, agent, rec)
            if merged_ok is not None:
                full_ok = merged_ok
            if not more:
                _succeed(run, task, agent, task["checkpoint"], result, rec, full_ok)
                return
            problems += more
        except gitops.MergeConflict as e:
            text = _carry(run, task, agent, rec, e.files)
            if text is None:
                return
            problems.append(("merge_conflict", text))

    problems.sort(key=lambda p: OUTCOME_PRIORITY.index(p[0]) if p[0] in OUTCOME_PRIORITY else -1)
    outcome = problems[0][0]
    problem = "\n\n".join(f"[{context.OUTCOME_TR.get(k, k)}] {v.strip()}" for k, v in problems)
    rec.update(outcome=outcome, problem=shorten(problem, 3000))
    task.setdefault("attempts", []).append(rec)
    fa = task.setdefault("failed_agents", [])
    if agent not in fa and outcome != "merge_conflict":  # çakışma ajanın hatası değil
        fa.append(agent)
    task["status"] = "needs_decision"
    run.save()
    run.emit("task.update", task_event(task))
    face = "blocked" if outcome == "agent_blocked" else "failed"
    run.emit("agent.state", {"agent": agent, "state": face, "task_id": task["id"], "phase": "worker",
                             "text": _face_text(outcome, rec), "until": None})
    run.term.error(f"{task['id']} ({context.display(agent)}): {context.OUTCOME_TR.get(outcome, outcome)}"
                   f" — {_first_line(problems[0][1])}")
    if sizing.should_escalate(run, task):  # mini deneme hakkını bitirdi: beyne gitmeden küçüğe yükselir
        sizing.escalate(run, task, outcome, problem)
    else:
        handle_problem(run, task, outcome, problem)
    if task.get("status") not in ("running", "verifying"):
        run.emit("agent.state", {"agent": agent, "state": "idle", "task_id": None, "phase": "worker", "text": "",
                                 "until": None})


def _face_text(outcome: str, rec: dict) -> str:
    rows = rec.get("verify") or []
    if outcome == "verify_failed" and rows:
        bad = sum(1 for r in rows if not r.get("passed"))
        return f"Doğrulama kaldı ({bad}/{len(rows)})"
    return context.OUTCOME_TR.get(outcome, outcome).capitalize()


def _project_tree(project: Path) -> str:
    """Tek görevli işin dosya ağacı; yeni projede (yalnızca Jev'in .gitignore'u varken) boş."""
    tree = gitops.file_tree(project, 200)
    return "" if set(tree.splitlines()) <= {".gitignore"} else tree


def _commit_message(task: dict, agent: str) -> str:
    title = " ".join((task.get("title") or "").split())[:72]
    return f"jev({task['id']}): {title} [{agent}]"


# --- paralel görev: koşu dalına birleştirme ------------------------------------------------

def _integrate(run, task: dict, agent: str, rec: dict) -> tuple[list[tuple[str, str]], bool | None]:
    """Şeritte doğrulanan işi koşu dalının son hâliyle buluşturur. Görev çalışırken başka görevler birleştiyse şerit
    güncellenir ve tam test birleşik hâlde yeniden çalışır (aynı anda biten görevler birbirini bozmasın).
    Dönüş: (sorunlar, birleşik tam test geçti mi; çalışmadıysa None). Çakışmada gitops.MergeConflict."""
    cfg, st, project = run.cfg, run.state, run.project
    lane = task["lane"]
    path = Path(lane["path"])
    save_main(run)
    main = gitops.head(project)
    if main == lane.get("base"):
        return [], None
    gitops.commit_all(path, _commit_message(task, agent))
    gitops.update_from(path, main, f"jev: {task['id']} şeridi koşu dalının son hâline getirildi")
    # şerit = koşu dalının son hâli + görevin commit edilmemiş değişiklikleri (checkpoint her zaman şeridin HEAD'i)
    gitops.git(["reset", "-q", "--soft", main], path)
    lane["base"] = task["checkpoint"] = main
    run.save()
    test_cmd = ((run.plan.get("commands") or {}).get("test") or "").strip()
    if not (cfg.get("her_gorevde_tam_test", default=True) and test_cmd):
        return [], None
    run.emit("agent.state", {"agent": "jev", "state": "verifying", "task_id": task["id"], "phase": "executing",
                             "text": f"{task['id']}: aynı anda biten görevlerle birlikte test ediliyor", "until": None})
    with _verify_slot(run, True):
        refresh_path()
        r = _run_check(run, test_cmd, path)
    row = {**r.to_dict(60), "source": "birlesik_test"}
    rec.setdefault("verify", []).append(row)
    run.emit("verify", {"task_id": task["id"], "command": test_cmd, "passed": r.passed, "exit_code": r.exit_code,
                        "source": "birlesik_test"})
    if r.passed:
        return [], True
    if st.get("full_test_ok"):
        return [("verify_failed", f"Tam test (`{test_cmd}`) görevin kendi şeridinde geçti ama aynı anda biten "
                                  "görevlerin değişiklikleriyle birleşince kalıyor (gerileme):\n" + r.tail(150))], False
    row["note"] = "tam test daha önce de geçmiyordu; gerileme sayılmadı"
    return [], False


def save_main(run, why: str = "görev birleştirilmeden önce") -> None:
    """Proje klasöründeki kaydedilmemiş değişiklikleri commit eder. Görev şeritleri koşu dalının commit'lerinden açılır
    ve birleştirme hatasında ana ağaç sıfırlanır: commit edilmemiş iş ne şeritlere geçer ne de kaybolmalı."""
    if not gitops.is_clean(run.project):
        gitops.commit_all(run.project, "jev: kaydedilmemiş değişiklikler")
        run.log("warn", f"Proje klasöründe kaydedilmemiş değişiklikler vardı; {why} commit edildi.")


def _carry(run, task: dict, agent: str, rec: dict, files: list[str]) -> str | None:
    """Görevin işi aynı anda biten görevlerle çakıştı: şerit koşu dalının son hâlinden yeniden kurulur ve görevin
    değişiklikleri üstüne taşınır (çakışan yerlerde git işaretleriyle). İş aynı ajana, çakışmayı çözmesi için geri
    verilir (deneme hakkından sayılmaz). MAX_CONFLICTS aşılırsa sorun metni döner; karar beyne gider."""
    project = run.project
    lane = task.get("lane") or {}
    count = int(task.get("merge_conflicts", 0) or 0) + 1
    task["merge_conflicts"] = count
    shown = ", ".join(files[:8]) or "?"
    carried, left = False, []
    try:
        main = gitops.head(project)
        path = Path(lane["path"])
        left = gitops.carry(project, path, lane["branch"], main, gitops.head(path))
        lane["base"] = task["checkpoint"] = main
        task["conflict_files"] = left
        carried = True
    except (gitops.GitError, OSError, KeyError) as e:
        run.log("warn", f"{task['id']}: değişiklikler koşu dalının son hâline taşınamadı ({shorten(str(e), 200)}); "
                        "şerit kapatıldı.")
        lanes.close_lane(run, task)
        task.pop("conflict_files", None)
    if count > MAX_CONFLICTS:
        if carried and left:
            tail = "Değişiklikler koşunun son hâline taşındı; çakışan yerlerde git işaretleri duruyor."
        elif carried:
            tail = "Değişiklikler koşunun son hâline çakışmasız taşındı."
        else:
            tail = "Değişiklikler taşınamadı; şerit kapatıldı."
        return (f"Görevin işi aynı anda biten görevlerin değişiklikleriyle {count}. kez çakıştı: {shown}. {tail}")
    if carried and left:
        guidance = (f"Bu görevin işi bitti ama aynı anda çalışan başka görevlerin değişiklikleriyle çakıştı: "
                    f"{', '.join(left[:8])}. Klasörün koşunun son hâline getirildi ve senin önceki değişikliklerin "
                    "geri kondu; çakışan yerlerde git çakışma işaretleri (<<<<<<< / >>>>>>>) var. Her iki tarafın "
                    "amacını koruyarak çakışmaları çöz, işaretleri kaldır, testleri yeniden çalıştır.")
    elif carried:
        guidance = ("Bu görevin işi, aynı anda biten görevlerin değişiklikleriyle birlikte koşunun son hâline "
                    "çakışmasız taşındı. İkisinin birlikte çalıştığını doğrula, gerekiyorsa uyumla ve testleri "
                    "yeniden çalıştır.")
    else:
        guidance = ("Bu görevin önceki işi aynı anda biten görevlerle çakıştı ve koşunun son hâline taşınamadı. Görevi "
                    "koşunun son hâli üzerinde yeniden yap.")
    rec.update(outcome="merge_conflict", counted=False, problem=shorten(f"Çakışan dosyalar: {shown}", 400),
               files=files[:20])
    task.setdefault("attempts", []).append(rec)
    task["attempt_count"] = max(0, int(task.get("attempt_count", 1)) - 1)
    task.update(status="ready", agent=None, keep_changes=carried, guidance=guidance,
                forced={"agent": agent, "effort": rec.get("effort")})
    run.save()
    run.emit("task.update", task_event(task))
    run.emit("agent.state", {"agent": agent, "state": "idle", "task_id": None, "phase": "worker", "text": "",
                             "until": None})
    run.term.warn(f"{task['id']}: iş aynı anda biten görevlerle çakıştı ({shown}); {context.display(agent)} "
                  "çakışmayı çözecek (deneme hakkı eksilmedi).")
    return None


@contextmanager
def _verify_slot(run, parallel: bool):
    """Paralel görevlerin doğrulama komutları sırayla çalışır. Sırayı beklerken durdurma isteğine bakılır."""
    if not parallel:
        yield
        return
    while not _VERIFY_LOCK.acquire(timeout=0.2):
        if run.cancel.is_set():
            raise KeyboardInterrupt
    try:
        yield
    finally:
        _VERIFY_LOCK.release()


def _run_check(run, cmd: str, cwd: Path):
    r = run_command(cmd, cwd, run.cfg.verify_timeout_s(scale_level(run.state)), script_dir=run.rdir / "dogrulama",
                    cancel=run.cancel)
    if r.cancelled:
        raise KeyboardInterrupt
    return r


def _guard_hits(run, task: dict, mark: int) -> list[dict]:
    """Denemeden bu yana koruma kayıtları. Paralel görevde yalnızca bu görevin ajanınınkiler (kayıtta görev kimliği
    var); aynı anda çalışan başka görevin girişimi bu göreve yazılmaz."""
    hits = read_jsonl(run.rdir / "guard.jsonl")[mark:]
    if task.get("lane"):
        hits = [h for h in hits if h.get("gorev") == task["id"]]
    return hits


def _has_markers(p: Path) -> bool:
    try:
        return bool(_CONFLICT_MARK.search(p.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


# --- Jev'in kabul sorusu (tek görevli iş) ---------------------------------------------------

def wants_acceptance(run, task: dict) -> bool:
    """Doğrulamadan geçen tek görevli işe Jev son bir kez bakar: isteğin alışılmış biçimi mi? Görev başına bir kez;
    düzeltme turunda (kullanıcının notu belirleyici) ve Jev modeli kapalıyken sorulmaz."""
    jev = getattr(run, "jev", None)
    return bool(task.get("verify_from_worker") and task.get("origin") == "plan" and not task.get("acceptance_retry")
                and jev is not None and jev.enabled)


def _samples(project: Path, files: list[str]) -> dict[str, str]:
    """Kabul sorusu için başlıca dosyaların başı (test dosyaları hariç; önce HTML, sonra kod ve belge)."""
    main = [f for f in files if f.lower().endswith(SAMPLE_EXT) and "test" not in f.lower().rsplit("/", 1)[-1]]
    main.sort(key=lambda f: next(i for i, e in enumerate(SAMPLE_EXT) if f.lower().endswith(e)))
    out: dict[str, str] = {}
    for f in main[:SAMPLE_FILES]:
        try:
            out[f] = (project / f).read_text(encoding="utf-8", errors="replace")[:SAMPLE_CHARS]
        except OSError:
            continue
    return out


def acceptance(run, task: dict, result: dict, rows: list[dict], cp: str) -> str | None:
    """Jev modeli işi kabul ediyor mu (~1 sn). Güvenle 'hayır' derse sorunu döndürür; değilse None. Çağrı
    başarısızsa iş kabul edilir (doğrulama zaten geçti)."""
    from .. import systemone
    st, jev = run.state, run.jev
    wd = lanes.workdir(run, task)
    files = gitops.changes_since(wd, cp)["all"]
    state = {
        "rol": "Jev, çok ajanlı bir yazılım ofisinin ustabaşısı. Biten işi kullanıcıya teslim etmeden önce son kez "
               "bakar: iş, isteği kullanıcının beklediği ALIŞILMIŞ biçimde, eksiksiz ve özenle karşılıyor mu?",
        "istek": st["request"], "isci_secimleri": context.note_lines(str(result.get("notes") or ""), "secim") or None,
        "isci_ozeti": result.get("summary") or "", "isci_notlari": shorten(str(result.get("notes") or ""), 600),
        "dosyalar": files[:40], "dogrulama": [{"komut": r.get("command"), "gecti": r.get("passed")} for r in rows],
        "dosya_ornekleri": _samples(wd, files),
    }
    q = {"kabul": systemone.noul(
             "Bu iş, isteği kullanıcının beklediği alışılmış biçimde, eksiksiz ve özenle karşılıyor mu? (ör. hesap "
             "makinesi isteğine tuş takımlı bir arayüz beklenir, form değil; video isteğine .mp4 dosyası beklenir; "
             "harita gerçek coğrafi veriden çizilir, elle değil)",
             "Evet: alışılmış biçimde, eksiksiz ve özenli",
             "Hayır: biçim alışılmışın dışında, önemli bir parça eksik ya da kaba bir taklit"),
         "sorun": systemone.choice("En önemli sorun hangisi?", {
             "bicim": "Biçim alışılmışın dışında (kullanıcı başka bir biçim bekler)",
             "eksik": "İsteğin önemli bir parçası eksik", "calismaz": "Çalışır görünmüyor ya da doğrulama yüzeysel",
             "kalite": "Kalite zayıf: gerçek veri ya da kaynak yerine elle kaba taklit (ör. elle çizilmiş harita), "
                       "özensiz görünüm",
             "yok": "Belirgin bir sorun yok"})}
    hint = None
    if st.get("dry_run"):  # senaryodaki "kabul": true/false ya da sorun türü (ör. "kalite")
        k = sizing.scenario(run).get("kabul", True)
        ok = bool(k) and not isinstance(k, str)
        hint = {"kabul": ok, "sorun": "yok" if ok else (k if isinstance(k, str) else "bicim")}
    run.emit("agent.state", {"agent": "jev", "state": "verifying", "task_id": task["id"], "phase": "executing",
                             "text": f"{task['id']}: isteğe uygun mu bakıyor", "until": None})
    res = jev.ask("kabul", state, q, task_id=task["id"], hint=hint)
    if not res.ok:
        run.log("info", f"{task['id']}: Jev modeli kabul sorusunu yanıtlayamadı ({shorten(res.error_text, 160)}); "
                        "doğrulama geçtiği için iş kabul edildi.")
        return None
    a = res.answers["kabul"]
    kind = res.answers["sorun"].value if "sorun" in res.answers else None
    task["kabul"] = {"p": round(float(a.value), 3), "guven": round(float(a.confidence), 3), "sorun": kind}
    if float(a.value) >= 0.5 or a.confidence < jev.threshold:
        return None
    kind = kind if kind in PROBLEM_TR and kind != "yok" else "bicim"
    return f"{PROBLEM_TR[kind]} (Jev %{round((1 - float(a.value)) * 100)} emin)"


def _not_accepted(run, task: dict, agent: str, rec: dict, why: str) -> None:
    """Doğrulamadan geçti ama Jev işi isteğin alışılmış biçimi ya da özenli bir sonuç olarak görmüyor: beyne gitmeden
    aynı ajan, değişiklikleri koruyarak bir kez daha dener (form yerine tuş takımı, elle çizilmiş harita yerine
    gerçek veri gibi sorunlar burada ucuza düzelir)."""
    rec.update(outcome="not_accepted", problem=why)
    task.setdefault("attempts", []).append(rec)
    guidance = (f"Jev'in kabul kontrolü: işin doğrulaması geçti ama iş isteği beklendiği gibi karşılamıyor — {why}. "
                "Önceki denemenin dosyaları klasörde duruyor. İşi isteğin herkesin beklediği ALIŞILMIŞ biçimine getir "
                "(ör. hesap makinesi → tuş takımlı arayüz, form değil), kaba taklitleri gerçek kaynakla yeniden yap "
                "(KALİTE kuralı), eksikleri tamamla, testleri güncelleyip yeniden çalıştır.")
    task.update(status="ready", agent=None, keep_changes=True, acceptance_retry=True, guidance=guidance,
                forced={"agent": agent, "effort": rec.get("effort")})
    run.save()
    run.emit("task.update", task_event(task))
    run.emit("agent.state", {"agent": agent, "state": "idle", "task_id": None, "phase": "worker", "text": "",
                             "until": None})
    run.term.warn(f"{task['id']}: doğrulama geçti ama Jev kabul etmedi ({why}); "
                  f"{context.display(agent)} bir kez daha deniyor.")


def _succeed(run, task: dict, agent: str, cp: str, result: dict, rec: dict, full_ok) -> None:
    """Görevi bitirir ve commit eder. Paralel görevde şerit koşu dalına tek commit olarak birleşir; birleştirme
    çakışırsa (gitops.MergeConflict) görev durumuna dokunulmadan hata yükselir."""
    project, st = run.project, run.state
    msg = _commit_message(task, agent)
    lane = task.get("lane")
    if lane:
        path = Path(lane["path"])
        changes = gitops.changes_since(path, cp)
        gitops.commit_all(path, msg)
        save_main(run)  # birleşik test sürerken proje klasöründe değişiklik yapılmış olabilir
        sha = gitops.squash_merge(project, lane["branch"], msg)
        lanes.close_lane(run, task)
    else:
        changes = gitops.changes_since(project, cp)
        sha = gitops.commit_all(project, msg)
    task.pop("conflict_files", None)
    if full_ok:
        st["full_test_ok"] = True
    summary = result.get("summary") or ""
    rec.update(outcome="done", commit=sha)
    task.setdefault("attempts", []).append(rec)
    task.update(status="done", commit=sha, summary=summary, files=changes["all"], forced=None, guidance="",
                finished=iso(), worker_notes=shorten(str(result.get("notes") or ""), 2000))
    context.append_progress(run.rdir, task, agent, changes["all"], summary)
    run.save()
    run.emit("task.update", task_event(task))
    run.emit("agent.state", {"agent": agent, "state": "done", "task_id": task["id"], "phase": "worker",
                             "text": f"{task['id']} bitti!", "until": None})
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": "executing", "text": "",
                             "until": None})
    done, total = routing.progress(st)
    run.term.ok(f"{task['id']} bitti ({context.display(agent)}) · {len(changes['all'])} dosya · "
                f"commit {sha[:8] if sha else '-'} · ilerleme {done}/{total}")


# --- doğrulama ---------------------------------------------------------------------------

def _rel(project: Path, p: str) -> str:
    s = str(p or "").strip().strip('"').strip("'")
    try:
        pp = Path(s)
        if pp.is_absolute():
            s = str(pp.resolve().relative_to(project.resolve()))
    except (ValueError, OSError):
        pass
    s = s.replace("\\", "/")
    return s[2:] if s.startswith("./") else s


def verify_task(run, task: dict, agent: str, cp: str, result: dict, guard_mark: int):
    """(sorunlar, doğrulama satırları, tam test geçti mi, notlar). Sorun: (sonuç kodu, açıklama). Görevin kendi
    klasöründe çalışır (paralel görevde şeridi); paralel görevlerin komutları sırayla çalışır."""
    cfg, st = run.cfg, run.state
    project, branch = lanes.workdir(run, task), lanes.branch(run, task)
    parallel = bool(task.get("lane"))
    task["status"] = "verifying"
    run.save()
    run.emit("task.update", task_event(task))
    run.emit("agent.state", {"agent": agent, "state": "idle", "task_id": task["id"], "phase": "worker",
                             "text": "Jev'in doğrulamasını bekliyor", "until": None})
    run.emit("agent.state", {"agent": "jev", "state": "verifying", "task_id": task["id"], "phase": "executing",
                             "text": f"{task['id']} doğrulanıyor", "until": None})
    problems: list[tuple[str, str]] = []
    rows: list[dict] = []
    notes: list[str] = []

    # 0) git denetimi: dal ve geçmiş
    cur = gitops.current_branch(project)
    if branch and cur != branch:
        problems.append(("audit", f"Ajan dalı değiştirdi ({branch} → {cur or 'ayrık HEAD'}). Git durumunu "
                                  "değiştiren komutlar yasak."))
        gitops.rollback(project, cp, branch)
        return problems, rows, None, notes
    head = gitops.head(project)
    if head != cp:
        is_desc = gitops.git(["merge-base", "--is-ancestor", cp, head], project, check=False).returncode == 0
        if is_desc:
            gitops.git(["reset", "-q", "--soft", cp], project)
            notes.append("Ajan kendisi commit attı; Jev bu commit'leri görev commit'inde birleştirdi.")
        else:
            problems.append(("audit", "Ajan git geçmişini değiştirdi (checkpoint artık HEAD'in atası değil)."))
            gitops.rollback(project, cp, branch)
            return problems, rows, None, notes

    def run_one(cmd: str, source: str) -> dict:
        r = _run_check(run, cmd, project)
        row = {**r.to_dict(60), "source": source}
        rows.append(row)
        run.emit("verify", {"task_id": task["id"], "command": cmd, "passed": r.passed, "exit_code": r.exit_code,
                            "source": source})
        return {"row": row, "res": r}

    full_ok = None
    with _verify_slot(run, parallel):
        refresh_path()  # işçi winget ile araç kurduysa Jev onu bulsun
        # 1) görevin verify komutları. Tek görevli işte (plan yok) komutları işçi verir; Jev onları proje kökünde
        #    yeniden çalıştırıp sonucu kendisi ölçer
        source = "gorev"
        if task.get("verify_from_worker"):
            source = "isci"
            adopted, skipped = worker_checks(result, project,
                                             protection=bool(run.cfg.get("guvenlik", "koruma", default=False)))
            task["verify"] = adopted
            if skipped:
                notes.append("Yeniden çalıştırılmayan işçi komutları (pencere ya da sunucu açar, ya da koruma "
                             "kuralına takılır): " + "; ".join(shorten(c, 80) for c in skipped[:5]))
            if not adopted:
                problems.append(("verify_failed", "İşçi kendiliğinden biten bir doğrulama komutu vermedi; Jev işi "
                                                  "doğrulayamadı. Proje kökünden çalışan, pencere ya da sunucu "
                                                  "açmayan en az bir kontrol komutu gerekli."))
        for cmd in task.get("verify") or []:
            if not str(cmd).strip():
                continue
            x = run_one(cmd, source)
            if not x["res"].passed:
                problems.append(("verify_failed", f"`{cmd}` kaldı (çıkış kodu {x['res'].exit_code}):\n"
                                                  + x["res"].tail(150)))
        # 2) tam test (gerileme denetimi)
        test_cmd = ((run.plan.get("commands") or {}).get("test") or "").strip()
        listed = {str(c).strip() for c in task.get("verify") or []}
        if cfg.get("her_gorevde_tam_test", default=True) and test_cmd:
            if test_cmd in listed:
                full_ok = all(r["passed"] for r in rows if r["command"].strip() == test_cmd)
            else:
                x = run_one(test_cmd, "tam_test")
                full_ok = x["res"].passed
                if not full_ok:
                    if st.get("full_test_ok"):
                        problems.append(("verify_failed", f"Tam test (`{test_cmd}`) önceki görevlerden sonra "
                                                          "geçiyordu, bu değişiklikten sonra kalıyor (gerileme):\n"
                                         + x["res"].tail(150)))
                    else:
                        x["row"]["note"] = "tam test daha önce de geçmiyordu; gerileme sayılmadı"
                        notes.append("Tam test geçmiyor (daha önce de geçmiyordu; gerileme sayılmadı).")
    # 3) beklenen dosyalar
    missing = [o for o in task.get("outputs") or [] if o and not (project / o).exists()]
    if missing:
        problems.append(("missing_outputs", "Beklenen dosyalar yok: " + ", ".join(missing)))
    # 4) diff
    changes = gitops.changes_since(project, cp)
    if task.get("type") in CODE_TYPES and not changes["all"]:
        problems.append(("no_diff", "Kod/test görevi ama hiçbir dosya değişmedi."))
    # 5) denetim: beklenmedik silmeler
    reported = {_rel(project, p) for p in result.get("changed_files") or []}
    owned = set()
    for t in list(st["tasks"]):
        if t is not task and t.get("status") == "done":
            owned.update(t.get("files") or [])
            owned.update(t.get("outputs") or [])
    unexpected = [d for d in changes["deleted"] if d in PROTECTED_FILES or (d in owned and d not in reported)]
    if unexpected:
        problems.append(("audit", "Beklenmedik şekilde silinen izlenen dosyalar: " + ", ".join(unexpected[:20])))
    other_del = [d for d in changes["deleted"] if d not in unexpected]
    if other_del:
        notes.append("Silinen dosyalar: " + ", ".join(other_del[:20]))
    # 6) aynı anda biten görevlerle çakışmadan kalan işaretler
    marked = [f for f in task.get("conflict_files") or [] if _has_markers(project / f)]
    if marked:
        problems.append(("verify_failed", "Çakışma işaretleri (<<<<<<< / >>>>>>>) duruyor: " + ", ".join(marked[:10])
                         + ". Çakışmaları çözüp işaretleri kaldırmak gerekli."))
    # 7) koruma kayıtları
    hits = _guard_hits(run, task, guard_mark)
    serious = [h for h in hits if h.get("serious")]
    if serious:
        problems.append(("guard", "Koruma ciddi bir girişimi engelledi: " + "; ".join(
            f"{h.get('category_tr', h.get('category'))}: {h.get('reason')}" for h in serious[:5])))
    elif hits:
        notes.append(f"Koruma {len(hits)} girişimi engelledi (ciddi değil): "
                     + "; ".join(shorten(str(h.get("reason")), 120) for h in hits[:3]))
    return problems, rows, full_ok, notes


def worker_checks(result: dict, project: Path, *, protection: bool = False) -> tuple[list[str], list[str]]:
    """İşçinin çalıştırdığı doğrulama komutlarından Jev'in yeniden çalıştıracakları (en fazla MAX_ADOPTED) ve
    atlananlar: pencere ya da sunucu açıp bekleyenler alınmaz. Koruma açıksa kuralları da uygulanır."""
    taken: list[str] = []
    skipped: list[str] = []
    for v in result.get("verification") or []:
        cmd = str((v.get("command") if isinstance(v, dict) else v) or "").strip()
        if not cmd or cmd in taken or cmd in skipped:
            continue
        if _OPENS.search(cmd) or (protection and guard.check_command(cmd, project)):
            skipped.append(cmd)
        elif len(taken) < MAX_ADOPTED:
            taken.append(cmd)
    return taken, skipped


def _first_line(text: str, n: int = 140) -> str:
    """Sorun metninin terminal için ilk satırı (sondaki iki nokta atılır; ayrıntı günlükte)."""
    lines = (text or "").strip().splitlines()
    return shorten(lines[0].rstrip().rstrip(":"), n) if lines else ""
