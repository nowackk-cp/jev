"""[3] UYGULAMA, paralel: birbirini beklemeyen görevler aynı anda, her biri kendi git çalışma ağacında (şerit).

Ana iş parçacığı dağıtır ve sonuçları sırayla uygular (birleştirme, beynin kararı, durum); ajan çağrısı ve görevin
kendi doğrulaması görevin iş parçacığında çalışır (execute.work). Bir ajan aynı anda en fazla
`ajanlar.<ad>.es_zamanli` görev alır.

Duraklatma (çıkış komutu, beynin duraklatma kararı, oturum hatası) yeni görev başlatmaz ve çalışan görevlerin
bitmesini bekler. Ctrl+C, iptal ya da beklenmedik hata çalışan ajanları durdurur; yarım işleri runner geri alır.
"""
from __future__ import annotations

import threading
import time

from .. import gitops, lanes, routing
from ..state import STATUS_TR, TERMINAL_TASK
from ..util import shorten
from . import execute
from .common import RunPaused, task_event, wait_or_pause

TICK_S = 0.2  # çalışan görevler varken dağıtıcının bakma aralığı
STOP_WAIT_S = 30  # durdurmada ajanların kapanması için beklenen en uzun süre


class _NoLanes(Exception):
    """Görev şeridi açılamadı: koşunun geri kalanı sırayla çalışır."""


def run_parallel(run, limit: int) -> bool:
    """Görevleri en fazla `limit` tanesi aynı anda olacak şekilde çalıştırır. Bütün görevler bittiğinde ya da aynı anda
    başlayabilecek görev kalmadığında (ör. sıradaki görevler zincir) döner. Dönüş: şeritler kullanılabildi mi (False:
    şerit açılamadı; görevler sırayla sürer)."""
    st = run.state
    _prepare(run)
    run.term.info(f"Aynı anda en fazla {limit} görev; her görev kendi çalışma kopyasında "
                  f"({gitops.worktree_root(run.project)})")
    pool: list[execute.Job] = []
    stop: RunPaused | None = None
    fallback = False
    try:
        while True:
            try:
                run.poll_commands()
            except RunPaused as e:
                if not pool or stop is not None:
                    raise  # ikinci çıkış: çalışan ajanlar durdurulur, yarım işleri geri alınır
                stop = e
                run.term.info("Çıkış istendi; çalışan görevler bitince koşu duraklatılacak (hemen durdurmak için "
                              "yeniden `çıkış`).")
            stop = _collect(run, pool, stop)
            for tid, status in routing.refresh(st):
                t = routing.by_id(st)[tid]
                run.emit("task.update", task_event(t))
                if status == "blocked":
                    run.term.warn(f"{tid} engellendi: {t.get('blocked_reason', '')}")
            run.save()
            open_tasks = [t for t in st["tasks"] if t.get("status") not in TERMINAL_TASK]
            if not pool:
                if stop is not None:
                    raise stop
                if not open_tasks or fallback or not execute.wants_lanes(st, limit):
                    break
            elif stop is not None or fallback:
                time.sleep(TICK_S)
                continue
            try:
                started, waits = _dispatch(run, pool, limit)
            except _NoLanes:
                fallback = True
                continue
            if pool:
                if not started:
                    time.sleep(TICK_S)
                continue
            if not [t for t in open_tasks if t.get("status") in ("ready", "waiting_quota")]:
                raise RunPaused("Görev grafiği ilerleyemiyor: " + ", ".join(
                    f"{t['id']}[{STATUS_TR.get(t.get('status'), t.get('status'))}]" for t in open_tasks))
            wait_or_pause(run, min(waits) if waits else None, "uygun işçilerin hepsi")
    except BaseException:
        _stop(run, pool)
        raise
    lanes.sweep(run)
    return not fallback


def _prepare(run) -> None:
    """Şeritler koşu dalının commit'lerinden açılır: proje klasöründe korunan yarım iş geri alınır, kaydedilmemiş
    değişiklikler commit edilir."""
    for t in run.state["tasks"]:
        if t.get("status") not in TERMINAL_TASK and t.get("keep_changes") and not t.get("lane"):
            lanes.rollback(run, t)
            run.log("warn", f"{t['id']} görevinin korunan yarım değişiklikleri geri alındı (görevler aynı anda "
                            "çalışacak).")
    execute.save_main(run, "görevler aynı anda başlamadan önce")


