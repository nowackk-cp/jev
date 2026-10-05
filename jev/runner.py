"""Koşu yöneticisi: yeni koşu ve devam, aşama makinesi, Ctrl+C, komut kuyruğu, koruma izleyici, ofis sunucusu.

Aşamalar: sizing → planning → [awaiting_approval] → executing → reviewing → reported (DURUR).
Ölçekleme işin boyunu seçer; mini ve küçük işte plan atlanır (sizing → executing). Planlayıcı planı ve görev
kartlarını tek çağrıda yazar (eski koşulardaki "decomposing" aşaması plana döner).
"düzelt" → fixing → executing → reviewing → reported. Ayrıca paused ve aborted.
"""
from __future__ import annotations

import datetime as _dt
import os
import queue
import re
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import context, gitops, lanes, routing
from .events import EventBus
from .gateway import Gateway
from .phases.common import RunAborted, RunPaused, task_event
from .quota import QuotaBook
from .state import (PHASE_TR, STATUS_TR, LockError, ProjectLock, close_work, find_run, index_update,
                    load_state, new_run_id, new_state, run_dir, save_state, work_from_history, work_seconds)
from .terminal import Terminal
from .util import clock, ek, expand_path, iso, local_hhmm, now, parse_iso, read_json, shorten, slugify

ACTIVE_PHASES = {"sizing", "planning", "awaiting_approval", "decomposing", "executing", "reviewing", "fixing"}
COMMANDS = {
    "düzelt": "duzelt", "duzelt": "duzelt", "durum": "durum", "rapor": "rapor", "devam": "devam", "ofis": "ofis",
    "çıkış": "cikis", "çikiş": "cikis", "cikis": "cikis", "exit": "cikis", "quit": "cikis", "q": "cikis",
    "onayla": "onayla", "reddet": "reddet", "yardım": "yardim", "yardim": "yardim", "help": "yardim", "?": "yardim",
}
HELP = ("Komutlar: düzelt [not] · durum · rapor · devam · ofis · çıkış"
        "  (plan onayında: onayla · reddet)")


class SetupError(RuntimeError):
    """Koşu başlatılamadı (kullanıcıya gösterilecek Türkçe mesaj)."""


def parse_command(line: str) -> tuple[str, str]:
    s = (line or "").strip()
    if not s:
        return "", ""
    head, _, rest = s.partition(" ")
    cmd = COMMANDS.get(head.lower(), "?" + head)
    arg = rest.strip()
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        arg = arg[1:-1]
    return cmd, arg


# --- proje hazırlığı -------------------------------------------------------------------------

def prepare_project(cfg, request: str, project_arg: str | None, dry: bool, term: Terminal) -> tuple[Path, bool]:
    """(proje klasörü, yeni mi). Yeni proje: proje_koku altında slug klasörü + git init + ilk commit."""
    if project_arg:
        p = expand_path(project_arg).resolve()
        if not p.exists() or (p.is_dir() and not any(p.iterdir())):
            p.mkdir(parents=True, exist_ok=True)
            gitops.init_repo(p)
            return p, True
        if not p.is_dir():
            raise SetupError(f"--proje bir klasör olmalı: {p}")
        if gitops.is_repo(p):
            if not gitops.has_commits(p):
                raise SetupError(f"{p} deposunda hiç commit yok. Önce bir commit yap, sonra yeniden dene.")
            gitops.ensure_exclude(p)
            if not gitops.is_clean(p):
                raise SetupError(f"{p} çalışma ağacı temiz değil. Değişiklikleri commit et ya da stash yap "
                                 "(Jev otomatik stash yapmaz), sonra yeniden dene.")
            return p, False
        if gitops.inside_repo(p):
            raise SetupError(f"{p} başka bir git deposunun alt klasörü. Jev deponun kökünde çalışır; "
                             "--proje ile deponun kök klasörünü ver.")
        term.warn(f"{p} bir git deposu değil; git deposu açılıp mevcut dosyalar ilk commit'e alınıyor.")
        gitops.init_repo(p, "jev: mevcut dosyalar (ilk commit)")
        return p, False
    root = cfg.project_root
    root.mkdir(parents=True, exist_ok=True)
    base = slugify(request, 40) or "proje"
    if dry:
        base = "kuru-" + base
    p, i = root / base, 2
    while p.exists():
        p, i = root / f"{base}-{i}", i + 1
    p.mkdir(parents=True)
    gitops.init_repo(p)
    return p, True


