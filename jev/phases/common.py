"""Aşamaların ortak yardımcıları: sabit rollü çağrılar (planlayıcı, denetçi), kota beklemesi, görev olayları."""
from __future__ import annotations

import datetime as _dt
import time

from ..adapters.base import AgentResult
from ..util import ek, iso, local_hhmm, now


class RunPaused(Exception):
    """Koşu kaydedilip duraklatılır; `jev devam` kaldığı aşamadan sürdürür.

    kind: kota | oturum | karar | beyin_siniri | kullanici | hata (arayüz ve `devam` davranışı için)
    """

    def __init__(self, reason: str, until: _dt.datetime | None = None, kind: str = "hata"):
        super().__init__(reason)
        self.reason = reason
        self.until = until
        self.kind = kind


class RunAborted(Exception):
    pass


class Unavailable(Exception):
    """İsteğe bağlı sabit çağrı yapılamadı (kota, oturum ya da iki başarısız deneme): çağıran asıl ajana döner."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Refused(Exception):
    """Sabit çağrıyı model güvenlik gerekçesiyle yanıtlamadı; yeniden denenmez. Çağıran ret yedeğini (ret_takasi) ya da başka
    ajanı seçer."""

    def __init__(self, agent: str, text: str = ""):
        super().__init__(f"{agent}: güvenlik reddi")
        self.agent = agent
        self.text = text


def refusal_partner(run, agent: str, phase: str, text: str = "", task_id: str | None = None) -> str | None:
    """`agent` bir çağrıyı (yedek modeliyle de) güvenlik gerekçesiyle yanıtlamadı: yalnızca bu çağrıyı alacak ajan
    ([jev] ret_takasi, varsayılan Sol) ya da None. Reddeden koşudan çıkarılmaz; ret rapora yazılır."""
    from ..context import display
    from ..util import ek
    partner = run.cfg.swap_partner
    if not partner or partner == agent:
        return None
    run.state.setdefault("retler", []).append({"agent": agent, "asama": phase, "yerine": partner, "gorev": task_id,
                                               "neden": (text or "").strip()[:300]})
    run.log("warn", f"{display(agent)} {phase} çağrısını güvenlik gerekçesiyle yanıtlamadı; bu iş "
                    f"{ek(display(partner), 'e')} geçti (sonraki işlerde yine kendisi çalışır).")
    return partner


def refusal_pause(run, agent: str, phase: str, text: str = "") -> RunPaused:
    from ..context import display
    return RunPaused(f"{display(agent)} {phase} aşamasında isteği güvenlik gerekçesiyle yanıtlamadı ve yerine geçecek "
                     f"ajan yok. İsteği netleştirip yeniden başlat ya da `jev devam` yaz. ({text[:200]})", kind="hata")


RUNTIME_DEFAULTS = {
    "status": "pending", "agent": None, "attempts": [], "attempt_count": 0, "failed_agents": [],
    "forced": None, "guidance": "", "checkpoint": None, "commit": None, "summary": "", "files": [],
    "decisions": [], "origin": "plan", "round": 1, "waiting_until": None, "simple_rule_used": False,
}


def init_task(t: dict, *, origin: str, round_: int) -> dict:
    task = dict(t)
    for k, v in RUNTIME_DEFAULTS.items():
        task.setdefault(k, list(v) if isinstance(v, list) else v)
    task["origin"] = origin
    task["round"] = round_
    return task


def task_event(task: dict) -> dict:
    return {"task_id": task["id"], "status": task.get("status"), "agent": task.get("agent"),
            "attempt": int(task.get("attempt_count", 0)), "title": task.get("title", ""),
            "complexity": task.get("complexity"), "type": task.get("type"), "module": task.get("module"),
            "depends_on": task.get("depends_on") or [], "round": task.get("round", 1),
            "covers": task.get("covers_criteria") or [], "parallel": bool(task.get("lane"))}


def wait_or_pause(run, until: _dt.datetime | None, who: str) -> None:
    """Sıfırlanma eşik içindeyse geri sayımla bekler; değilse koşuyu duraklatır."""
    threshold = float(run.cfg.get("kota", "bekleme_esigi_dk", default=30) or 30)
    if until is None:
        until = now() + _dt.timedelta(minutes=float(run.cfg.get("kota", "varsayilan_soguma_dk", default=60)))
    left = (until - now()).total_seconds()
    if left > threshold * 60:
        raise RunPaused(f"Kota doldu ({who}); ~{ek(local_hhmm(until), 'den')} sonra `jev devam`", until, kind="kota")
    run.log("warn", f"Kota doldu ({who}); {ek(local_hhmm(until), 'e')} kadar bekleniyor (~{max(1, int(left // 60))} dk).")
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": run.state["phase"],
                             "text": f"Kota bekleniyor: {local_hhmm(until)}", "until": iso(until)})
    run.sleep_until(until)


def call_fixed(run, agent: str, phase: str, prompt: str, *, schema_name: str, effort: str, timeout_key: str,
               extra: dict | None = None, state_text: str | None = None, images: list | None = None,
               tools: str | None = None, optional: bool = False) -> AgentResult:
    """Plan, son kontrol ve düzeltme çağrıları: başka modele verilmez (kural 11).

    Kota: eşik içindeyse beklenir, değilse duraklatılır. Oturum hatası: duraklatılır.
    Diğer hatalarda bir kez daha denenir; yine olmazsa duraklatılır.
    optional (yedek denetçi): beklenmez, duraklatılmaz; kota, oturum ya da ikinci başarısız denemede Unavailable
    yükselir ve çağıran asıl ajana döner.
    Güvenlik reddi yeniden denenmez: Refused yükselir (optional ise Unavailable).
    """
    tries = 0
    while True:
        if not run.quota.available(agent):
            if optional:
                raise Unavailable("soğumada")
            wait_or_pause(run, run.quota.until(agent), agent)
        res = run.gw.call(agent, phase, prompt, schema_name=schema_name, readonly=True, effort=effort,
                          timeout_key=timeout_key, extra=extra, state_text=state_text, images=images, tools=tools)
        if res.ok:
            return res
        if res.error_kind == "cancelled":
            raise KeyboardInterrupt
        if res.error_kind == "quota":
            if optional:
                raise Unavailable("kotası doldu")
            wait_or_pause(run, res.reset_at, agent)
            continue
        if res.error_kind == "refusal":
            if optional:
                raise Unavailable("güvenlik reddi")
            raise Refused(agent, res.error_text)
        if res.error_kind == "auth":
            if optional:
                raise Unavailable("oturum kapalı")
            raise RunPaused(f"{run.cfg.label(agent)} için oturum kapalı ya da giriş gerekli. "
                            f"Giriş yapıp `jev devam` yaz. ({res.error_text[:200]})", kind="oturum")
        tries += 1
        run.log("warn", f"{run.cfg.label(agent)} çağrısı başarısız ({res.error_kind}): {res.error_text[:300]}")
        if tries >= 2:
            if optional:
                raise Unavailable(f"iki kez başarısız oldu: {res.error_kind}")
            raise RunPaused(f"{run.cfg.label(agent)} {phase} aşamasında iki kez başarısız oldu "
                            f"({res.error_kind}). Kayıtlar: {res.paths.get('stdout', '')}")
        time.sleep(1)
