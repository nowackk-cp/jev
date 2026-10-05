"""Koşu yöneticisi (runner.py): proje hazırlığı, koşu oluşturma, aşama makinesi, duraklatma ve sürdürme, Ctrl+C,
çökme sonrası kurtarma, komut kuyruğu, etkileşimli oturum, koruma izleyici ve düzeltme turunun başlatılması.

Model çağrısı yapılmaz: koşular kuru (sahte ajanlar) ve aşama adımları gerektiğinde yakalanır. Gerçek stdin hiç
okunmaz (`_stdin_is_tty` her testte yakalanır); ofis sunucusu açılmaz, tarayıcı açılmaz.
"""
import copy
import datetime as dt
import io
import json
import os
import signal
import sys
import time
import unittest
from unittest import mock

import _ortak
from jev import gitops
from jev import runner as runner_mod
from jev.phases.brain import BRAIN_LIMIT_EXTRA
from jev.phases.common import RunAborted, RunPaused
from jev.phases.fix import nothing_to_fix, start_fix
from jev.phases.review import fix_hint, report_name
from jev.runner import GuardWatcher, SetupError, create_run, prepare_project
from jev.state import index_all, jev_dir, load_state
from jev.util import atomic_write_json, atomic_write_text, ek, iso, local_hhmm, now, shorten, slugify

LIVE_PID = 4242  # testlerde "başka bir jev süreci" sayılan PID
REAL_STDIN_IS_TTY = runner_mod._stdin_is_tty


def header(text: str) -> str:
    return "━" * 8 + " " + text + " " + "━" * 8


class PrepareProjectTests(unittest.TestCase):
    REQUEST = "Yapılacaklar listesi CLI tasarla"

    def setUp(self):
        self.root = _ortak.temp_dir("koku")
        self.cfg = _ortak.make_config(proje_koku=str(self.root / "projeler"))
        self.term = _ortak.QuietTerminal()

    def prepare(self, project_arg=None, *, dry=False, request=REQUEST):
        return prepare_project(self.cfg, request, None if project_arg is None else str(project_arg), dry, self.term)

    def test_new_projects_get_their_own_folder(self):
        base = self.root / "projeler" / slugify(self.REQUEST, 40)
        self.assertEqual(self.prepare(), (base, True))
        self.assertEqual(self.prepare(), (base.with_name(base.name + "-2"), True))  # aynı istek: yeni klasör
        self.assertEqual(self.prepare(dry=True), (base.with_name("kuru-" + base.name), True))
        self.assertEqual(self.prepare(request="!!!")[0], self.root / "projeler" / "proje")
        for p in (base, base.with_name(base.name + "-2")):
            self.assertTrue(gitops.is_repo(p) and gitops.has_commits(p) and gitops.is_clean(p), p.name)
        self.assertEqual(self.term.lines, [])

    def test_project_folder_is_created_or_adopted(self):
        new = self.root / "yeni" / "alt"
        self.assertEqual(self.prepare(new), (new.resolve(), True))
        self.assertTrue(gitops.is_repo(new))
        empty = self.root / "bos"
        empty.mkdir()
        self.assertEqual(self.prepare(empty), (empty.resolve(), True))
        # dosyaları olan klasör: depo açılır, dosyalar ilk commit'e girer; proje yeni sayılmaz (plan mevcut kodu okur)
        old = self.root / "mevcut"
        old.mkdir()
        (old / "notlar.txt").write_text("merhaba\n", encoding="utf-8")
        self.assertEqual(self.prepare(old), (old.resolve(), False))
        self.assertEqual(self.term.lines, [f"! {old.resolve()} bir git deposu değil; git deposu açılıp mevcut "
                                           "dosyalar ilk commit'e alınıyor."])
        self.assertEqual(gitops.out(["log", "--format=%s"], old), "jev: mevcut dosyalar (ilk commit)")
        self.assertIn("notlar.txt", gitops.out(["ls-files"], old).splitlines())
        # temiz depo olduğu gibi kullanılır; Jev'in kendi klasörü (.jev) ağacı kirli göstermez
        atomic_write_json(jev_dir(old) / "lock", {})
        self.assertEqual(self.prepare(old), (old.resolve(), False))
        self.assertEqual(len(self.term.lines), 1)

    def test_refusals(self):
        a_file = self.root / "dosya.txt"
        a_file.write_text("x", encoding="utf-8")
        no_commit = self.root / "commitsiz"
        no_commit.mkdir()
        gitops.git(["init", "-q"], no_commit)
        dirty = self.root / "kirli"
        gitops.init_repo(dirty)
        (dirty / "yarim.py").write_text("x = 1\n", encoding="utf-8")
        sub = self.root / "ana" / "alt"
        gitops.init_repo(self.root / "ana")
        sub.mkdir()
        (sub / "a.txt").write_text("x", encoding="utf-8")
        cases = [
            (a_file, f"--proje bir klasör olmalı: {a_file.resolve()}"),
            (no_commit, f"{no_commit.resolve()} deposunda hiç commit yok. Önce bir commit yap, sonra yeniden dene."),
            (dirty, f"{dirty.resolve()} çalışma ağacı temiz değil. Değişiklikleri commit et ya da stash yap "
                    "(Jev otomatik stash yapmaz), sonra yeniden dene."),
            (sub, f"{sub.resolve()} başka bir git deposunun alt klasörü. Jev deponun kökünde çalışır; "
                  "--proje ile deponun kök klasörünü ver."),
        ]
        for path, msg in cases:
            with self.subTest(path=path.name):
                with self.assertRaises(SetupError) as cm:
                    self.prepare(path)
                self.assertEqual(str(cm.exception), msg)
        self.assertTrue((dirty / "yarim.py").exists())  # kullanıcının işine dokunulmaz
        self.assertFalse((sub / ".git").exists())


