"""Koşu durumu: koşu kimliği ve klasörleri, state.json, kilit, koşu dizini."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .util import atomic_write_json, iso, jev_home, now, parse_iso, pid_alive, read_json, slugify

# "decomposing": eski koşulardan kalan aşama (artık plan ve kartlar tek çağrıda yazılıyor; devam edilince plana döner)
RUN_PHASES = ("sizing", "planning", "awaiting_approval", "decomposing", "executing", "reviewing", "reported",
              "fixing", "paused", "aborted")
# Kullanıcıyı beklemeyen aşamalar: çalışma süresi (work_s) yalnız bunlarda birikir; onay, rapor, duraklama sayılmaz
WORK_PHASES = {"sizing", "planning", "decomposing", "executing", "reviewing", "fixing"}
TASK_STATUSES = ("pending", "ready", "running", "verifying", "done", "needs_decision", "waiting_quota",
                 "split", "skipped", "failed", "blocked")
TERMINAL_TASK = {"done", "split", "skipped", "failed", "blocked"}
STATUS_TR = {
    "pending": "Bekliyor", "ready": "Sırada", "running": "Çalışıyor", "verifying": "Doğrulanıyor",
    "done": "Bitti", "needs_decision": "Sorunlu", "waiting_quota": "Kota bekliyor", "split": "Bölündü",
    "skipped": "Atlandı", "failed": "Başarısız", "blocked": "Engellendi",
}
TYPE_TR = {"code": "Kod", "test": "Test", "docs": "Belge", "config": "Yapılandırma", "research": "Araştırma"}
PHASE_TR = {
    "sizing": "Ölçekleme", "planning": "Plan", "awaiting_approval": "Onay bekliyor", "decomposing": "Plan",
    "executing": "Uygulama", "reviewing": "Son kontrol", "reported": "Rapor", "fixing": "Düzeltme",
    "paused": "Duraklatıldı", "aborted": "İptal edildi",
}


class LockError(RuntimeError):
    pass


def new_run_id(request: str) -> str:
    return now().astimezone().strftime("%Y%m%d-%H%M%S") + "-" + slugify(request, 24)


def jev_dir(project: Path) -> Path:
    return project / ".jev"


def run_dir(project: Path, run_id: str) -> Path:
    return jev_dir(project) / "runs" / run_id


def new_state(run_id: str, request: str, project: Path, *, dry: bool, speed: float, approval: bool) -> dict:
    t = iso()
    return {
        "run_id": run_id, "request": request, "project_dir": str(project),
        "phase": "sizing", "phase_since": t, "work_s": 0.0, "round": 1, "resume_phase": None,
        "paused_reason": None, "quota_until": None,
        "branch": f"jev/{run_id}", "base_commit": None,
        "created": t, "updated": t, "dry_run": dry, "speed": speed,
        "plan_approval": approval, "project_name": None,
        "tasks": [], "brain_calls": 0, "provider_streak": {"provider": None, "count": 0},
        "reports": [], "fix_note": None, "call_seq": 0, "deviations": [],
    }


def scale_level(state: dict) -> str:
    """Koşunun ölçeği; ölçeği olmayan (eski ya da ölçeklemeden geçmemiş) koşu büyük sayılır: bugünkü tam hat."""
    return (state.get("scale") or {}).get("level") or "buyuk"


def close_work(state: dict, end: str | None) -> None:
    """Açık aşama aralığını (phase_since → end) çalışma aşamasıysa work_s'e ekler."""
    since, until = parse_iso(state.get("phase_since")), parse_iso(end)
    if state.get("phase") in WORK_PHASES and since and until:
        state["work_s"] = round(float(state.get("work_s") or 0) + max(0.0, (until - since).total_seconds()), 1)


def work_seconds(state: dict, running: bool = True) -> float:
    """Toplam çalışma süresi; koşu şu an bir çalışma aşamasında sürüyorsa açık aralık da eklenir."""
    total, since = float(state.get("work_s") or 0), parse_iso(state.get("phase_since"))
    if running and state.get("phase") in WORK_PHASES and since:
        total += max(0.0, (now() - since).total_seconds())
    return total


