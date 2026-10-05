"""Jev'in kural katmanı: hazır görev, görev sırası ve ajan seçimi (belirtim §5.6). Model çağırmaz."""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable

from .dag import COMPLEXITY_ORDER, dependents_count
from .state import scale_level

OPEN_STATUSES = {"pending", "ready", "waiting_quota"}


@dataclass
class Choice:
    agent: str | None
    effort: str = ""
    reason: str = ""
    wait_until: _dt.datetime | None = None


def by_id(state: dict) -> dict[str, dict]:
    return {t["id"]: t for t in state.get("tasks", [])}


def dep_status(task: dict, tasks: dict[str, dict]) -> str:
    """"ok" | "wait" | "broken". Atlanan bağımlılık, beyin atlamaya karar verdiği için geçer sayılır;
    başarısız ya da engellenmiş bağımlılık görevi engeller (diğer bağımlılıklar sürüyor olsa da)."""
    result = "ok"
    for d in task.get("depends_on") or []:
        dt = tasks.get(d)
        if dt is None:
            continue  # bölünmüş görevlerin bağımlılıkları yeniden yazılır; bilinmeyen kimlik görmezden gelinir
        st = dt.get("status")
        if st in ("failed", "blocked"):
            return "broken"
        if st not in ("done", "skipped"):
            result = "wait"
    return result


def refresh(state: dict) -> list[tuple[str, str]]:
    """pending → ready ve pending/ready → blocked geçişlerini yapar. Değişen (kimlik, yeni durum) listesi."""
    tasks = by_id(state)
    changed = []
    for t in state.get("tasks", []):
        if t.get("status") not in OPEN_STATUSES:
            continue
        ds = dep_status(t, tasks)
        if ds == "broken":
            t["status"] = "blocked"
            t["blocked_reason"] = "Bağımlı olduğu görev başarısız oldu ya da engellendi."
            changed.append((t["id"], "blocked"))
        elif ds == "ok" and t["status"] == "pending":
            t["status"] = "ready"
            changed.append((t["id"], "ready"))
        elif ds == "wait" and t["status"] == "ready":
            t["status"] = "pending"
            changed.append((t["id"], "pending"))
    return changed


def ordered_ready(state: dict) -> list[dict]:
    """Önce kritik yol (geçişli olarak en çok görevi bekleten), sonra düşük karmaşıklık, sonra liste sırası."""
    tasks = state.get("tasks", [])
    weight = dependents_count(tasks)
    order = {t["id"]: i for i, t in enumerate(tasks)}
    ready = [t for t in tasks if t.get("status") in ("ready", "waiting_quota")]
    return sorted(ready, key=lambda t: (-weight.get(t["id"], 0), COMPLEXITY_ORDER.get(t.get("complexity"), 1),
                                        order[t["id"]]))


def candidates(task: dict, cfg) -> list[str]:
    """Tek görevli işin aday havuzu (zorluğa göre; varsa) + planın önerisi + tür listesi (varsa) ya da karmaşıklık
    listesi. Yalnızca işçi rolündeki ajanlar: eskalasyon ajanı (Opus) görevi yalnızca Jev'in kararıyla alır."""
    out: list[str] = list(task.get("aday_havuzu") or [])
    sug = task.get("suggested_agent")
    if sug:
        out.append(sug)
    by_type = cfg.get("yonlendirme", "tur", default={}) or {}
    lst = by_type.get(task.get("type")) or cfg.get("yonlendirme", task.get("complexity") or "M", default=[]) or []
    out += list(lst)
    seen, res = set(), []
    size = task.get("complexity") or "M"
    for a in out:
        if a in seen or a not in cfg.agents or not cfg.has_role(a, "isci") or not cfg.takes(a, size):
            continue
        seen.add(a)
        res.append(a)
    return res