class CreateRunTests(unittest.TestCase):
    def setUp(self):
        self.project = _ortak.temp_dir("olustur") / "proj"
        self.cfg = _ortak.make_config()

    def create(self, request="Yapılacaklar listesi CLI tasarla"):
        return create_run(self.cfg, request, project_arg=str(self.project), approval=False, dry=True, speed=1000.0,
                          term=_ortak.QuietTerminal(), ui=False, open_browser=False, exit_after=True)

    def test_empty_request(self):
        for req in ("", "   ", None):
            with self.assertRaises(SetupError) as cm:
                self.create(req)
            self.assertEqual(str(cm.exception), "İstek boş olamaz.")
        self.assertFalse(self.project.exists())

    def test_new_run(self):
        run = self.create("  Yapılacaklar listesi CLI tasarla ")
        self.addCleanup(run.bus.close)
        st, p = run.state, self.project.resolve()
        self.assertEqual((run.project, st["request"], st["phase"], st["round"], st["dry_run"], st["new_project"]),
                         (p, "Yapılacaklar listesi CLI tasarla", "sizing", 1, True, True))
        self.assertEqual(st["branch"], f"jev/{run.run_id}")
        self.assertEqual(gitops.current_branch(p), st["branch"])
        self.assertNotEqual(st["origin_branch"], st["branch"])
        self.assertEqual(st["base_commit"], gitops.head(p))
        self.assertEqual(load_state(run.rdir)["run_id"], run.run_id)
        rec = index_all()[run.run_id]
        self.assertEqual({k: rec.get(k) for k in ("project", "request", "phase", "dry", "round")},
                         {"project": str(p), "request": st["request"], "phase": "sizing", "dry": True, "round": 1})
        self.assertFalse((jev_dir(p) / "lock").exists())  # kilit yalnızca oturum sürerken tutulur
        self.assertIn(".jev/", (p / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines())

    def test_busy_project(self):
        gitops.init_repo(self.project)
        lock = jev_dir(self.project) / "lock"
        atomic_write_json(lock, {"pid": LIVE_PID, "run_id": "20260101-000000-baska"})
        branch = gitops.current_branch(self.project)
        with mock.patch("jev.state.pid_alive", side_effect=lambda pid: pid == LIVE_PID):
            with self.assertRaises(SetupError) as cm:
                self.create()
        self.assertEqual(str(cm.exception),
                         f"Bu projede başka bir jev süreci çalışıyor (PID {LIVE_PID}, koşu 20260101-000000-baska).")
        self.assertEqual(gitops.current_branch(self.project), branch)  # dal açılmadı
        self.assertFalse((jev_dir(self.project) / "runs").exists())
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["pid"], LIVE_PID)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.run_ = _ortak.make_run()
        self.lock = jev_dir(self.run_.project) / "lock"
        self.addCleanup(self.run_.bus.close)  # close() da kapatır; ikinci kez kapatmak zararsız

    def test_open_and_close(self):
        run = self.run_
        run.open()
        info = json.loads(self.lock.read_text(encoding="utf-8"))
        self.assertEqual((info["pid"], info["run_id"]), (os.getpid(), run.run_id))
        self.assertTrue(run._watcher.is_alive())
        run.state["ui_url"] = "http://127.0.0.1:1"
        run.close()
        run._watcher.join(5)
        self.assertFalse(run._watcher.is_alive())
        self.assertFalse(self.lock.exists())
        self.assertIsNone(load_state(run.rdir)["ui_url"])  # kapanmış sürecin ofis adresi kayıtta kalmaz

    def test_open_refuses_a_busy_project(self):
        run = self.run_
        atomic_write_json(self.lock, {"pid": LIVE_PID, "run_id": "20260101-000000-baska"})
        with mock.patch("jev.state.pid_alive", side_effect=lambda pid: pid == LIVE_PID):
            with self.assertRaises(SetupError) as cm:
                run.open()
        self.assertEqual(str(cm.exception),
                         f"Bu projede başka bir jev süreci çalışıyor (PID {LIVE_PID}, koşu 20260101-000000-baska).")
        self.assertIsNone(run._watcher)
        self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["pid"], LIVE_PID)

    def test_office_failure_is_not_fatal(self):
        run = self.run_
        run.ui = True
        with mock.patch("jev.office.server.OfficeServer", side_effect=OSError("port dolu")), \
                mock.patch("webbrowser.open") as browser:
            run.open()
            try:
                self.assertEqual(run.term.lines[-1], "! Ofis arayüzü açılamadı: port dolu")
                self.assertEqual((run.server, run.url), (None, None))
                browser.assert_not_called()
            finally:
                run.close()
        self.assertFalse(self.lock.exists())


