"""Koşu durumu: kimlik, çalışma süresi, kilit, koşu dizini ve koşu bulma."""
import datetime as dt
import json
import os
import subprocess
import sys
import unittest

import _ortak
from jev.state import (LockError, ProjectLock, close_work, find_run, index_all, index_update, load_state,
                       new_run_id, new_state, run_dir, save_state, work_from_history, work_seconds)
from jev.util import iso, now

T0 = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.timezone.utc)


def at(sec):
    return iso(T0 + dt.timedelta(seconds=sec))


class StateTests(unittest.TestCase):
    def test_new_run_id(self):
        rid = new_run_id("Yapılacaklar listesi CLI tasarla")
        self.assertRegex(rid, r"^\d{8}-\d{6}-yapilacaklar-listesi-cli$")

    def test_new_state(self):
        st = new_state("r1", "istek", _ortak.ROOT, dry=True, speed=5.0, approval=True)
        self.assertEqual((st["phase"], st["round"], st["work_s"], st["branch"]), ("sizing", 1, 0.0, "jev/r1"))
        self.assertEqual(st["phase_since"], st["created"])
        self.assertTrue(st["dry_run"] and st["plan_approval"])
        self.assertEqual(st["provider_streak"], {"provider": None, "count": 0})
        self.assertEqual((st["tasks"], st["reports"], st["deviations"], st["call_seq"]), ([], [], [], 0))

    def test_close_work(self):
        st = {"phase": "executing", "phase_since": at(0), "work_s": 5.0}
        close_work(st, at(30))
        self.assertEqual(st["work_s"], 35.0)
        for phase in ("paused", "reported", "awaiting_approval", "aborted"):
            st = {"phase": phase, "phase_since": at(0), "work_s": 5.0}
            close_work(st, at(30))
            self.assertEqual(st["work_s"], 5.0, phase)
        st = {"phase": "planning", "phase_since": at(30), "work_s": 1.0}
        close_work(st, at(0))  # saat geri gitse de süre azalmaz
        close_work(st, None)
        self.assertEqual(st["work_s"], 1.0)

    def test_work_seconds(self):
        st = {"phase": "executing", "phase_since": iso(now() - dt.timedelta(seconds=10)), "work_s": 20.0}
        self.assertAlmostEqual(work_seconds(st), 30.0, delta=2)
        self.assertEqual(work_seconds(st, running=False), 20.0)
        st["phase"] = "reported"
        self.assertEqual(work_seconds(st), 20.0)

    def test_work_from_history(self):
        st = {"created": at(0)}
        hist = [{"type": "run.phase", "ts": at(10), "data": {"phase": "awaiting_approval"}},
                {"type": "log", "ts": at(50), "data": {"text": "x"}},
                {"type": "run.phase", "ts": at(100), "data": {"phase": "decomposing"}},
                {"type": "run.phase", "ts": at(110), "data": {"phase": "executing"}},
                {"type": "run.phase", "ts": at(130), "data": {"phase": "reviewing"}},
                {"type": "run.phase", "ts": at(138), "data": {"phase": "reported"}}]
        self.assertEqual(work_from_history(st, hist), (48.0, at(138)))
        self.assertEqual(work_from_history(st, []), (0.0, at(0)))

    def test_save_and_load(self):
        rdir = _ortak.temp_dir("durum")
        st = {"run_id": "r1", "request": "Türkçe istek: ığüşöç", "updated": "eski"}
        save_state(rdir, st)
        self.assertNotEqual(st["updated"], "eski")
        self.assertEqual(load_state(rdir)["request"], "Türkçe istek: ığüşöç")
        (rdir / "state.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            load_state(rdir)
        with self.assertRaises(FileNotFoundError):
            load_state(rdir / "yok")


class LockTests(unittest.TestCase):
    def setUp(self):
        self.project = _ortak.temp_dir("kilit")
        self.path = self.project / ".jev" / "lock"

    def write_lock(self, pid, run_id="baska"):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pid": pid, "run_id": run_id}), encoding="utf-8")

    def test_acquire_release(self):
        lock = ProjectLock(self.project, "r1")
        lock.acquire()
        info = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((info["pid"], info["run_id"], lock.held), (os.getpid(), "r1", True))
        lock.release()
        self.assertFalse(self.path.exists())
        self.assertFalse(lock.held)

    def test_stale_locks_are_taken_over(self):
        for content in (json.dumps({"pid": 99999999, "run_id": "eski"}), json.dumps({"pid": os.getpid()}),
                        "bozuk içerik", json.dumps({"pid": 0}), "[1, 2]", json.dumps({"pid": "abc"})):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(content, encoding="utf-8")
            lock = ProjectLock(self.project, "r2")
            lock.acquire()
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["run_id"], "r2", content)
            lock.release()

    def test_live_foreign_process_blocks(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            self.write_lock(proc.pid, "20260101-000000-baska")
            with self.assertRaises(LockError) as cm:
                ProjectLock(self.project, "r3").acquire()
            self.assertEqual(str(cm.exception), f"Bu projede başka bir jev süreci çalışıyor "
                                                f"(PID {proc.pid}, koşu 20260101-000000-baska).")
            self.assertTrue(self.path.exists())
        finally:
            proc.kill()
            proc.wait(10)

    def test_release_keeps_foreign_lock(self):
        lock = ProjectLock(self.project, "r4")
        lock.acquire()
        self.write_lock(99999999)
        lock.release()
        self.assertTrue(self.path.exists())


class IndexTests(unittest.TestCase):
    def setUp(self):
        _ortak.clear_index()
        self.addCleanup(_ortak.clear_index)
        self.root = _ortak.temp_dir("dizin")

    def make(self, project, rid, created=None, index=True):
        d = run_dir(project, rid)
        d.mkdir(parents=True)
        (d / "state.json").write_text("{}", encoding="utf-8")
        if index:
            index_update(rid, project=str(project), created=created or rid)
        return d

    def test_index_update_merges(self):
        index_update("r1", project="p", phase="planning")
        index_update("r1", phase="executing")
        rec = index_all()["r1"]
        self.assertEqual((rec["project"], rec["phase"]), ("p", "executing"))
        self.assertIn("updated", rec)

    def test_find_run(self):
        p1, p2, p3 = self.root / "p1", self.root / "p2", self.root / "p3"
        self.make(p1, "20260101-000000-a", "2026-01-01T00:00:00Z")
        self.make(p2, "20260102-000000-b", "2026-01-02T00:00:00Z")
        self.make(p3, "yerel-kosu", index=False)
        self.assertEqual(find_run("20260101-000000-a"), (p1, "20260101-000000-a"))
        self.assertEqual(find_run("20260102"), (p2, "20260102-000000-b"))  # tek kısmi eşleşme
        self.assertEqual(find_run("yerel-kosu", p3), (p3, "yerel-kosu"))
        with self.assertRaises(FileNotFoundError) as cm:
            find_run("2026")  # iki koşuya uyuyor
        self.assertEqual(str(cm.exception), "Koşu bulunamadı: 2026")
        # kimliksiz: bulunulan projenin en son koşusu, yoksa dizindeki en yeni koşu
        self.make(p1, "20260103-000000-c", "2026-01-03T00:00:00Z")
        self.assertEqual(find_run(None, p1), (p1, "20260103-000000-c"))
        self.assertEqual(find_run(None, p3), (p3, "yerel-kosu"))
        self.assertEqual(find_run(None, self.root), (p1, "20260103-000000-c"))
        self.assertEqual(find_run(None), (p1, "20260103-000000-c"))

    def test_find_run_ignores_deleted_runs(self):
        p1 = self.root / "p1"
        self.make(p1, "20260101-000000-a", "2026-01-01T00:00:00Z")
        index_update("20260105-000000-silindi", project=str(self.root / "yok"), created="2026-01-05T00:00:00Z")
        self.assertEqual(find_run(None), (p1, "20260101-000000-a"))

    def test_nothing_found(self):
        with self.assertRaises(FileNotFoundError) as cm:
            find_run(None, self.root)
        self.assertEqual(str(cm.exception), 'Hiç koşu bulunamadı. Yeni koşu için: jev "istek"')


if __name__ == "__main__":
    unittest.main()