def _dispatch(run, pool: list, limit: int) -> tuple[int, list]:
    """Boş yer oldukça hazır görevleri başlatır. Dönüş: (başlayan görev sayısı, kota bekleyen görevlerin açılma
    zamanları)."""
    st = run.state
    load: dict[str, int] = {}
    for j in pool:
        load[j.agent] = load.get(j.agent, 0) + 1
    busy = {a for a, n in load.items() if n >= run.cfg.slots(a)}
    taken = {o for j in pool for o in (j.task.get("outputs") or [])}
    started, waits = 0, []
    for t in routing.ordered_ready(st):
        if len(pool) >= limit:
            break
        outs = set(t.get("outputs") or [])
        if outs & taken:  # aynı dosyayı yazacak iki görev aynı anda çalışmaz (çakışma kesin)
            continue
        ch = _choose(run, t, busy)
        if ch is None:
            continue
        if not ch.agent:
            if ch.wait_until:
                waits.append(ch.wait_until)
            execute.mark_waiting(run, t, ch)
            continue
        try:
            job = execute.begin(run, t, ch, parallel=True)
        except (gitops.GitError, OSError) as e:
            run.term.warn(f"{t['id']}: görev şeridi açılamadı ({shorten(str(e), 200)}); görevler sırayla çalışacak.")
            lanes.close_lane(run, t)
            t.update(status="ready", agent=None)
            run.save()
            run.emit("task.update", task_event(t))
            raise _NoLanes from e
        job.thread = threading.Thread(target=_work, args=(run, job), name=f"jev-{t['id']}", daemon=True)
        job.thread.start()
        pool.append(job)
        load[job.agent] = load.get(job.agent, 0) + 1
        if load[job.agent] >= run.cfg.slots(job.agent):
            busy.add(job.agent)
        taken |= outs
        started += 1
    return started, waits


def _choose(run, task: dict, busy: set):
    """Görevin ajanı; `busy` (eş zamanlı görev sınırı dolmuş ajanlar) seçilmez. None: uygun ajan şu an başka bir görevde (biri boşalınca
    yeniden bakılır). Ajansız seçim: uygun ajanların hepsi soğumada (görev kotayı bekler)."""
    st, cfg, quota = run.state, run.cfg, run.quota
    fa = (task.get("forced") or {}).get("agent")
    if fa and fa in busy and quota.available(fa):
        return None  # Jev'in seçtiği ajan başka bir görevde: bitince bu göreve geçer
    ch = routing.choose_agent(task, st, cfg, lambda a: a not in busy and quota.available(a), quota.until,
                              picker=execute._jev_picker(run))
    if ch.agent or not busy:
        return ch
    if routing.choose_agent(task, st, cfg, quota.available, quota.until).agent:
        return None
    return ch


def _work(run, job) -> None:
    try:
        execute.work(run, job)
    except BaseException as e:  # ana iş parçacığı sonucu toplarken yeniden yükseltir
        job.error = e


def _collect(run, pool: list, stop: RunPaused | None) -> RunPaused | None:
    """Biten görevlerin sonucunu ana iş parçacığında uygular. Duraklatma isteği ilk gelenle tutulur; çalışan görevler
    bitince yükselir."""
    for job in [j for j in pool if not j.thread.is_alive()]:
        pool.remove(job)
        if job.error is not None:
            raise job.error
        try:
            execute.conclude(run, job)
        except RunPaused as e:
            if stop is None and pool:
                run.term.info("Koşu, çalışan görevler bitince duraklatılacak.")
            stop = stop or e
    return stop


def _stop(run, pool: list) -> None:
    """Çalışan ajanları durdurur ve iş parçacıklarının bitmesini bekler; yarım işleri runner geri alır."""
    if not pool:
        return
    run.cancel.set()
    end = time.monotonic() + STOP_WAIT_S
    for job in pool:
        job.thread.join(max(0.0, end - time.monotonic()))
    alive = [j.task["id"] for j in pool if j.thread.is_alive()]
    if alive:
        run.log("warn", "Şu görevlerin ajanı zamanında durmadı: " + ", ".join(alive))