class _RunTest(unittest.TestCase):
    """Sınıf başına planı yazılmış bir kuru koşu (aşama executing, T01–T06 bekliyor). Her test kaydı, git ağacını
    ve koşucunun komut durumunu geri yükler."""

    @classmethod
    def setUpClass(cls):
        cls.run_ = _ortak.planned_run()
        cls.base = copy.deepcopy(cls.run_.state)
        cls.base_head = gitops.head(cls.run_.project)
        cls.gitignore = (cls.run_.project / ".gitignore").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.run_.bus.close()

    def setUp(self):
        run = self.run_
        tty = mock.patch.object(runner_mod, "_stdin_is_tty", return_value=False)
        tty.start()
        self.addCleanup(tty.stop)
        gitops.rollback(run.project, self.base_head, self.base["branch"])
        run.state.clear()  # aynı sözlük: ağ geçidi de aynı kaydı görür
        run.state.update(copy.deepcopy(self.base))
        run.save()
        for p in run.rdir.glob("report*"):
            p.unlink()
        run.cancel.clear()
        while not run.controls.empty():
            run.controls.get_nowait()
        run._pending = run._input_started = run._stdin_eof = False
        run.server, run.url, run.exit_after, run.verbose = None, None, True, False
        run.term.lines.clear()
        self.seq = run.bus.last_seq

    def events(self, type_=None):
        return [e for e in self.run_.bus.since(self.seq) if type_ is None or e["type"] == type_]

    def task(self, tid):
        return next(t for t in self.run_.state["tasks"] if t["id"] == tid)

    def half_done(self, tid="T01", status="running"):
        """Görev yarıda: checkpoint alınmış, ağaçta commit edilmemiş yeni ve değişmiş dosya var."""
        p = self.run_.project
        t = self.task(tid)
        t.update(status=status, agent="sonnet", checkpoint=gitops.head(p), keep_changes=True)
        (p / "yarim.py").write_text("print('yarım')\n", encoding="utf-8")
        (p / ".gitignore").write_text("bozuldu\n", encoding="utf-8")
        return t

    def assert_rolled_back(self):
        p = self.run_.project
        self.assertFalse((p / "yarim.py").exists())
        self.assertEqual((p / ".gitignore").read_text(encoding="utf-8"), self.gitignore)
        self.assertTrue(gitops.is_clean(p))

    def write_report(self, round_, verdict, *, gaps=(), md="# Rapor\n"):
        run, name = self.run_, report_name(round_)
        atomic_write_json(run.rdir / f"{name}.json", {"verdict": verdict, "summary": "özet", "gaps": list(gaps),
                                                      "criteria": [], "risks": [], "next_steps": []})
        atomic_write_text(run.rdir / f"{name}.md", md)
        run.state["reports"].append({"round": round_, "file": f"{name}.md", "verdict": verdict,
                                     "summary": "özet", "ts": iso()})

    def interactive(self):
        """Konsol varmış gibi: komutlar kuyruktan okunur (okuyucu iş parçacığı başlatılmaz)."""
        run = self.run_
        run.exit_after, run._input_started = False, True
        tty = mock.patch.object(runner_mod, "_stdin_is_tty", return_value=True)
        tty.start()
        self.addCleanup(tty.stop)

    def put(self, *cmds):
        q = self.run_.controls
        while not q.empty():  # önceki oturumdan kalan yedek eof okunmasın
            q.get_nowait()
        for c in cmds:
            q.put(c if isinstance(c, tuple) else (c, "", "terminal"))
        q.put(("eof", "", "terminal"))  # yedek: beklenmedik bir yolda test takılmasın


