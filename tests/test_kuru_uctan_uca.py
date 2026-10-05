"""Uçtan uca kuru koşu: gerçek `jev` komutu alt süreçte, sahte ajanlarla baştan sona çalışır.

Kuru senaryo (jev/mock/senaryo_varsayilan.json) sabittir ve sistemin sorun yollarından geçer: Opus planı ve görev
kartlarını tek çağrıda yazar; Sol'un T02'si doğrulamada kalır, Jev modeli işi Sonnet'e verir; T04'te koruma proje
dışına yazmayı engeller ve şemaya uymayan çıktı onarılır; Luna T05'te kotaya takılır ve iş sıradaki uygun ajana
geçer. Denetçi Sol'un ilk raporu «kısmen başarılı»dır ve akış orada durur; `jev duzelt` ile açılan ikinci turdan
sonra rapor «başarılı» olur.

Model çağrısı yapılmaz (kuru koşu), arayüz açılmaz (--arayuz-yok), stdin boştur: komut beklenmez (--bitince-cik).
Her sınıf kendi geçici JEV_HOME'u ve proje klasörüyle çalışır.
"""
import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

import _ortak
from jev.util import parse_iso, read_json, read_jsonl

REQUEST = "Yapılacaklar listesi CLI tasarla"
NOTE = "temizle komutunu ekle"
SPEED = "200"
FIRST_COMMITS = ["jev: proje başlangıcı", "jev: ortak bağlam (AGENTS.md, CLAUDE.md)"]
CALL_RX = re.compile(r"^\d{3}-([a-z_]+)-(.+)-([a-z0-9]+)\.prompt\.md$")