def choose_agent(task: dict, state: dict, cfg, available: Callable[[str], bool],
                 until: Callable[[str], _dt.datetime | None],
                 picker: Callable[[dict, list[str], str, str], tuple[str, str] | None] | None = None) -> Choice:
    """`picker(task, free, rule_pick, rule_reason)`: Jev modelinin seçimi (ajan, gerekçe) ya da None (kural kalır)."""
    comp = task.get("complexity") or "M"
    forced = task.get("forced") or {}
    fa = forced.get("agent")
    refused = set(task.get("ret_veren") or [])  # bu görevi güvenlik gerekçesiyle reddeden: bu görevi yeniden almaz
    if fa and not (fa in cfg.agents and (cfg.has_role(fa, "isci") or cfg.has_role(fa, "eskalasyon")))             or fa in refused:
        fa = None  # işçi olamayan ajan (ör. elle bozulmuş durumda yalnızca denetçi olan) zorlanamaz; seçim kurala kalır
    cap = cfg.worker_cap(scale_level(state), comp)  # ölçek tavanı yalnızca kuralın eforunu bağlar
    if fa and available(fa):
        return Choice(fa, forced.get("effort") or cfg.effort(fa, comp, cap), "Jev'in kararı")
    failed = set(task.get("failed_agents") or [])
    cands = candidates(task, cfg)
    usable = [a for a in cands if (a not in failed or a == fa) and a not in refused]
    others = [a for a in cfg.workers() if cfg.takes(a, comp) and a not in failed and a not in refused]
    if refused & set(cands):  # reddedenin yeri boş kalmasın: listenin sonuna diğer işçiler
        usable += [a for a in others if a not in usable]
    widened = False
    if not usable:  # listedeki herkes bu görevde başarısız oldu: diğer işçilere aç
        usable = others
        widened = True
    free = [a for a in usable if available(a)]
    pool = [a for a in free if a in (task.get("aday_havuzu") or [])]
    if pool:  # havuzdan müsait biri varsa seçim havuzda kalır; diğer işçiler yalnızca havuz soğumadayken
        free = pool
    if not free:
        waits = [u for u in (until(a) for a in usable) if u is not None]
        if fa and not available(fa) and until(fa):
            waits.append(until(fa))
        return Choice(None, reason="Uygun ajanların hepsi soğumada.", wait_until=min(waits) if waits else None)
    pick = free[0]
    if fa and fa != pick:
        reason = f"Jev'in seçtiği {fa} soğumada; sıradaki uygun ajan"
    elif pick in pool:
        reason = "zorluğun aday havuzunda ilk müsait ajan"
    elif task.get("suggested_agent") == pick:
        reason = "planın önerisi"
    elif widened:
        reason = "Listedeki ajanlar bu görevde başarısız oldu; başka bir işçi"
    else:
        reason = f"{'tür' if (cfg.get('yonlendirme', 'tur', default={}) or {}).get(task.get('type')) else comp} listesinde ilk müsait ajan"
    # sağlayıcı dengesi
    limit = int(cfg.get("yonlendirme", "saglayici_dengesi", default=0) or 0)
    streak = state.get("provider_streak") or {}
    if (limit > 0 and streak.get("provider") == cfg.provider(pick)
            and int(streak.get("count", 0)) >= limit):
        alt = next((a for a in free if cfg.provider(a) != cfg.provider(pick)), None)
        if alt:
            reason = f"sağlayıcı dengesi ({cfg.provider(pick)} art arda {streak.get('count')} görev aldı)"
            pick = alt
    # Jev modeli: seçim birden çok müsait ajan arasındaysa kararı Jev verir; kuralın seçimi ona öneri olarak gider
    if picker is not None and not fa and len(free) >= 2:
        got = picker(task, list(free), pick, reason)
        if got and got[0] in free:
            pick, reason = got
    effort = forced.get("effort") if forced.get("effort") and not fa else cfg.effort(pick, comp, cap)
    return Choice(pick, effort, reason)


def note_dispatch(state: dict, provider: str) -> None:
    s = state.setdefault("provider_streak", {"provider": None, "count": 0})
    if s.get("provider") == provider:
        s["count"] = int(s.get("count", 0)) + 1
    else:
        s["provider"], s["count"] = provider, 1


def progress(state: dict) -> tuple[int, int]:
    """(biten, toplam). Bölünen görevler sayılmaz."""
    tasks = [t for t in state.get("tasks", []) if t.get("status") != "split"]
    done = sum(1 for t in tasks if t.get("status") in ("done", "skipped", "failed", "blocked"))
    return done, len(tasks)