class PhaseTests(_RunTest):
    def test_set_phase(self):
        run, st = self.run_, self.run_.state
        st.update(phase_since=iso(now() - dt.timedelta(seconds=90)), work_s=10.0)
        run.set_phase("reviewing", reason="deneme")
        self.assertAlmostEqual(st["work_s"], 100.0, delta=2)  # uygulamada geçen 90 sn eklendi
        self.assertEqual(load_state(run.rdir)["phase"], "reviewing")
        [ev] = self.events("run.phase")
        self.assertEqual(ev["data"], {"phase": "reviewing", "round": 1, "work_s": st["work_s"], "reason": "deneme"})
        self.assertEqual(index_all()[run.run_id]["phase"], "reviewing")
        run.set_phase("reported")
        worked = st["work_s"]
        st["phase_since"] = iso(now() - dt.timedelta(hours=5))  # rapor, onay ve duraklama beklemesi sayılmaz
        run.set_phase("fixing")
        self.assertEqual(st["work_s"], worked)

    def test_drive_stops_at_resting_phases(self):
        for ph in ("reported", "paused", "aborted"):
            self.run_.state["phase"] = ph
            self.assertEqual(self.run_.drive(), ph)
        self.assertEqual(self.events(), [])

    def test_drive_walks_the_phases(self):
        with mock.patch("jev.phases.execute.run_execute", side_effect=lambda r: r.set_phase("reviewing")), \
                mock.patch("jev.phases.review.run_review", side_effect=lambda r: r.set_phase("reported")):
            self.assertEqual(self.run_.drive(), "reported")
        self.assertEqual([e["data"]["phase"] for e in self.events("run.phase")], ["reviewing", "reported"])

    def test_drive_pause_abort_and_ctrl_c(self):
        run, st = self.run_, self.run_.state
        until = now() + dt.timedelta(hours=2)
        with mock.patch("jev.phases.execute.run_execute", side_effect=RunPaused("Kota doldu (opus)", until, "kota")):
            self.assertEqual(run.drive(), "paused")
        self.assertEqual((st["phase"], st["resume_phase"], st["pause_kind"], st["paused_reason"], st["quota_until"]),
                         ("paused", "executing", "kota", "Kota doldu (opus)", iso(until)))
        st["phase"] = "executing"
        with mock.patch("jev.phases.execute.run_execute", side_effect=RunAborted("Kullanıcı planı reddetti.")):
            self.assertEqual(run.drive(), "aborted")
        self.assertEqual((st["phase"], st["paused_reason"]), ("aborted", "Kullanıcı planı reddetti."))
        self.assertIn("✗ Koşu iptal edildi: Kullanıcı planı reddetti.", run.term.lines)
        st["phase"] = "executing"
        with mock.patch("jev.phases.execute.run_execute", side_effect=KeyboardInterrupt):
            self.assertEqual(run.drive(), "interrupted")
        self.assertEqual((st["phase"], st["pause_kind"], st["resume_phase"]), ("paused", "kullanici", "executing"))
        self.assertTrue(run.cancel.is_set())

    def test_pause_rolls_back_half_done_work(self):
        run, st = self.run_, self.run_.state
        self.half_done("T01", "running")
        self.task("T02")["status"] = "verifying"
        self.task("T03").update(status="needs_decision", agent="luna")
        self.task("T04")["status"] = "done"
        self.task("T05")["status"] = "waiting_quota"
        until = now() + dt.timedelta(hours=3)
        with mock.patch.object(run, "resume_cmd", return_value="jev devam"):
            run.pause("Opus kotası doldu.", until, "kota")
        self.assert_rolled_back()
        self.assertEqual([t["status"] for t in st["tasks"]], ["ready", "ready", "ready", "done", "waiting_quota",
                                                               "pending"])
        self.assertEqual([(t["agent"], t["keep_changes"]) for t in st["tasks"][:3]], [(None, False)] * 3)
        self.assertEqual((st["phase"], st["resume_phase"], st["pause_kind"], st["quota_until"]),
                         ("paused", "executing", "kota", iso(until)))
        self.assertEqual([e["data"]["task_id"] for e in self.events("task.update")], ["T01", "T02", "T03"])
        [ev] = self.events("run.phase")
        self.assertEqual(ev["data"], {"phase": "paused", "round": 1, "work_s": st["work_s"],
                                      "reason": "Opus kotası doldu.", "until": iso(until), "kind": "kota"})
        self.assertEqual(load_state(run.rdir)["phase"], "paused")
        self.assertEqual(run.term.lines[-2:], ["! Koşu duraklatıldı: Opus kotası doldu.", "Sürdürmek için: `jev devam`"])
        run.url = "http://ofis"
        with mock.patch.object(run, "resume_cmd", return_value="jev devam"):
            run.pause("İkinci duraklama.")  # zaten duraklatılmışken kaldığı aşama korunur
        self.assertEqual((st["resume_phase"], st["pause_kind"], st["quota_until"]), ("executing", "hata", None))
        self.assertEqual(run.term.lines[-1], "Sürdürmek için: `jev devam` (ya da arayüzde Devam: http://ofis)")

    def test_resume_cmd(self):
        run = self.run_
        with mock.patch.object(runner_mod, "find_run", return_value=(run.project, run.run_id)) as find:
            self.assertEqual(run.resume_cmd(), "jev devam")  # kimliksiz `jev devam` bu koşuyu bulur
        find.assert_called_once_with(None, mock.ANY)
        with mock.patch.object(runner_mod, "find_run", return_value=(run.project, "20260101-000000-baska")):
            self.assertEqual(run.resume_cmd(), f"jev devam {run.run_id}")
        with mock.patch.object(runner_mod, "find_run", side_effect=FileNotFoundError("Hiç koşu bulunamadı.")):
            self.assertEqual(run.resume_cmd(), f"jev devam {run.run_id}")

    def test_ctrl_c(self):
        run, st = self.run_, self.run_.state
        self.half_done("T02", "verifying")
        before = signal.getsignal(signal.SIGINT)
        with mock.patch.object(run, "resume_cmd", return_value="jev devam X"):
            run.interrupted()
        self.assertIs(signal.getsignal(signal.SIGINT), before)  # ikinci Ctrl+C koruması kaldırıldı
        self.assertTrue(run.cancel.is_set())  # çalışan ajan süreçleri durdurulur
        self.assert_rolled_back()
        self.assertEqual((self.task("T02")["status"], self.task("T02")["agent"]), ("ready", None))
        self.assertEqual((st["phase"], st["pause_kind"], st["resume_phase"], st["paused_reason"], st["quota_until"]),
                         ("paused", "kullanici", "executing", "Ctrl+C ile durduruldu.", None))
        idle = self.events("agent.state")
        self.assertEqual(sorted(e["data"]["agent"] for e in idle), sorted([*run.cfg.agents, "jev"]))
        self.assertTrue(all(e["data"]["state"] == "idle" and e["data"]["phase"] == "paused" for e in idle))
        self.assertEqual(run.term.lines[-3:], ["", "! Durduruldu. Yarım kalan görevin değişiklikleri geri alındı.",
                                               "Kaldığı yerden sürdürmek için: `jev devam X`"])

    def test_ctrl_c_survives_a_failed_rollback(self):
        run = self.run_
        self.half_done("T01")
        with mock.patch.object(gitops, "rollback", side_effect=gitops.GitError("index.lock var")):
            run.interrupted()
        self.assertIn("✗ Geri alma başarısız: index.lock var", run.term.lines)
        self.assertEqual((run.state["phase"], self.task("T01")["status"]), ("paused", "ready"))