def jev_env(home: Path) -> dict:
    return dict(os.environ, JEV_HOME=str(home), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def jev(env: dict, *args: str, speed: str = SPEED) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "jev", *args, "--kuru-hiz", speed, "--arayuz-yok", "--bitince-cik"],
                          cwd=_ortak.ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def git(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


def only_run(project: Path) -> str:
    runs = project / ".jev" / "runs"
    ids = sorted(p.name for p in runs.iterdir()) if runs.exists() else []
    if len(ids) != 1:
        raise AssertionError(f"projede tek koşu bekleniyordu: {ids}")
    return ids[0]


def calls_of(rdir: Path) -> list[tuple[str, str, str]]:
    """Koşunun model çağrıları sırasıyla: (aşama, görev ya da "-", ajan)."""
    calls = []
    for p in sorted((rdir / "calls").glob("*.prompt.md")):
        m = CALL_RX.match(p.name)
        calls.append((m.group(1), m.group(2), m.group(3)) if m else ("?", "?", p.name))
    return calls


def snapshot(project: Path, rid: str, home: Path) -> dict:
    """Komuttan sonra koşunun ve deponun durumu: testler sıradan bağımsız olsun diye bir kez okunur."""
    rdir = project / ".jev" / "runs" / rid
    calls = calls_of(rdir)
    return {
        "state": read_json(rdir / "state.json", {}),
        "events": read_jsonl(rdir / "events.jsonl"),
        "decisions": read_jsonl(rdir / "decisions.jsonl"),
        "guard": read_jsonl(rdir / "guard.jsonl"),
        "model": read_jsonl(rdir / "jev-model.jsonl"),  # Jev modeli çağrıları (kuru: sahte model)
        "calls": calls,  # (aşama, görev ya da "-", ajan)
        "files": sorted(p.name for p in rdir.iterdir() if p.is_file()),
        "commits": git(project, "log", "--reverse", "--format=%s").splitlines(),
        "branch": git(project, "branch", "--show-current"),
        "dirty": git(project, "status", "--porcelain"),
        "tracked_jev": git(project, "ls-files", ".jev"),
        "lock": (project / ".jev" / "lock").exists(),
        "index": (read_json(home / "kosular.json", {}) or {}).get(rid),
    }


def tail(proc: subprocess.CompletedProcess, n: int = 15) -> str:
    return "\n".join((proc.stdout + proc.stderr).splitlines()[-n:])


@_ortak.slow
class DryRunEndToEnd(unittest.TestCase):
    """`jev "istek" --kuru-hiz …` ve ardından `jev duzelt`: iki tur, iki rapor."""

    @classmethod
    def setUpClass(cls):
        base = _ortak.temp_dir("uctan-uca")
        cls.home, cls.project = base / "home", base / "proj"
        cls.home.mkdir()
        (cls.home / "jev.toml").write_text("[guvenlik]\nkoruma = true\n", encoding="utf-8")
        cls.env = jev_env(cls.home)
        cls.first = jev(cls.env, REQUEST, "--proje", str(cls.project))
        if cls.first.returncode != 0:
            raise AssertionError(f"ilk koşu {cls.first.returncode} ile bitti:\n{tail(cls.first)}")
        cls.rid = only_run(cls.project)
        cls.r1 = snapshot(cls.project, cls.rid, cls.home)
        cls.second = jev(cls.env, "duzelt", cls.rid, NOTE)
        cls.r2 = snapshot(cls.project, cls.rid, cls.home)

    def tasks(self, snap: dict) -> dict[str, dict]:
        return {t["id"]: t for t in snap["state"]["tasks"]}

    def test_first_round_stops_at_the_report(self):
        st, out = self.r1["state"], self.first.stdout
        self.assertEqual((st["phase"], st["round"], st["dry_run"], st["new_project"]), ("reported", 1, True, True))
        self.assertEqual(Path(st["project_dir"]).resolve(), self.project.resolve())
        self.assertEqual({k: t["status"] for k, t in self.tasks(self.r1).items()},
                         {f"T0{i}": "done" for i in range(1, 7)})
        self.assertEqual([(r["round"], r["verdict"]) for r in st["reports"]], [(1, "kismen")])
        self.assertLessEqual({"plan.md", "report.md", "report.json"}, set(self.r1["files"]))
        self.assertNotIn("report-2.md", self.r1["files"])
        self.assertEqual([e["data"]["phase"] for e in self.r1["events"] if e["type"] == "run.phase"],
                         ["planning", "executing", "reviewing", "reported"])
        self.assertIn("Kısmen başarılı", out)
        self.assertIn("✗ SC4: karşılanmadı", out)
        self.assertIn(f'Düzeltmek için: jev duzelt {self.rid} "not"', out)  # düzeltme yalnızca kullanıcı isteyince
        self.assertNotIn("Düzeltme turu", out)
        self.assertEqual((self.r1["index"]["phase"], self.r1["index"]["round"], self.r1["index"]["dry"]),
                         ("reported", 1, True))
        self.assertEqual(self.r1["index"]["request"], REQUEST)

    def test_jev_solves_problems(self):
        t, evs = self.tasks(self.r1), self.r1["events"]
        outcomes = {k: [(a["agent"], a["outcome"]) for a in x["attempts"]] for k, x in t.items()}
        # T02: Sol'un işi doğrulamada kaldı; Jev modeli (kuru: sahte) işi Sonnet'e verdi
        self.assertEqual(outcomes["T02"], [("sol", "verify_failed"), ("sonnet", "done")])
        self.assertEqual(t["T02"]["failed_agents"], ["sol"])
        decisions = [e["data"] for e in evs if e["type"] == "jev.decision"]
        self.assertEqual([(d["task_id"], d["decision"], d["agent"], d["brain"]) for d in decisions],
                         [("T02", "reassign", "sonnet", "jev")])
        self.assertEqual([(d["task_id"], d["brain"], d["agent"]) for d in self.r1["decisions"]],
                         [("T02", "jev", "sonnet")])
        # T05: Luna kotaya takıldı; iş deneme hakkı yenmeden sıradaki uygun ajana geçti
        self.assertEqual(outcomes["T05"], [("luna", "quota"), ("sonnet", "done")])
        self.assertEqual(t["T05"]["attempt_count"], 1)
        quota = [e for e in evs if e["type"] == "quota"]
        self.assertEqual([e["data"]["agent"] for e in quota], ["luna"])
        self.assertGreater(parse_iso(quota[0]["data"]["until"]), parse_iso(quota[0]["ts"]))
        # T04: gerçek kanca betiği proje dışına yazmayı engelledi; şemaya uymayan çıktı onarıldı
        guard = [e["data"] for e in evs if e["type"] == "guard.blocked"]
        self.assertEqual([(g["agent"], g["task_id"], g["category"], g["action"]) for g in guard],
                         [("sonnet", "T04", "proje_disi", "engellendi")])
        self.assertEqual([(g["ajan"], g["gorev"], g["eylem"]) for g in self.r1["guard"]],
                         [("sonnet", "T04", "engellendi")])
        self.assertIn("sonnet: çıktı şemaya uymuyor; onarım isteniyor (1/2).",
                      [e["data"].get("text") for e in evs if e["type"] == "log"])
        self.assertIn(("repair", "T04", "sonnet"), self.r1["calls"])
        self.assertEqual(outcomes["T04"], [("sonnet", "done")])
        # kararı Jev modeli verdi (metin gerekmedi): beyin hiç çağrılmadı
        self.assertEqual({a for p, _, a in self.r1["calls"] if p == "brain"}, set())
        self.assertIn("karar", {m["purpose"] for m in self.r1["model"]})

    def test_opus_plans_sol_reviews(self):
        # Opus planı ve görev kartlarını tek çağrıda yazar, kod yazmaz (eskalasyon olmadı); son kontrolü Sol yapar
        calls = self.r1["calls"]
        self.assertEqual([(p, a) for p, _, a in calls if p in ("plan", "review")], [("plan", "opus"), ("review", "sol")])
        self.assertEqual({p for p, _, a in calls if a == "opus"}, {"plan"})
        self.assertLessEqual({a for p, _, a in calls if p == "worker"}, {"sol", "sonnet", "luna"})
        self.assertEqual([r["by"] for r in self.r1["state"]["reports"]], ["sol"])

    def test_every_task_is_one_commit_on_the_run_branch(self):
        st = self.r1["state"]
        want = FIRST_COMMITS + [f"jev({t['id']}): {t['title']} [{t['agent']}]" for t in st["tasks"]]
        self.assertEqual(self.r1["commits"], want)
        self.assertIn("jev(T06): Uçtan uca test [sonnet]", self.r1["commits"])  # Türkçe karakterler bozulmadan
        self.assertEqual(self.r1["branch"], f"jev/{self.rid}")
        self.assertEqual(st["branch"], self.r1["branch"])
        for t in st["tasks"]:
            self.assertEqual(git(self.project, "log", "-1", "--format=%s", t["commit"]),
                             f"jev({t['id']}): {t['title']} [{t['agent']}]")
        self.assertEqual((self.r1["dirty"], self.r1["tracked_jev"], self.r1["lock"]), ("", "", False))

    def test_fix_round_on_request(self):
        st, out = self.r2["state"], self.second.stdout
        self.assertEqual(self.second.returncode, 0, tail(self.second))
        self.assertEqual((st["phase"], st["round"], st["fix_note"]), ("reported", 2, NOTE))
        self.assertIn(f"Düzeltme turu başlıyor (tur 2; not: {NOTE}).", out)
        self.assertEqual([(r["round"], r["verdict"]) for r in st["reports"]], [(1, "kismen"), (2, "basarili")])
        self.assertIn("report-2.md", self.r2["files"])
        self.assertIn("Başarılı", out)
        fix = self.tasks(self.r2)["D1-01"]
        self.assertEqual((fix["status"], fix["agent"], fix["round"]), ("done", "sol", 2))
        self.assertEqual({k: t["status"] for k, t in self.tasks(self.r2).items() if k != "D1-01"},
                         {k: t["status"] for k, t in self.tasks(self.r1).items()})
        # ikinci turda yalnızca düzeltme işi yapıldı: Opus eksikleri düzeltme kartına çevirdi, Sol yaptı ve denetledi
        self.assertEqual(self.r2["calls"][len(self.r1["calls"]):],
                         [("fix", "-", "opus"), ("worker", "D1-01", "sol"), ("review", "-", "sol")])
        seen = len(self.r1["events"])
        self.assertEqual([e["data"]["phase"] for e in self.r2["events"][seen:] if e["type"] == "run.phase"],
                         ["fixing", "executing", "reviewing", "reported"])
        self.assertEqual(self.r2["commits"], self.r1["commits"] + ["jev(D1-01): temizle alt komutu [sol]"])
        self.assertEqual((self.r2["branch"], self.r2["dirty"], self.r2["lock"]), (self.r1["branch"], "", False))
        self.assertEqual((self.r2["index"]["phase"], self.r2["index"]["round"]), ("reported", 2))

    def test_the_generated_product_works(self):
        data = _ortak.temp_dir("todo") / "todo.json"  # ürün kullanıcının ev klasörüne yazmasın
        env = dict(self.env, TODO_DOSYA=str(data))

        def py(*args: str) -> str:
            r = subprocess.run([sys.executable, *args], cwd=self.project, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(r.returncode, 0, f"{args}: {r.stderr[-600:]}")
            return r.stdout.strip()

        py("-m", "unittest", "discover", "-s", "tests")  # projenin kendi testleri (temizle dahil)
        self.assertEqual(py("-m", "todo", "ekle", "Süt al, çöpü çıkar"), "Eklendi: Süt al, çöpü çıkar")
        py("-m", "todo", "ekle", "Işıkları kapat")
        py("-m", "todo", "bitir", "1")
        self.assertEqual(py("-m", "todo", "temizle"), "1 tamamlanmış görev silindi.")
        self.assertEqual(py("-m", "todo", "listele"), "1. [ ] Işıkları kapat")


@_ortak.slow
class CrashRecovery(unittest.TestCase):
    """Süreç bir görevin ortasında öldürülür (elektrik kesildi gibi); `jev devam` yarım işi geri alıp koşuyu bitirir."""

    def test_devam_after_the_process_is_killed(self):
        base = _ortak.temp_dir("cokme")
        home, project = base / "home", base / "proj"
        env = jev_env(home)
        proc = subprocess.Popen([sys.executable, "-m", "jev", REQUEST, "--proje", str(project), "--kuru-hiz", "10",
                                 "--arayuz-yok", "--bitince-cik"], cwd=_ortak.ROOT, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(self.wait_until_running(project, "T02", proc), "T02 hiç başlamadı")
        finally:
            proc.kill()  # temiz kapanış yok: kilit, yarım iş ve etkin aşama geride kalır
            proc.wait(30)
        rid = only_run(project)
        rdir = project / ".jev" / "runs" / rid
        before = read_json(rdir / "state.json", {})
        self.assertEqual(before["phase"], "executing")
        half = [t["id"] for t in before["tasks"] if t["status"] in ("running", "verifying", "needs_decision")]
        done = [t["id"] for t in before["tasks"] if t["status"] == "done"]
        worker_calls = lambda calls, tid: [c for c in calls if c[:2] == ("worker", tid)]
        calls_before = calls_of(rdir)
        self.assertTrue(half)
        self.assertIn("T01", done)
        self.assertTrue((project / ".jev" / "lock").exists())

        r = jev(env, "devam", rid)
        self.assertEqual(r.returncode, 0, tail(r))
        for tid in half:
            self.assertIn(f"{tid} yarım kalmıştı; değişiklikleri geri alındı, görev yeniden sırada.", r.stdout)
        self.assertIn("Koşu sürdürülüyor", r.stdout)
        snap = snapshot(project, rid, home)
        st = snap["state"]
        self.assertEqual(st["phase"], "reported")
        self.assertEqual({t["id"]: t["status"] for t in st["tasks"]}, {f"T0{i}": "done" for i in range(1, 7)})
        self.assertEqual(snap["commits"],
                         FIRST_COMMITS + [f"jev({t['id']}): {t['title']} [{t['agent']}]" for t in st["tasks"]])
        for tid in done:  # çökmeden önce biten görevler yeniden yapılmadı
            self.assertEqual(worker_calls(snap["calls"], tid), worker_calls(calls_before, tid), tid)
        self.assertEqual(snap["calls"][:len(calls_before)], calls_before)  # eski çağrı kayıtları korunur
        self.assertEqual((snap["branch"], snap["dirty"], snap["tracked_jev"], snap["lock"]),
                         (f"jev/{rid}", "", "", False))
        phases = [(e["data"]["phase"], e["data"].get("kind")) for e in snap["events"] if e["type"] == "run.phase"]
        self.assertIn(("paused", "hata"), phases)  # çökme duraklatma sayıldı, sonra kaldığı yerden sürdü
        self.assertEqual(phases[-2:], [("reviewing", None), ("reported", None)])
        seqs = [e["seq"] for e in snap["events"]]
        self.assertEqual(seqs, sorted(set(seqs)))  # olay numaraları çökmeden sonra da tekrarsız ve sıralı

    @staticmethod
    def wait_until_running(project: Path, tid: str, proc: subprocess.Popen, timeout: float = 120) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None:
            for ev_file in (project / ".jev" / "runs").glob("*/events.jsonl"):
                for e in read_jsonl(ev_file):
                    d = e.get("data") or {}
                    if e.get("type") == "task.update" and d.get("task_id") == tid and d.get("status") == "running":
                        return True
            time.sleep(0.05)
        return False


if __name__ == "__main__":
    unittest.main()
