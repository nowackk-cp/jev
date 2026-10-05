"""Paralel çalışma: birbirini beklemeyen görevler aynı anda, her biri kendi git çalışma ağacında (şerit).

Birim testleri: ne zaman paralel çalışılır, aynı anda kaç görev, ajan seçimi (başka görevde çalışan ajan seçilmez),
şerit açma/kapama/geri alma/toplama, koruma kayıtlarının kendi görevine yazılması, kaydedilmemiş değişikliklerin
commit'i, koşucunun duraklatma/Ctrl+C/sürdürme/iptal temizliği.

Kuru koşular: paralel senaryosu (jev/mock/senaryo_paralel.json) T01 ile T02'yi aynı anda çalıştırır ve koşu dalına
ayrı birer commit olarak birleştirir; T03 ikisinden sonra proje klasöründe çalışır. İki görev aynı dosyaya yazınca
sonra biten görevin işi koşunun son hâline taşınır ve çakışmayı aynı ajan çözer (deneme hakkı eksilmez).
"""
import copy
import datetime as _dt
import os
import subprocess
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

import _ortak
from jev import gitops, lanes, routing
from jev.adapters.mock import MOCK_DIR, load_scenario
from jev.phases import execute, parallel
from jev.quota import QuotaBook
from jev.util import append_jsonl, now, read_jsonl

PARALEL = MOCK_DIR / "senaryo_paralel.json"
REQUEST = "Uzunluk ve sıcaklık birimleri arasında dönüşüm yapan bir komut satırı aracı yap"
LANE = {"path": "x", "branch": "b", "base": "c"}