class ResumeTests(_RunTest):
    def test_aborted_and_reported(self):
        run, st = self.run_, self.run_.state
        st["phase"] = "aborted"
        self.assertFalse(run.prepare_resume())
        self.assertEqual(run.term.lines, ["✗ Bu koşu iptal edilmiş; devam ettirilemez. Yeni bir koşu başlat."])
        st["phase"] = "reported"
        self.assertTrue(run.prepare_resume())
        self.assertEqual(run.term.lines[-1], "Koşu rapor aşamasında. Düzeltme için: `düzelt [not]`.")
        self.assertEqual((st["phase"], self.events()), ("reported", []))

    def test_resumes_where_it_stopped(self):
        run, st = self.run_, self.run_.state
        self.half_done("T01", "running")
        self.task("T02")["status"] = "waiting_quota"
        st.update(phase="paused", resume_phase="reviewing", pause_kind="kota", paused_reason="Kota doldu.",
                  quota_until=iso(now()))
        self.assertTrue(run.prepare_resume())
        self.assert_rolled_back()
        self.assertEqual([self.task(i)["status"] for i in ("T01", "T02")], ["ready", "ready"])
        self.assertEqual((st["phase"], st["resume_phase"], st["pause_kind"], st["paused_reason"], st["quota_until"]),
                         ("reviewing", None, None, None, None))
        self.assertEqual(load_state(run.rdir)["phase"], "reviewing")
        self.assertIn("T01 yarım kalmıştı; değişiklikleri geri alındı, görev yeniden sırada.", run.term.lines)
        self.assertEqual(run.term.lines[-1], "Koşu sürdürülüyor: Son kontrol.")
        self.assertNotIn("brain_limit_extra", st)

    def test_default_phase_and_brain_limit(self):
        run, st = self.run_, self.run_.state
        for n in (1, 2):
            st.update(phase="paused", resume_phase=None, pause_kind="beyin_siniri")
            self.assertTrue(run.prepare_resume())
            self.assertEqual((st["phase"], st["brain_limit_extra"]), ("executing", n * BRAIN_LIMIT_EXTRA))

    def test_switches_back_to_the_run_branch(self):
        run, st = self.run_, self.run_.state
        gitops.git(["checkout", "-q", st["origin_branch"]], run.project)
        st.update(phase="paused", resume_phase="executing")
        self.assertTrue(run.prepare_resume())
        self.assertEqual(gitops.current_branch(run.project), st["branch"])
        self.assertIn(f"Koşu dalına geçildi: {st['branch']}", run.term.lines)

    def test_keeps_uncommitted_work_on_another_branch(self):
        run, st = self.run_, self.run_.state
        origin = st["origin_branch"]
        gitops.git(["checkout", "-q", origin], run.project)
        (run.project / "kullanici.txt").write_text("kaydedilmemiş iş\n", encoding="utf-8")
        st.update(phase="paused", resume_phase="executing")
        before = copy.deepcopy(st)
        with self.assertRaises(SetupError) as cm:
            run.prepare_resume()
        self.assertTrue(str(cm.exception).startswith(f"Proje şu an '{origin}' dalında ve kaydedilmemiş değişiklikler "
                                                     "var."), "hata iletisi beklenen biçimde değil")
        self.assertEqual(st, before)
        self.assertEqual(gitops.current_branch(run.project), origin)
        self.assertTrue((run.project / "kullanici.txt").exists())  # kullanıcının işine dokunulmaz

    def test_warns_about_leftover_changes(self):
        run, st = self.run_, self.run_.state
        (run.project / "artik.txt").write_text("x\n", encoding="utf-8")
        st.update(phase="paused", resume_phase="executing")
        self.assertTrue(run.prepare_resume())
        self.assertIn("! Çalışma ağacında kaydedilmemiş değişiklikler var; bir sonraki görevin commit'ine girecek.",
                      run.term.lines)
        self.assertTrue((run.project / "artik.txt").exists())