def work_from_history(state: dict, hist: list[dict]) -> tuple[float, str | None]:
    """work_s tutmayan eski koşular için: run.phase olaylarından çalışma süresi (açık aralık hariç) ve
    son aşamanın başlangıcı. İlk aşama olay yazılmadan, koşu oluşturulurken başlar (ölçekleme; ondan önceki
    sürümlerde plan)."""
    total, phase, since = 0.0, "sizing" if state.get("scale") else "planning", state.get("created")
    for ev in hist:
        if ev.get("type") != "run.phase":
            continue
        a, b = parse_iso(since), parse_iso(ev.get("ts"))
        if phase in WORK_PHASES and a and b:
            total += max(0.0, (b - a).total_seconds())
        phase, since = (ev.get("data") or {}).get("phase"), ev.get("ts")
    return round(total, 1), since


def save_state(rdir: Path, state: dict) -> None:
    state["updated"] = iso()
    atomic_write_json(rdir / "state.json", state)


def load_state(rdir: Path) -> dict:
    st = read_json(rdir / "state.json")
    if not isinstance(st, dict):
        raise FileNotFoundError(f"state.json okunamadı: {rdir}")
    return st


# --- kilit -------------------------------------------------------------------

def _lock_info(path: Path) -> dict:
    """Kilit dosyasının içeriği; bozuksa pid 0 (sahipsiz kilit) sayılır."""
    info = read_json(path, {})
    info = info if isinstance(info, dict) else {}
    try:
        info["pid"] = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        info["pid"] = 0
    return info


class ProjectLock:
    def __init__(self, project: Path, run_id: str):
        self.path = jev_dir(project) / "lock"
        self.run_id = run_id
        self.held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = _lock_info(self.path)
                pid = info["pid"]
                if pid and pid != os.getpid() and pid_alive(pid):
                    raise LockError(
                        f"Bu projede başka bir jev süreci çalışıyor (PID {pid}, koşu {info.get('run_id')}).")
                try:
                    self.path.unlink()  # artık çalışmayan sürece ait kilit
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "run_id": self.run_id, "since": iso()}, f)
            self.held = True
            return
        raise LockError(f"Kilit alınamadı: {self.path}")

    def release(self) -> None:
        if self.held:
            try:
                if _lock_info(self.path)["pid"] == os.getpid():
                    self.path.unlink()
            except OSError:
                pass
            self.held = False


# --- koşu dizini (%USERPROFILE%\.jev\kosular.json) ---------------------------------

def _index_path() -> Path:
    return jev_home() / "kosular.json"


def index_update(run_id: str, **fields: Any) -> None:
    p = _index_path()
    idx = read_json(p, {}) or {}
    rec = idx.get(run_id, {})
    rec.update(fields)
    rec["updated"] = iso()
    idx[run_id] = rec
    atomic_write_json(p, idx)


def index_all() -> dict[str, dict]:
    return read_json(_index_path(), {}) or {}


def find_run(run_id: str | None, cwd: Path | None = None) -> tuple[Path, str]:
    """(proje, run_id) döndürür. run_id yoksa bulunulan projenin ya da dizindeki en son koşu."""
    idx = index_all()
    if run_id:
        rec = idx.get(run_id)
        if rec and Path(rec["project"], ".jev", "runs", run_id).exists():
            return Path(rec["project"]), run_id
        # kısmi eşleşme
        matches = [k for k in idx if k.startswith(run_id) or run_id in k]
        if len(matches) == 1:
            return Path(idx[matches[0]]["project"]), matches[0]
        if cwd is not None and (run_dir(cwd, run_id)).exists():
            return cwd, run_id
        raise FileNotFoundError(f"Koşu bulunamadı: {run_id}")
    if cwd is not None:
        runs = jev_dir(cwd) / "runs"
        if runs.exists():
            ids = sorted(p.name for p in runs.iterdir() if (p / "state.json").exists())
            if ids:
                return cwd, ids[-1]
    live = [(k, v) for k, v in idx.items() if Path(v.get("project", ""), ".jev", "runs", k).exists()]
    if not live:
        raise FileNotFoundError("Hiç koşu bulunamadı. Yeni koşu için: jev \"istek\"")
    live.sort(key=lambda kv: kv[1].get("created", kv[0]))
    k, v = live[-1]
    return Path(v["project"]), k