class GuardWatcher(threading.Thread):
    """guard.jsonl'ı izler (Claude kancası ayrı bir süreçte yazar) ve arayüze `guard.blocked` olayı gönderir."""

    def __init__(self, path: Path, emit):
        super().__init__(daemon=True, name="jev-koruma-izleyici")
        self.path = path
        self.emit = emit
        self.stop_event = threading.Event()
        self.pos = path.stat().st_size if path.exists() else 0
        self._buf = b""

    def run(self) -> None:
        while not self.stop_event.wait(0.5):
            self.poll()

    def poll(self) -> None:
        import json
        try:
            if not self.path.exists():
                return
            size = self.path.stat().st_size
            if size < self.pos:
                self.pos = 0
            if size == self.pos:
                return
            with self.path.open("rb") as f:
                f.seek(self.pos)
                chunk = f.read(size - self.pos)
            self.pos = size
        except OSError:
            return
        data = self._buf + chunk
        lines = data.split(b"\n")
        self._buf = lines.pop()
        for ln in lines:
            try:
                rec = json.loads(ln.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(rec, dict) or "reason" not in rec:
                continue
            self.emit("guard.blocked", {"agent": rec.get("ajan"), "task_id": rec.get("gorev"),
                                        "tool": rec.get("arac"), "reason": rec.get("reason"),
                                        "category": rec.get("category"), "category_tr": rec.get("category_tr"),
                                        "serious": bool(rec.get("serious")), "action": rec.get("eylem"),
                                        "input": shorten(str(rec.get("girdi") or ""), 200)})


# --- koşu -------------------------------------------------------------------------------------

class Runner:
    def __init__(self, cfg, project: Path, state: dict, *, term: Terminal, ui: bool = True,
                 open_browser: bool = True, verbose: bool = False, exit_after: bool = False,
                 scenario: dict | None = None):
        self.cfg = cfg
        self.project = Path(project)
        self.state = state
        self.term = term
        self.verbose = verbose
        self.ui = ui and bool(cfg.get("arayuz", "acik", default=True))
        self.open_browser = open_browser and bool(cfg.get("arayuz", "tarayici_ac", default=True))
        self.exit_after = exit_after
        self.rdir = run_dir(self.project, state["run_id"])
        self.rdir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus(state["run_id"], self.rdir / "events.jsonl")
        hist = self.bus.history()
        # önceki sürecin son yaşam belirtisi: kapanmış bir sürecin çalışma süresi burada biter (mark_crashed)
        self._last_seen = max((t for t in (state.get("updated"), hist[-1].get("ts") if hist else None) if t),
                              default=None)
        if "work_s" not in state:  # eski sürümün koşusu: çalışma süresini olay geçmişinden kur
            state["work_s"], since = work_from_history(state, hist)
            state["phase_since"] = state.get("phase_since") or since
        dry = bool(state.get("dry_run"))
        self.quota = QuotaBook(self.rdir / "kuru-kota.json" if dry else None)
        self.cancel = threading.Event()
        self.controls: queue.Queue = queue.Queue()
        self.plan: dict = read_json(self.rdir / "plan.json", {}) or {}
        self.gw = Gateway(cfg, rdir=self.rdir, project=self.project, state=state, bus=self.bus, quota=self.quota,
                          save=self.save, dry=dry, scenario=scenario, speed=float(state.get("speed") or 1.0),
                          cancel=self.cancel)
        from . import systemone
        # Jev'in kendi modeli (TypeSafe Jev); kuruda senaryoyu sahte adaptörle paylaşır
        self.jev = systemone.make(cfg, self.rdir, dry=dry, scenario=lambda: self.gw.adapter("opus").scenario)
        self.lock = ProjectLock(self.project, state["run_id"])
        self.url: str | None = None
        self.server = None
        self._input_started = False
        self._stdin_eof = False
        self._pending = False
        self._watcher: GuardWatcher | None = None
        self._state_lock = threading.RLock()
        self.bus.add_listener(self._echo)

    # --- temel yardımcılar -----------------------------------------------------------------
    @property
    def run_id(self) -> str:
        return self.state["run_id"]

    def emit(self, type_: str, data: dict | None = None) -> dict:
        return self.bus.emit(type_, data)

    def log(self, level: str, text: str) -> None:
        self.emit("log", {"level": level, "text": text})

    def save(self) -> None:
        # paralel görevlerde başka bir iş parçacığı durumu yazarken değiştirebilir: json 'changed size' hatasında
        # kısa aralıklarla yeniden denenir
        with self._state_lock:
            for i in range(8):
                try:
                    save_state(self.rdir, self.state)
                    return
                except RuntimeError:
                    if i == 7:
                        raise
                    time.sleep(0.02)

    def set_phase(self, phase: str, **extra) -> None:
        st, t = self.state, iso()
        close_work(st, t)  # ofisin süre sayacı: onay, rapor ve duraklama beklemesi sayılmaz
        st["phase"] = phase
        st["phase_since"] = t
        self.save()
        self.emit("run.phase", {"phase": phase, "round": st.get("round", 1), "work_s": st.get("work_s", 0.0),
                                **extra})
        index_update(self.run_id, phase=phase, round=st.get("round", 1))

    def _echo(self, ev: dict) -> None:
        t, d = ev["type"], ev.get("data") or {}
        term = self.term
        if t == "log":
            fn = {"warn": term.warn, "error": term.error, "ok": term.ok}.get(d.get("level"), term.info)
            fn(str(d.get("text", "")))
        elif t == "guard.blocked":
            term.warn(f"Koruma: {d.get('reason')} — {context.display(d.get('agent') or '?')} "
                      f"({d.get('category_tr') or d.get('category')}; {d.get('action') or 'engellendi'})")
        elif t == "quota":
            until = local_hhmm(parse_iso(d.get("until")))  # 23:56'ya, 10:30'a
            ortak = f" (ortak kota; {context.display(d['shared_from'])} çağrısında doldu)" if d.get("shared_from") else ""
            term.warn(f"Kota: {context.display(d.get('agent') or '?')} ~{ek(until, 'e')} kadar soğumada{ortak}.")
        elif t == "agent.activity" and self.verbose:
            term.dim(f"    {context.display(d.get('agent') or '?')} · {d.get('kind')}: {shorten(d.get('text') or '', 150)}")

    # --- komutlar ----------------------------------------------------------------------------
    def start_input(self) -> None:
        if self._input_started or not _stdin_is_tty():
            return
        self._input_started = True

        def reader() -> None:
            while True:
                try:
                    line = sys.stdin.readline()
                except (OSError, ValueError):
                    line = ""
                if line == "":
                    self._stdin_eof = True
                    self.controls.put(("eof", "", "terminal"))
                    return
                cmd, arg = parse_command(line)
                if cmd:
                    self.controls.put((cmd, arg, "terminal"))

        threading.Thread(target=reader, daemon=True, name="jev-girdi").start()

    def submit(self, cmd: str, arg: str = "", source: str = "arayuz") -> tuple[bool, str]:
        """Arayüzden gelen komut. Yalnızca uygun aşamada kabul edilir (§7.5)."""
        ph = self.state.get("phase")
        need = {"duzelt": "reported", "onayla": "awaiting_approval", "reddet": "awaiting_approval",
                "devam": "paused"}.get(cmd)
        if need is None:
            return False, "Bilinmeyen komut."
        if ph != need:
            return False, f"Bu komut şu an kullanılamaz (aşama: {PHASE_TR.get(ph, ph)})."
        if self._pending:
            return False, "Önceki komut işleniyor."
        if cmd == "duzelt":  # kabul edip sessizce hiçbir şey yapmamak yerine hemen söyle
            from .phases.fix import NOTHING_TO_FIX, nothing_to_fix
            from .phases.review import latest_report
            if nothing_to_fix(latest_report(self.rdir, self.state)[0], arg):
                return False, NOTHING_TO_FIX
        self._pending = True
        self.controls.put((cmd, arg, source))
        return True, "Tamam."

    def next_command(self) -> tuple[str, str]:
        """Terminal ya da arayüzden bir komut bekler (Ctrl+C'ye duyarlı)."""
        while True:
            if self.server is None and (self._stdin_eof or not self._input_started):
                return "cikis", ""  # komut gelebilecek bir kanal yok: sonsuza dek bekleme
            try:
                cmd, arg, source = self.controls.get(timeout=0.2)
            except queue.Empty:
                continue
            self._pending = False
            if cmd == "eof":
                if self.server is None:
                    return "cikis", ""
                continue
            if source == "arayuz":
                self.term.info(f"Arayüzden komut: {cmd}" + (f" ({shorten(arg, 80)})" if arg else ""))
            return cmd, arg

    def poll_commands(self) -> None:
        """Uygulama sırasında gelen komutlar: yalnızca durum ve çıkış."""
        while True:
            try:
                cmd, arg, source = self.controls.get_nowait()
            except queue.Empty:
                return
            self._pending = False
            if cmd == "durum":
                self.print_status()
            elif cmd == "cikis":
                raise RunPaused("Kullanıcı çıkış istedi.", kind="kullanici")
            elif cmd == "yardim":
                self.term.info(HELP)
            elif cmd not in ("eof", ""):
                self.term.warn("Koşu sürüyor; şu an yalnızca `durum` ve `çıkış` kullanılabilir.")

    def sleep_until(self, until: _dt.datetime) -> None:
        last = None
        while True:
            left = (until - now()).total_seconds()
            if left <= 0:
                return
            mins = int(left // 60)
            if mins != last and (last is None or mins % 5 == 0 or mins < 5):
                self.term.dim(f"  … {ek(local_hhmm(until), 'e')} ~{mins + 1} dk (çıkmak için `çıkış` ya da Ctrl+C)")
                last = mins
            time.sleep(min(1.0, left))
            self.poll_commands()

    # --- ekran -------------------------------------------------------------------------------
    def print_status(self) -> None:
        print_status(self.term, self.state, self.cfg, self.quota, self.url)

    # --- yaşam döngüsü -------------------------------------------------------------------------
    def open(self) -> None:
        try:
            self.lock.acquire()
        except LockError as e:
            raise SetupError(str(e)) from e
        self._watcher = GuardWatcher(self.rdir / "guard.jsonl", self.emit)
        self._watcher.start()
        if self.ui:
            self.start_office()

    def close(self) -> None:
        if self._watcher:
            self._watcher.poll()
            self._watcher.stop_event.set()
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:
                pass
            self.server = None
        self.state["ui_url"] = None
        try:
            self.save()
        except OSError:
            pass
        self.bus.close()
        self.lock.release()

    def start_office(self) -> None:
        try:
            from .office.server import OfficeServer
            self.server = OfficeServer(self, port=int(self.cfg.get("arayuz", "port", default=8765) or 8765))
            self.url = self.server.start()
        except Exception as e:  # arayüz olmadan da çalışır
            self.server = None
            self.term.warn(f"Ofis arayüzü açılamadı: {e}")
            return
        self.state["ui_url"] = self.url
        self.save()
        index_update(self.run_id, ui_url=self.url)
        if self.open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass

    def drive(self) -> str:
        """Aşama makinesi. Dönüş: reported | paused | aborted | interrupted."""
        from .phases.execute import run_execute
        from .phases.fix import run_fix
        from .phases.plan import await_approval, run_plan
        from .phases.review import run_review
        from .phases.sizing import run_sizing
        steps = {"sizing": run_sizing, "planning": run_plan, "awaiting_approval": await_approval, "decomposing": run_plan,
                 "executing": run_execute, "reviewing": run_review, "fixing": run_fix}
        try:
            while True:
                ph = self.state.get("phase")
                step = steps.get(ph)
                if step is None:
                    return ph
                step(self)
        except RunPaused as e:
            self.pause(e.reason, e.until, e.kind)
            return "paused"
        except RunAborted as e:
            self.abort(str(e))
            return "aborted"
        except KeyboardInterrupt:
            self.interrupted()
            return "interrupted"

    def pause(self, reason: str, until: _dt.datetime | None = None, kind: str = "hata") -> None:
        st = self.state
        if st.get("phase") != "paused":
            st["resume_phase"] = st.get("phase")
        st.update(paused_reason=reason, quota_until=iso(until) if until else None, pause_kind=kind)
        for t in st["tasks"]:  # yarım görev kalmasın (paralel görevin şeridi kaldırılır)
            if t.get("status") in ("running", "verifying", "needs_decision"):
                lanes.rollback(self, t)
                t.update(status="ready", agent=None, keep_changes=False)
                self.emit("task.update", task_event(t))
        self.set_phase("paused", reason=reason, until=iso(until) if until else None, kind=kind)
        self.term.warn(f"Koşu duraklatıldı: {reason}")
        self.term.line(f"Sürdürmek için: `{self.resume_cmd()}`" + (f" (ya da arayüzde Devam: {self.url})" if self.url else ""))

    def resume_cmd(self) -> str:
        """Sürdürme komutu: kimliksiz `jev devam` bu koşuyu bulacaksa kısa biçim, yoksa kimlikle."""
        try:
            _, rid = find_run(None, Path.cwd())
        except (OSError, FileNotFoundError):
            rid = None
        return "jev devam" if rid == self.run_id else f"jev devam {self.run_id}"

    def abort(self, reason: str) -> None:
        self.state["paused_reason"] = reason
        for t in self.state["tasks"]:  # iptal edilen koşu sürdürülemez: görev kopyaları kaldırılır
            if t.get("lane"):
                lanes.close_lane(self, t)
        lanes.sweep(self)
        self.set_phase("aborted", reason=reason)
        self.term.error(f"Koşu iptal edildi: {reason}")

    def interrupted(self) -> None:
        old = signal.getsignal(signal.SIGINT)
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (ValueError, OSError):
            old = None
        try:
            self.cancel.set()
            st = self.state
            for t in st["tasks"]:
                if t.get("status") in ("running", "verifying", "needs_decision"):
                    try:
                        lanes.rollback(self, t)
                    except gitops.GitError as e:
                        self.term.error(f"Geri alma başarısız: {e}")
                    t.update(status="ready", agent=None, keep_changes=False)
                    self.emit("task.update", task_event(t))
            if st.get("phase") != "paused":
                st["resume_phase"] = st.get("phase")
            st.update(paused_reason="Ctrl+C ile durduruldu.", pause_kind="kullanici", quota_until=None)
            self.set_phase("paused", reason="Ctrl+C ile durduruldu.", kind="kullanici")
            self.all_idle("paused")
            self.term.line("")
            self.term.warn("Durduruldu. Yarım kalan görevin değişiklikleri geri alındı.")
            self.term.line(f"Kaldığı yerden sürdürmek için: `{self.resume_cmd()}`")
        finally:
            if old is not None:
                try:
                    signal.signal(signal.SIGINT, old)
                except (ValueError, OSError):
                    pass

    def prepare_resume(self) -> bool:
        """`jev devam`: yarım görevleri geri alır, duraklamadan önceki aşamaya döner."""
        st, project = self.state, self.project
        ph = st.get("phase")
        if ph == "aborted":
            self.term.error("Bu koşu iptal edilmiş; devam ettirilemez. Yeni bir koşu başlat.")
            return False
        if ph == "reported":
            self.term.info("Koşu rapor aşamasında. Düzeltme için: `düzelt [not]`.")
            return True
        cur = gitops.current_branch(project)
        if st.get("branch") and cur != st["branch"]:
            if gitops.is_clean(project):
                gitops.git(["checkout", "-q", st["branch"]], project)
                self.term.info(f"Koşu dalına geçildi: {st['branch']}")
            else:
                raise SetupError(f"Proje şu an '{cur}' dalında ve kaydedilmemiş değişiklikler var. Değişiklikleri "
                                 f"commit et ya da stash yap, sonra yeniden `jev devam` yaz.")
        for t in st["tasks"]:
            if t.get("status") in ("running", "verifying", "needs_decision"):
                lanes.rollback(self, t)
                t.update(status="ready", agent=None, keep_changes=False)
                self.emit("task.update", task_event(t))
                self.log("info", f"{t['id']} yarım kalmıştı; değişiklikleri geri alındı, görev yeniden sırada.")
            elif t.get("status") == "waiting_quota":
                t["status"] = "ready"
                self.emit("task.update", task_event(t))
        lanes.sweep(self)  # paralel koşudan kalan görev kopyaları (korunanlar hariç)
        if not gitops.is_clean(project) and not any(t.get("keep_changes") for t in st["tasks"]):
            self.term.warn("Çalışma ağacında kaydedilmemiş değişiklikler var; bir sonraki görevin commit'ine girecek.")
        if ph == "paused":
            if st.get("pause_kind") == "beyin_siniri":
                from .phases.brain import BRAIN_LIMIT_EXTRA
                st["brain_limit_extra"] = int(st.get("brain_limit_extra", 0) or 0) + BRAIN_LIMIT_EXTRA
            nxt = st.get("resume_phase") or "executing"
            st.update(paused_reason=None, pause_kind=None, quota_until=None, resume_phase=None)
            self.set_phase(nxt)
            self.log("info", f"Koşu sürdürülüyor: {PHASE_TR.get(nxt, nxt)}.")
        else:
            self.save()
        return True

    def mark_crashed(self) -> None:
        """Süreç beklenmedik şekilde kapanmışsa (etkin aşamada ama kilit yok) koşuyu duraklatılmış say."""
        st = self.state
        if st.get("phase") in ACTIVE_PHASES:
            # süreç son yaşam belirtisinden sonra çalışmadı: kapalı kaldığı süre çalışma süresine eklenmez
            close_work(st, self._last_seen)
            st["phase_since"] = None
            st["resume_phase"] = st["phase"]
            st.update(paused_reason="Önceki jev süreci beklenmedik şekilde kapandı.", pause_kind="hata")
            self.set_phase("paused", reason=st["paused_reason"], kind="hata")
            self.all_idle("paused")  # ofiste eski süreçten kalan "çalışıyor" görüntüsü kalmasın

    def all_idle(self, phase: str) -> None:
        for a in list(self.cfg.agents) + ["jev"]:
            self.emit("agent.state", {"agent": a, "state": "idle", "task_id": None, "phase": phase,
                                      "text": "", "until": None})

    # --- oturum ----------------------------------------------------------------------------
    def interactive(self) -> bool:
        if self.exit_after:
            return False
        return _stdin_is_tty() or self.server is not None

    def session(self, *, drive_first: bool = True) -> int:
        """Aşamaları yürütür; rapordan (ya da duraklamadan) sonra komut bekler. Çıkış kodu döndürür."""
        self.start_input()
        outcome = self.drive() if drive_first else self.state.get("phase")
        while True:
            if outcome == "interrupted":
                return 130
            if not self.interactive():
                return {"reported": 0, "paused": 3, "aborted": 4}.get(outcome, 0)
            self._show_prompt(outcome)
            cmd, arg = self.next_command()
            if cmd == "cikis":
                ph = self.state.get("phase")
                self.term.info("Görüşürüz. Koşu kaydedildi" + (f"; tekrar açmak için: jev ofis {self.run_id}" if outcome else ""))
                return {"paused": 3, "aborted": 4}.get(ph, 0)
            if cmd == "duzelt":
                from .phases.fix import start_fix
                if start_fix(self, arg):
                    outcome = self.drive()
                continue
            if cmd == "devam":
                if self.state.get("phase") == "paused":
                    self.cancel.clear()
                    if self.prepare_resume():
                        outcome = self.drive()
                else:
                    self.term.info("Duraklatılmış bir koşu yok.")
                continue
            if cmd == "durum":
                self.print_status()
            elif cmd == "rapor":
                show_report(self.term, self.rdir, self.state)
            elif cmd == "ofis":
                if self.url:
                    webbrowser.open(self.url)
                    self.term.info(f"Ofis: {self.url}")
                else:
                    self.start_office()
            elif cmd == "yardim":
                self.term.info(HELP)
            elif cmd in ("onayla", "reddet"):
                self.term.warn("Onay bekleyen bir plan yok.")
            elif cmd.startswith("?"):
                self.term.warn(f"Bilinmeyen komut: {cmd[1:]}. {HELP}")

    def _show_prompt(self, outcome: str) -> None:
        ph = self.state.get("phase")
        if ph == "reported":
            tip = "Komut bekleniyor: düzelt [not] · rapor · durum · ofis · çıkış"
        elif ph == "paused":
            tip = "Komut bekleniyor: devam · durum · ofis · çıkış"
        else:
            tip = "Komut bekleniyor: durum · rapor · ofis · çıkış"
        self.term.dim(tip + (f"  (arayüz: {self.url})" if self.url else ""))


def _stdin_is_tty() -> bool:
    """Girdi gerçekten etkileşimli bir konsol mu? Windows'ta NUL aygıtı da isatty()=True verir;
    bu yüzden orada konsol kipi okunabiliyor mu diye ayrıca bakılır."""
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return True


# --- koşu oluşturma / yükleme ------------------------------------------------------------------

def create_run(cfg, request: str, *, project_arg: str | None, approval: bool, dry: bool, speed: float,
               term: Terminal, scale: str | None = None, **runner_kw) -> Runner:
    request = (request or "").strip()
    if not request:
        raise SetupError("İstek boş olamaz.")
    project, is_new = prepare_project(cfg, request, project_arg, dry, term)
    run_id = new_run_id(request)
    state = new_state(run_id, request, project, dry=dry, speed=speed, approval=approval)
    state["new_project"] = is_new
    if scale:
        state["forced_scale"] = scale  # --olcek: Jev'in ölçümünden üstün
    lock = ProjectLock(project, run_id)
    try:
        lock.acquire()
    except LockError as e:
        raise SetupError(str(e)) from e
    try:
        gitops.ensure_exclude(project)
        state["origin_branch"] = gitops.current_branch(project)
        state["base_commit"] = gitops.head(project)
        gitops.start_branch(project, state["branch"])
    finally:
        lock.release()
    rdir = run_dir(project, run_id)
    rdir.mkdir(parents=True, exist_ok=True)
    save_state(rdir, state)
    index_update(run_id, project=str(project), request=request, created=state["created"], phase="sizing",
                 dry=dry, round=1)
    runner = Runner(cfg, project, state, term=term, **runner_kw)
    return runner


def load_run(cfg, project: Path, run_id: str, *, term: Terminal, **runner_kw) -> Runner:
    rdir = run_dir(project, run_id)
    state = load_state(rdir)
    return Runner(cfg, project, state, term=term, **runner_kw)


# --- ekran yardımcıları ------------------------------------------------------------------------

def print_status(term: Terminal, st: dict, cfg, quota: QuotaBook | None = None, url: str | None = None,
                 running: bool = True) -> None:
    done, total = routing.progress(st)
    ph = st.get("phase")
    term.header(f"Koşu {st.get('run_id')}")
    term.line(f"İstek: {shorten(st.get('request', ''), 200)}", stamp=False)
    term.line(f"Aşama: {PHASE_TR.get(ph, ph)} · tur {st.get('round', 1)} · ilerleme {done}/{total}"
              + (f" · çalışma {clock(work_seconds(st, running))}" if "work_s" in st else "")
              + (" · KURU KOŞU" if st.get("dry_run") else ""), stamp=False)
    if st.get("scale"):
        from .phases.sizing import describe
        term.line(f"Ölçek: {describe(st)}", stamp=False)
    if ph == "paused" and st.get("paused_reason"):
        term.line(f"Duraklama nedeni: {st['paused_reason']}", "yellow", stamp=False)
    term.line(f"Proje: {st.get('project_dir')} · dal {st.get('branch')}", stamp=False)
    for t in st.get("tasks", []):
        agent = context.display(t["agent"]) if t.get("agent") else "-"
        term.line(f"  {t['id']:<8} {STATUS_TR.get(t.get('status'), t.get('status')):<14} {agent:<8} "
                  f"{int(t.get('attempt_count', 0))} deneme · {shorten(t.get('title', ''), 60)}", stamp=False)
    if quota is not None:
        cool = quota.all()
        if cool:
            term.line("Soğumadakiler: " + ", ".join(
                f"{context.display(a)} ~{local_hhmm(parse_iso(r.get('until')))}" for a, r in cool.items()), stamp=False)
    term.line(f"Beyin çağrısı: {st.get('brain_calls', 0)}/{cfg.limit('kosu_basina_beyin_cagrisi')}", stamp=False)
    if url:
        term.line(f"Ofis: {url}", stamp=False)


def show_report(term: Terminal, rdir: Path, st: dict) -> bool:
    from .phases.review import latest_report
    rep, md = latest_report(rdir, st)
    if rep is None:
        term.warn("Bu koşunun henüz raporu yok.")
        return False
    term.line(md, stamp=False)
    reps = st.get("reports") or []
    term.dim(f"Rapor dosyası: {rdir / reps[-1]['file']}")
    return True