class CrashTests(_RunTest):
    def test_active_phase_becomes_paused(self):
        run, st = self.run_, self.run_.state
        st.update(phase_since=iso(now() - dt.timedelta(seconds=600)), work_s=5.0)
        run._last_seen = iso(now() - dt.timedelta(seconds=60))
        run.mark_crashed()
        self.assertEqual((st["phase"], st["pause_kind"], st["resume_phase"], st["paused_reason"]),
                         ("paused", "hata", "executing", "Önceki jev süreci beklenmedik şekilde kapandı."))
        # süreç son yaşam belirtisinden sonra çalışmadı: kapalı kalınan son 60 sn çalışma süresine eklenmez
        self.assertAlmostEqual(st["work_s"], 545.0, delta=2)
        self.assertEqual(load_state(run.rdir)["phase"], "paused")
        self.assertEqual(len(self.events("agent.state")), len(run.cfg.agents) + 1)  # ofiste "çalışıyor" kalmaz

    def test_other_phases_are_left_alone(self):
        run, st = self.run_, self.run_.state
        for ph in ("reported", "paused", "aborted"):
            st["phase"] = ph
            run.mark_crashed()
            self.assertEqual(st["phase"], ph)
        self.assertEqual(self.events(), [])


class CommandTests(_RunTest):
    def test_commands_during_execution(self):
        run = self.run_
        run._pending = True
        for item in (("durum", "", "terminal"), ("yardim", "", "terminal"), ("onayla", "", "arayuz"),
                     ("eof", "", "terminal")):
            run.controls.put(item)
        run.poll_commands()
        lines = run.term.lines
        self.assertIn(header(f"Koşu {run.run_id}"), lines)
        self.assertIn(runner_mod.HELP, lines)
        self.assertEqual(lines[-1], "! Koşu sürüyor; şu an yalnızca `durum` ve `çıkış` kullanılabilir.")
        self.assertFalse(run._pending)
        run.controls.put(("cikis", "", "terminal"))
        with self.assertRaises(RunPaused) as cm:
            run.poll_commands()
        self.assertEqual((cm.exception.reason, cm.exception.kind), ("Kullanıcı çıkış istedi.", "kullanici"))

    def test_next_command(self):
        run = self.run_
        self.assertEqual(run.next_command(), ("cikis", ""))  # komut gelebilecek kanal yok: beklemeden çıkar
        run._input_started = True
        for item in (("eof", "", "terminal"), ("durum", "", "terminal")):  # eof yok sayılırsa "durum" döner, test takılmaz
            run.controls.put(item)
        self.assertEqual(run.next_command(), ("cikis", ""))  # konsol kapandı
        self.assertEqual(run.controls.get_nowait()[0], "durum")
        run.server = object()  # ofis açıkken konsolun kapanması beklemeyi bitirmez
        run._pending = True
        for item in (("eof", "", "terminal"), ("duzelt", "eksik testler", "arayuz")):
            run.controls.put(item)
        self.assertEqual(run.next_command(), ("duzelt", "eksik testler"))
        self.assertFalse(run._pending)
        self.assertEqual(run.term.lines[-1], "Arayüzden komut: duzelt (eksik testler)")

    def test_exit_codes_without_console(self):
        run, st = self.run_, self.run_.state
        for exit_after in (True, False):  # konsol ve ofis yoksa --bitince-cik olmasa da komut beklenmez
            run.exit_after = exit_after
            for ph, code in (("reported", 0), ("paused", 3), ("aborted", 4)):
                with self.subTest(exit_after=exit_after, phase=ph):
                    st["phase"] = ph
                    self.assertEqual(run.session(), code)
                    self.assertEqual(run.session(drive_first=False), code)
        with mock.patch.object(run, "drive", return_value="interrupted"):
            self.assertEqual(run.session(), 130)
        self.assertEqual(run.term.lines, [])
        self.assertFalse(run._input_started)

    def test_interactive_session_after_the_report(self):
        run, st = self.run_, self.run_.state
        self.interactive()
        st["phase"] = "reported"
        self.write_report(1, "basarili", md="# Rapor\n\nHer şey yolunda.")
        self.put("durum", "rapor", "devam", "onayla", "?sil", "yardim", "duzelt", "cikis")
        self.assertEqual(run.session(drive_first=False), 0)
        lines = run.term.lines
        for want in (header(f"Koşu {run.run_id}"), "# Rapor\n\nHer şey yolunda.", "Duraklatılmış bir koşu yok.",
                     "! Onay bekleyen bir plan yok.", f"! Bilinmeyen komut: sil. {runner_mod.HELP}", runner_mod.HELP,
                     "✓ Düzeltilecek eksik yok; değişiklik istiyorsan ne istediğini notla birlikte ver.",
                     f"Görüşürüz. Koşu kaydedildi; tekrar açmak için: jev ofis {run.run_id}"):
            self.assertIn(want, lines)
        self.assertEqual(lines.count("Komut bekleniyor: düzelt [not] · rapor · durum · ofis · çıkış"), 8)
        self.assertEqual((st["phase"], st["round"]), ("reported", 1))  # eksik yok ve not yok: tur açılmadı

    def test_interactive_fix_and_resume_drive_the_phases(self):
        run, st = self.run_, self.run_.state
        self.interactive()
        st["phase"] = "reported"
        self.write_report(1, "basarili")
        drives = []

        def fake_drive():
            drives.append(st["phase"])
            run.set_phase("reported")
            return "reported"

        self.put(("duzelt", "eksik testler", "arayuz"), "cikis")
        with mock.patch.object(run, "drive", side_effect=fake_drive):
            self.assertEqual(run.session(drive_first=False), 0)
        self.assertEqual((drives, st["round"], st["fix_note"]), (["fixing"], 2, "eksik testler"))
        st.update(phase="paused", resume_phase="executing", pause_kind="kota")
        run.cancel.set()  # önceki Ctrl+C'den kalan iptal işareti
        self.put("devam", "cikis")
        with mock.patch.object(run, "drive", side_effect=fake_drive):
            self.assertEqual(run.session(drive_first=False), 0)
        self.assertEqual(drives, ["fixing", "executing"])
        self.assertFalse(run.cancel.is_set())

    def test_interactive_exit_codes(self):
        run, st = self.run_, self.run_.state
        self.interactive()
        for ph, code in (("paused", 3), ("aborted", 4)):
            st["phase"] = ph
            self.put("cikis")
            self.assertEqual(run.session(drive_first=False), code)
        self.assertEqual(run.term.lines.count("Komut bekleniyor: devam · durum · ofis · çıkış"), 1)

    def test_events_are_echoed_to_the_terminal(self):
        run = self.run_
        for level, prefix in (("info", ""), ("ok", "✓ "), ("warn", "! "), ("error", "✗ "), ("bilinmeyen", "")):
            run.log(level, f"{level} satırı")
            self.assertEqual(run.term.lines[-1], f"{prefix}{level} satırı")
        run.emit("guard.blocked", {"agent": "sonnet", "reason": "Proje dışına yazma", "category_tr": "Dosya sistemi"})
        self.assertEqual(run.term.lines[-1], "! Koruma: Proje dışına yazma — Sonnet (Dosya sistemi; engellendi)")
        until = now() + dt.timedelta(hours=1)
        run.emit("quota", {"agent": "opus", "until": iso(until)})
        self.assertEqual(run.term.lines[-1], f"! Kota: Opus ~{ek(local_hhmm(until), 'e')} kadar soğumada.")
        run.emit("quota", {"agent": "opus", "until": iso(until), "shared_from": "sonnet"})
        self.assertEqual(run.term.lines[-1], f"! Kota: Opus ~{ek(local_hhmm(until), 'e')} kadar soğumada "
                                             "(ortak kota; Sonnet çağrısında doldu).")
        n = len(run.term.lines)
        run.emit("agent.activity", {"agent": "luna", "kind": "komut", "text": "py -m unittest"})
        self.assertEqual(len(run.term.lines), n)  # ajan ayrıntısı yalnızca --ayrintili ile
        run.verbose = True
        run.emit("agent.activity", {"agent": "luna", "kind": "komut", "text": "py -m unittest"})
        self.assertEqual(run.term.lines[-1], "    Luna · komut: py -m unittest")

    def test_redirected_input_is_not_a_console(self):
        for stdin in (io.StringIO("durum\n"), None):  # setUp'taki yakalama değil, gerçek işlev
            with mock.patch.object(sys, "stdin", stdin):
                self.assertFalse(REAL_STDIN_IS_TTY())