def same_path(a, b) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def git(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


def tasks_state(*specs: dict) -> dict:
    return {"tasks": [{"id": f"T{i:02d}", **s} for i, s in enumerate(specs, 1)]}


def lane_branches(run) -> list[str]:
    return gitops.branches(run.project, lanes.run_branch(run) + lanes.LANE_INFIX)


# --- birim ---------------------------------------------------------------------------------------

class WantsLanesTests(unittest.TestCase):
    def test_two_ready_tasks(self):
        st = tasks_state({"status": "ready"}, {"status": "ready"}, {"status": "pending"})
        self.assertTrue(execute.wants_lanes(st, 2))
        self.assertFalse(execute.wants_lanes(st, 1))

    def test_single_ready_task_runs_in_project(self):
        st = tasks_state({"status": "ready"}, {"status": "pending"}, {"status": "done"})
        self.assertFalse(execute.wants_lanes(st, 3))

    def test_waiting_quota_counts(self):
        self.assertTrue(execute.wants_lanes(tasks_state({"status": "ready"}, {"status": "waiting_quota"}), 2))

    def test_open_lane_keeps_parallel_mode(self):
        # şeridi süren görev varken paralel döngü sürer (sınır 1 olsa bile: şerit birleşmeden kapanmaz)
        self.assertTrue(execute.wants_lanes(tasks_state({"status": "running", "lane": LANE}, {"status": "pending"}), 1))
        # biten görevin kaydında kalmış şerit sayılmaz
        self.assertFalse(execute.wants_lanes(tasks_state({"status": "done", "lane": LANE}, {"status": "ready"}), 2))

    def test_kept_changes_in_project_first(self):
        st = tasks_state({"status": "ready", "keep_changes": True}, {"status": "ready"})
        self.assertFalse(execute.wants_lanes(st, 2))


class ParallelLimitTests(unittest.TestCase):
    LEVELS = ("mini", "kucuk", "orta", "buyuk")

    @staticmethod
    def fake_run(cfg, level: str, scenario: dict, dry: bool = True):
        adapter = SimpleNamespace(scenario=scenario)
        return SimpleNamespace(cfg=cfg, state={"dry_run": dry, "scale": {"level": level}},
                               gw=SimpleNamespace(adapter=lambda name: adapter))

    def test_profiles(self):
        cfg = _ortak.make_config()
        self.assertEqual([cfg.parallel(x) for x in self.LEVELS], [1, 1, 8, 8])  # 4 ajan × 2
        self.assertEqual(cfg.parallel(None), 8)  # ölçeği olmayan eski koşu: büyük
        low = _ortak.make_config(paralel=1)  # genel ayar profillerin üst sınırı
        self.assertEqual([low.parallel(x) for x in self.LEVELS], [1, 1, 1, 1])

    def test_dry_run_scenario_limit(self):
        cfg = _ortak.make_config()
        limit = execute.parallel_limit
        self.assertEqual(limit(self.fake_run(cfg, "orta", {"paralel": 2})), 2)
        self.assertEqual(limit(self.fake_run(cfg, "buyuk", {"paralel": 2})), 2)
        self.assertEqual(limit(self.fake_run(cfg, "orta", {"paralel": 5})), 5)
        self.assertEqual(limit(self.fake_run(cfg, "orta", {"paralel": 12})), 8)  # genel ayar da sınır
        self.assertEqual(limit(self.fake_run(cfg, "buyuk", {})), 1)  # senaryoda yoksa sırayla (mevcut senaryolar)
        self.assertEqual(limit(self.fake_run(cfg, "orta", {}, dry=False)), 8)
        self.assertEqual(limit(self.fake_run(cfg, "buyuk", {}, dry=False)), 8)


class ChooseTests(unittest.TestCase):
    """Paralel dağıtımda ajan seçimi: başka görevde çalışan ajan seçilmez; uygun ajan meşgulse boşalması beklenir,
    hepsi soğumadaysa görev kotayı bekler."""

    def setUp(self):
        self.quota = QuotaBook(_ortak.temp_dir("kota") / "kota.json")
        self.run_ = SimpleNamespace(state={"scale": {"level": "orta"}, "tasks": []}, cfg=_ortak.make_config(),
                                    quota=self.quota, jev=None)
        self.task = {"id": "T04", "type": "test", "complexity": "S", "suggested_agent": "sonnet"}

    def cool(self, agent: str, minutes: int = 30) -> None:
        self.quota.mark(agent, now() + _dt.timedelta(minutes=minutes), "test")

    def choose(self, *busy: str):
        return parallel._choose(self.run_, self.task, set(busy))

    def test_candidates(self):
        self.assertEqual(routing.candidates(self.task, self.run_.cfg), ["sonnet", "sol"])

    def test_free_agent(self):
        self.assertEqual(self.choose().agent, "sonnet")
        self.assertEqual(self.choose("sonnet").agent, "sol")  # Sonnet başka görevde
        self.assertEqual(self.choose("luna").agent, "sonnet")

    def test_waits_for_busy_agent(self):
        self.assertIsNone(self.choose("sonnet", "sol"))  # ikisi de çalışıyor: biri boşalınca yeniden bakılır
        self.cool("sol")
        self.assertIsNone(self.choose("sonnet"))  # Sol soğumada, Sonnet çalışıyor: Sonnet beklenir (kota değil)

    def test_slots_from_config(self):
        cfg = self.run_.cfg
        self.assertEqual((cfg.slots("sonnet"), cfg.slots("sol")), (2, 2))  # her ajan aynı anda iki görev alabilir
        self.assertEqual(_ortak.make_config(ajanlar={"sol": {"es_zamanli": 1}}).slots("sol"), 1)

    def test_dispatch_fills_agent_slots(self):
        def busy_seen(*agents):
            seen = []
            pool = [SimpleNamespace(agent=a, task={"outputs": []}) for a in agents]
            run = SimpleNamespace(state={"tasks": [dict(self.task, status="ready")]}, cfg=self.run_.cfg)
            with mock.patch.object(parallel, "_choose", lambda r, t, busy: seen.append(busy)):
                parallel._dispatch(run, pool, 5)
            return seen[0]
        self.assertEqual(busy_seen("sonnet"), set())  # Sonnet'in ikinci yeri boş
        self.assertEqual(busy_seen("sonnet", "sonnet", "sol"), {"sonnet"})  # Sol'un ikinci yeri boş
        self.assertEqual(busy_seen("sonnet", "sonnet", "sol", "sol"), {"sonnet", "sol"})

    def test_all_cooling_waits_for_quota(self):
        self.cool("sonnet", 30)
        self.cool("sol", 10)
        ch = self.choose("luna")
        self.assertIsNotNone(ch)
        self.assertIsNone(ch.agent)
        self.assertEqual(ch.wait_until, self.quota.until("sol"))

    def test_forced_agent(self):
        self.task["forced"] = {"agent": "sol", "effort": "high"}
        ch = self.choose()
        self.assertEqual((ch.agent, ch.effort, ch.reason), ("sol", "high", "Jev'in kararı"))
        self.assertIsNone(self.choose("sol"))  # Jev'in seçtiği ajan başka görevde: bitince bu göreve geçer
        self.cool("sol")
        ch = self.choose("sol")  # soğumadaki ajan beklenmez
        self.assertEqual(ch.agent, "sonnet")
        self.assertIn("soğumada", ch.reason)


class GuardHitsTests(unittest.TestCase):
    def test_lane_task_sees_only_its_own(self):
        rdir = _ortak.temp_dir("koruma")
        for gorev, cat in (("T00", "eski"), ("T01", "a"), ("T02", "b")):
            append_jsonl(rdir / "guard.jsonl", {"gorev": gorev, "category": cat})
        run = SimpleNamespace(rdir=rdir)
        cats = lambda t: [h["category"] for h in execute._guard_hits(run, t, 1)]  # noqa: E731
        self.assertEqual(cats({"id": "T01"}), ["a", "b"])  # sırayla çalışmada denemeden bu yana hepsi
        self.assertEqual(cats({"id": "T01", "lane": LANE}), ["a"])  # paralelde yalnızca kendi ajanınınkiler


class _PlannedRun(unittest.TestCase):
    """Planı yazılmış ve bölünmüş kuru koşu (görevler T01–T06, varsayılan senaryo)."""

    def setUp(self):
        self.run_ = _ortak.planned_run()
        self.addCleanup(self.run_.bus.close)
        self.st, self.p = self.run_.state, self.run_.project
        self.root = gitops.worktree_root(self.p)

    def t(self, tid: str) -> dict:
        return routing.by_id(self.st)[tid]

    def running(self, tid: str) -> tuple[dict, Path]:
        """Şeridinde yarım işi olan, çalışan görev."""
        t = self.t(tid)
        path = lanes.open_lane(self.run_, t)
        t.update(status="running", agent="sonnet", checkpoint=gitops.head(path))
        (path / "yarim.txt").write_text("yarım iş\n", encoding="utf-8")
        return t, path

    def assert_reset(self, t: dict, path: Path) -> None:
        self.assertFalse(path.exists())
        self.assertNotIn("lane", t)
        self.assertEqual((t["status"], t["agent"], t["keep_changes"]), ("ready", None, False))


class LaneTests(_PlannedRun):
    def test_open_and_close(self):
        t, head = self.t("T01"), gitops.head(self.p)
        path = lanes.open_lane(self.run_, t)
        lane = t["lane"]
        self.assertTrue(path.is_dir())
        self.assertTrue(same_path(path.parent, self.root))
        self.assertTrue(path.name.startswith(lanes.run_tag(self.st["run_id"]) + "-"))
        self.assertEqual(lane["branch"], self.st["branch"] + "-serit-T01")
        self.assertEqual((lane["path"], lane["base"]), (str(path), head))
        self.assertEqual(gitops.current_branch(path), lane["branch"])
        self.assertEqual((lanes.workdir(self.run_, t), lanes.branch(self.run_, t)), (path, lane["branch"]))
        t.update(checkpoint=head, keep_changes=True)
        lanes.close_lane(self.run_, t)
        self.assertFalse(path.exists())
        self.assertNotIn("lane", t)
        self.assertNotIn("checkpoint", t)  # checkpoint şeridin geçmişindeydi
        self.assertFalse(t["keep_changes"])
        self.assertEqual(lane_branches(self.run_), [])
        self.assertEqual((lanes.workdir(self.run_, t), lanes.branch(self.run_, t)), (self.p, self.st["branch"]))
        self.assertEqual(gitops.head(self.p), head)  # ana projeye dokunulmadı

    def test_kept_lane_reused(self):
        t = self.t("T01")
        path = lanes.open_lane(self.run_, t)
        (path / "yarim.txt").write_text("yarım iş\n", encoding="utf-8")
        t["keep_changes"] = True  # beyin değişiklikleri korumaya karar verdi
        self.assertEqual(lanes.open_lane(self.run_, t), path)
        self.assertTrue((path / "yarim.txt").exists())
        t["keep_changes"] = False  # korunmayan şerit koşu dalının son hâlinden yeniden açılır
        self.assertEqual(lanes.open_lane(self.run_, t), path)
        self.assertFalse((path / "yarim.txt").exists())
        lanes.close_lane(self.run_, t)

    def test_rollback_with_lane_leaves_project(self):
        t, path = self.running("T01")
        (self.p / "kullanici.txt").write_text("kullanıcının işi\n", encoding="utf-8")
        head = gitops.head(self.p)
        lanes.rollback(self.run_, t)
        self.assertFalse(path.exists())
        self.assertNotIn("lane", t)
        self.assertEqual(gitops.head(self.p), head)
        self.assertTrue((self.p / "kullanici.txt").exists())

    def test_rollback_without_lane(self):
        t = self.t("T01")
        t.update(checkpoint=gitops.checkpoint(self.p), keep_changes=True)
        (self.p / "yarim.txt").write_text("yarım iş\n", encoding="utf-8")
        lanes.rollback(self.run_, t)
        self.assertFalse((self.p / "yarim.txt").exists())
        self.assertFalse(t["keep_changes"])

    def test_sweep(self):
        kept, kept_path = self.running("T01")
        kept.update(status="ready", keep_changes=True)  # korunan şerit
        dropped, dropped_path = self.running("T02")
        base = gitops.head(self.p)
        stray = lanes.lane_path(self.run_, "T99")  # kaydı kalmış sahipsiz şerit
        gitops.worktree_add(self.p, stray, lanes.lane_branch(self.run_, "T99"), base)
        loose = lanes.lane_path(self.run_, "T97")  # git kaydı olmayan artık klasör
        loose.mkdir()
        (loose / "artik.txt").write_text("x\n", encoding="utf-8")
        foreign = self.root / "baska1-T01"  # başka bir koşunun şeridi
        gitops.worktree_add(self.p, foreign, "jev/baska-serit-T01", base)
        gitops.git(["branch", lanes.lane_branch(self.run_, "T98")], self.p)  # sahipsiz dal

        lanes.sweep(self.run_)
        self.assertTrue((kept_path / "yarim.txt").exists())
        self.assertIn("lane", kept)
        self.assertFalse(dropped_path.exists())
        self.assertNotIn("lane", dropped)
        self.assertFalse(stray.exists() or loose.exists())
        self.assertTrue(foreign.is_dir())
        self.assertEqual(lane_branches(self.run_), [kept["lane"]["branch"]])
        self.assertTrue(gitops.branch_exists(self.p, "jev/baska-serit-T01"))

        lanes.close_lane(self.run_, kept)
        gitops.worktree_remove(self.p, foreign, "jev/baska-serit-T01")
        lanes.sweep(self.run_)
        self.assertFalse(self.root.exists())  # boş kalan kök klasör kalkar
        self.assertEqual(len(gitops.worktree_paths(self.p)), 1)


class SaveMainTests(_PlannedRun):
    def test_commits_dirty_tree_only(self):
        execute.save_main(self.run_)
        head = gitops.head(self.p)
        execute.save_main(self.run_)
        self.assertEqual(gitops.head(self.p), head)  # temiz ağaçta commit yok
        (self.p / "elle.txt").write_text("kullanıcı yazdı\n", encoding="utf-8")
        execute.save_main(self.run_, "test")
        self.assertNotEqual(gitops.head(self.p), head)
        self.assertEqual(git(self.p, "log", "-1", "--format=%s"), "jev: kaydedilmemiş değişiklikler")
        self.assertTrue(gitops.is_clean(self.p))


class RunnerCleanupTests(_PlannedRun):
    """Duraklatma, Ctrl+C ve iptal yarım görevlerin şeritlerini kaldırır; sürdürmede korunan şerit kalır."""

    def test_pause(self):
        t, path = self.running("T01")
        self.run_.pause("test")
        self.assert_reset(t, path)
        self.assertEqual(self.st["phase"], "paused")
        self.assertEqual(lane_branches(self.run_), [])

    def test_interrupted(self):
        t, path = self.running("T01")
        self.run_.interrupted()
        self.assert_reset(t, path)
        self.assertTrue(self.run_.cancel.is_set())
        self.assertEqual(self.st["phase"], "paused")

    def test_resume_keeps_kept_lane(self):
        kept, kept_path = self.running("T01")
        kept.update(status="ready", agent=None, keep_changes=True)
        t, path = self.running("T02")
        self.assertTrue(self.run_.prepare_resume())
        self.assert_reset(t, path)
        self.assertTrue((kept_path / "yarim.txt").exists())
        self.assertTrue(kept["keep_changes"])
        self.assertEqual(lane_branches(self.run_), [kept["lane"]["branch"]])

    def test_abort(self):
        a, pa = self.running("T01")
        b, pb = self.running("T02")
        self.run_.abort("test")
        self.assertFalse(pa.exists() or pb.exists())
        self.assertNotIn("lane", a)
        self.assertNotIn("lane", b)
        self.assertFalse(self.root.exists())
        self.assertEqual(lane_branches(self.run_), [])
        self.assertEqual(self.st["phase"], "aborted")


# --- kuru koşular ---------------------------------------------------------------------------------

def run_scenario(scenario: dict):
    term = _ortak.QuietTerminal()
    run = _ortak.make_run(REQUEST, scenario=scenario, speed=1000.0, term=term)
    result = run.drive()
    run.bus.close()
    return run, term, result


def conflict_scenario() -> dict:
    """T01 ve T02 aynı dosyaya (ortak.txt) farklı içerik yazar: sonra biten görevin işi çakışır. İkinci girdiler
    çakışmayı çözer (iki satır birlikte)."""
    sc = copy.deepcopy(load_scenario(PARALEL))
    workers = sc["workers"]
    for tid in ("T01", "T02"):
        entry = next(w for w in workers if w["task"] == tid)
        test_step = entry["steps"][-1]
        entry["steps"].insert(-1, {"do": "write", "path": "ortak.txt", "content": f"{tid} yazdı\n", "delay": 0.5})
        workers.append({"task": tid, "outcome": "done", "summary": f"{tid}: çakışma çözüldü.", "steps": [
            {"do": "think", "text": "Çakışmayı çözüyor", "delay": 0.5},
            {"do": "write", "path": "ortak.txt", "content": "T01 yazdı\nT02 yazdı\n", "delay": 0.5},
            dict(test_step)]})
    return sc


class _NoLeftovers:
    def test_no_lane_leftovers(self):
        p = self.run_.project
        self.assertFalse(any("lane" in t for t in self.st["tasks"]))
        self.assertEqual(lane_branches(self.run_), [])
        self.assertEqual(len(gitops.worktree_paths(p)), 1)
        self.assertFalse(gitops.worktree_root(p).exists())
        self.assertEqual(gitops.current_branch(p), self.st["branch"])


@_ortak.slow
class ParallelRunTests(_NoLeftovers, unittest.TestCase):
    """Paralel senaryosu uçtan uca: T01 ve T02 aynı anda (her biri kendi şeridinde), T03 ikisinden sonra."""

    @classmethod
    def setUpClass(cls):
        cls.run_, cls.term, cls.result = run_scenario(load_scenario(PARALEL))
        cls.st = cls.run_.state

    def test_reported(self):
        self.assertEqual(self.result, "reported")
        self.assertEqual(self.st["scale"]["level"], "orta")
        self.assertEqual([(t["id"], t["status"]) for t in self.st["tasks"]],
                         [("T01", "done"), ("T02", "done"), ("T03", "done")])
        self.assertEqual([(r["verdict"], r["by"]) for r in self.st["reports"]], [("basarili", "sol")])
        for f in ("uzunluk.py", "sicaklik.py", "donustur.py", "test_donustur.py"):
            self.assertTrue((self.run_.project / f).exists(), f)

    def test_first_two_ran_together(self):
        starts = {tid: [ln for ln in self.term.lines if ln.startswith(f"▶ {tid} ")] for tid in ("T01", "T02", "T03")}
        self.assertTrue(all(len(v) == 1 for v in starts.values()), starts)
        self.assertIn("· paralel", starts["T01"][0])
        self.assertIn("· paralel", starts["T02"][0])
        self.assertNotIn("· paralel", starts["T03"][0])  # tek hazır görev proje klasöründe çalışır
        ups = [(e["data"]["task_id"], e["data"]["status"]) for e in read_jsonl(self.run_.rdir / "events.jsonl")
               if e["type"] == "task.update"]
        self.assertLess(ups.index(("T02", "running")), ups.index(("T01", "done")))

    def test_one_commit_per_task(self):
        subjects = git(self.run_.project, "log", "--format=%s", self.st["branch"]).splitlines()
        for tid in ("T01", "T02", "T03"):
            self.assertEqual(sum(1 for s in subjects if s.startswith(f"jev({tid}):")), 1, subjects)


@_ortak.slow
class ConflictRunTests(_NoLeftovers, unittest.TestCase):
    """Aynı dosyaya yazan iki paralel görev: sonra biten görevin işi koşunun son hâline çakışma işaretleriyle taşınır,
    aynı ajan çakışmayı çözer; deneme hakkı eksilmez, ajan başarısız sayılmaz."""

    @classmethod
    def setUpClass(cls):
        cls.run_, cls.term, cls.result = run_scenario(conflict_scenario())
        cls.st = cls.run_.state

    def test_reported(self):
        self.assertEqual(self.result, "reported")
        self.assertTrue(all(t["status"] == "done" for t in self.st["tasks"]))

    def test_conflict_goes_back_to_same_agent(self):
        hit = [t for t in self.st["tasks"] if any(a.get("outcome") == "merge_conflict" for a in t.get("attempts", []))]
        self.assertEqual(len(hit), 1, [t.get("attempts") for t in self.st["tasks"]])
        t = hit[0]
        first, second = t["attempts"]
        self.assertEqual((first["outcome"], first["counted"], first["files"]), ("merge_conflict", False, ["ortak.txt"]))
        self.assertEqual((second["outcome"], second["agent"], second["reason"]),
                         ("done", first["agent"], "Jev'in kararı"))
        self.assertEqual((t["attempt_count"], t["merge_conflicts"]), (1, 1))
        self.assertNotIn(first["agent"], t.get("failed_agents") or [])
        self.assertIn("çakıştı (ortak.txt)", self.term.text())
        other = next(x for x in self.st["tasks"][:2] if x is not t)
        self.assertEqual([a["outcome"] for a in other["attempts"]], ["done"])

    def test_resolved_file_merged(self):
        text = (self.run_.project / "ortak.txt").read_text(encoding="utf-8")
        self.assertEqual(text, "T01 yazdı\nT02 yazdı\n")


if __name__ == "__main__":
    unittest.main()