class FixTests(_RunTest):
    def test_nothing_to_fix(self):
        clean = {"verdict": "basarili", "gaps": []}
        cases = [((None, ""), False), ((clean, ""), True), ((clean, "   "), True), ((clean, None), True),
                 ((clean, "not"), False), (({"verdict": "basarili", "gaps": [{"id": "G1"}]}, ""), False),
                 (({"verdict": "kismen", "gaps": []}, ""), False)]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertIs(nothing_to_fix(*args), want)

    def test_start_fix(self):
        run, st = self.run_, self.run_.state
        self.assertFalse(start_fix(run, "not"))  # uygulama sürerken
        self.assertEqual(run.term.lines[-1], "! Düzeltme yalnızca rapordan sonra başlatılabilir.")
        st["phase"] = "reported"
        self.assertFalse(start_fix(run, "not"))
        self.assertEqual(run.term.lines[-1], "! Rapor bulunamadı.")
        self.write_report(1, "basarili")
        self.assertFalse(start_fix(run, "  "))
        self.assertIn("✓ Düzeltilecek eksik yok; değişiklik istiyorsan ne istediğini notla birlikte ver.",
                      run.term.lines)
        self.assertEqual((st["phase"], st["round"]), ("reported", 1))
        self.assertTrue(start_fix(run, "  Silmeden önce onay sor "))
        self.assertEqual((st["phase"], st["round"], st["fix_note"]), ("fixing", 2, "Silmeden önce onay sor"))
        self.assertEqual(load_state(run.rdir)["round"], 2)
        self.assertIn("Düzeltme turu başlıyor (tur 2; not: Silmeden önce onay sor).", run.term.lines)

    def test_gaps_open_a_round_without_a_note(self):
        run, st = self.run_, self.run_.state
        st["phase"] = "reported"
        self.write_report(1, "kismen", gaps=[{"id": "G1", "criteria": ["C2"], "description": "Silme onaysız"}])
        self.assertTrue(start_fix(run, None))
        self.assertEqual((st["phase"], st["round"], st["fix_note"]), ("fixing", 2, ""))
        self.assertIn("Düzeltme turu başlıyor (tur 2).", run.term.lines)

    def test_fix_hint(self):
        run = self.run_
        clean, gaps = {"verdict": "basarili", "gaps": []}, {"verdict": "kismen", "gaps": [{"id": "G1"}]}
        self.assertEqual(fix_hint(run, clean), f'Eksik yok; yine de değişiklik istersen: jev duzelt {run.run_id} "not"')
        self.assertEqual(fix_hint(run, gaps), f'Düzeltmek için: jev duzelt {run.run_id} "not"')
        with mock.patch.object(run, "interactive", return_value=True):
            self.assertEqual(fix_hint(run, gaps), "Düzeltmek için: `düzelt [not]` yaz.")
            run.url = "http://ofis"
            self.assertEqual(fix_hint(run, clean), "Eksik yok; yine de değişiklik istersen: `düzelt [not]` yaz ya da "
                                                   "ofisteki Düzelt düğmesine bas.")


class GuardWatcherTests(unittest.TestCase):
    REC = {"ajan": "sonnet", "gorev": "T02", "arac": "Bash", "reason": "Proje dışında silme", "category": "silme",
           "category_tr": "Toplu silme", "serious": 1, "eylem": "engellendi", "girdi": "Remove-Item " + "x" * 400}

    def setUp(self):
        self.path = _ortak.temp_dir("koruma") / "guard.jsonl"
        self.got = []

    def emit(self, type_, data):
        self.got.append((type_, data))

    def append(self, raw: bytes):
        with self.path.open("ab") as f:
            f.write(raw)

    def record(self, **over) -> bytes:
        return (json.dumps({**self.REC, **over}, ensure_ascii=False) + "\n").encode("utf-8")

    def test_only_new_records_are_sent(self):
        self.append(self.record(reason="eski kayıt"))
        w = GuardWatcher(self.path, self.emit)
        w.poll()
        self.assertEqual(self.got, [])  # sürdürülen koşuda eski engeller yeniden gösterilmez
        self.append(self.record())
        w.poll()
        w.poll()
        [(type_, data)] = self.got
        self.assertEqual(type_, "guard.blocked")
        self.assertEqual(data, {"agent": "sonnet", "task_id": "T02", "tool": "Bash", "reason": "Proje dışında silme",
                                "category": "silme", "category_tr": "Toplu silme", "serious": True,
                                "action": "engellendi", "input": shorten(self.REC["girdi"], 200)})
        self.assertEqual(len(data["input"]), 200)

    def test_partial_lines_and_junk(self):
        w = GuardWatcher(self.path, self.emit)  # dosya henüz yok
        w.poll()
        raw = self.record(reason="Yıkıcı komut: ş")
        cut = raw.index("ş".encode("utf-8")) + 1  # çok baytlı harfin ortasından bölünmüş yazım
        self.append(raw[:cut])
        w.poll()
        self.assertEqual(self.got, [])
        self.append(raw[cut:] + b"{bozuk\n[1, 2]\n" + json.dumps({"ajan": "luna"}).encode() + b"\n\n")
        w.poll()
        self.assertEqual([d["reason"] for _, d in self.got], ["Yıkıcı komut: ş"])

    def test_truncated_file_is_read_from_the_start(self):
        self.append(self.record() + self.record())
        w = GuardWatcher(self.path, self.emit)
        self.path.write_bytes(self.record(reason="yeni", girdi=""))
        w.poll()
        self.assertEqual([(d["reason"], d["input"]) for _, d in self.got], [("yeni", "")])

    def test_thread(self):
        w = GuardWatcher(self.path, self.emit)
        w.start()
        try:
            self.append(self.record())
            deadline = time.monotonic() + 5
            while not self.got and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            w.stop_event.set()
            w.join(5)
        self.assertEqual(len(self.got), 1)
        self.assertFalse(w.is_alive())


if __name__ == "__main__":
    unittest.main()
