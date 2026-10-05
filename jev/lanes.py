"""Paralel görev şeritleri: her görev kendi git çalışma ağacında (worktree) ve kendi dalında çalışır.

Şerit: task["lane"] = {"path", "branch", "base"}. Klasör projenin kardeşidir (`<proje>--jev-wt/<koşu etiketi>-<görev>`),
dal koşu dalının adına `-serit-<görev>` eklenerek açılır (mini deneme arşiv dalıyla karışmaz). Biten görevin dalını
Jev koşu dalına tek commit olarak birleştirir. Geri alma her zaman buradan geçer: şeridi olan görevde ana projeye
dokunulmaz (checkpoint şeridin geçmişindedir; ana projeyi ona döndürmek koşu dalının ilerlemesini silerdi).
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import gitops

LANE_INFIX = "-serit-"


def _sid(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(task_id))


def _key(p) -> str:
    # realpath: git yolları uzun adla yazar (C:\Users\Administrator), geçici klasör kısa adla gelebilir (ADMINI~1)
    return os.path.normcase(os.path.realpath(str(p)))


def run_tag(run_id: str) -> str:
    """Koşunun kısa etiketi: farklı koşuların şerit klasörleri çakışmasın, Windows'ta yollar kısa kalsın."""
    return hashlib.sha1(str(run_id).encode("utf-8")).hexdigest()[:6]


def run_branch(run) -> str:
    st = run.state
    return st.get("branch") or f"jev/{st['run_id']}"


def lane_branch(run, task_id: str) -> str:
    return f"{run_branch(run)}{LANE_INFIX}{_sid(task_id)}"


def lane_path(run, task_id: str) -> Path:
    return gitops.worktree_root(run.project) / f"{run_tag(run.state['run_id'])}-{_sid(task_id)}"


def open_lane(run, task: dict) -> Path:
    """Görevin şeridini hazırlar ve klasörünü döndürür. Beyin değişiklikleri korumaya karar verdiyse (keep_changes)
    mevcut şerit olduğu gibi kullanılır; değilse koşu dalının son hâlinden yeni bir şerit açılır. `keep_changes`
    sıfırlanmadan önce çağrılmalı."""
    lane = task.get("lane") or {}
    if task.get("keep_changes") and lane.get("path") and Path(lane["path"]).is_dir():
        return Path(lane["path"])
    if lane:
        close_lane(run, task)
    path, branch = lane_path(run, task["id"]), lane_branch(run, task["id"])
    base = gitops.head(run.project)
    gitops.worktree_add(run.project, path, branch, base)
    gitops.link_shared(run.project, path)
    task["lane"] = {"path": str(path), "branch": branch, "base": base}
    return path


def close_lane(run, task: dict) -> None:
    """Görevin şeridini (klasör ve dal) kaldırır. Hata koşuyu durdurmaz; kalan parçaları `sweep` toplar.

    Checkpoint şeridin geçmişindeydi: onunla birlikte düşer. Sonraki bir geri alma ana projeyi eski bir şerit
    tabanına döndürüp koşu dalının ilerlemesini silmesin."""
    lane = task.pop("lane", None)
    if not lane:
        return
    task.pop("checkpoint", None)
    task["keep_changes"] = False
    try:
        gitops.worktree_remove(run.project, Path(lane["path"]), lane.get("branch"))
    except (gitops.GitError, OSError) as e:
        run.log("warn", f"{task['id']} şeridi kaldırılamadı: {e}")


def workdir(run, task: dict) -> Path:
    """Görevin çalıştığı klasör: şeridi varsa şerit, yoksa proje."""
    lane = task.get("lane")
    return Path(lane["path"]) if lane else run.project


def branch(run, task: dict) -> str | None:
    """Görevin çalıştığı dal: şeridi varsa şerit dalı, yoksa koşu dalı."""
    lane = task.get("lane")
    return lane["branch"] if lane else run.state.get("branch")


def rollback(run, task: dict) -> None:
    """Görevin yarım değişikliklerini geri alır: şeridi varsa şerit kaldırılır, yoksa proje checkpoint'e döner."""
    task["keep_changes"] = False
    if task.get("lane"):
        close_lane(run, task)
    elif task.get("checkpoint"):
        gitops.rollback(run.project, task["checkpoint"], run.state.get("branch"))


def sweep(run) -> None:
    """Koşudan kalan şeritleri toplar: korunmayan şeritleri kaldırır, bu koşunun sahipsiz klasör ve dallarını siler,
    boş kalan kök klasörü kaldırır. Korunan şerit: beynin değişiklikleri koruyarak yeniden sıraya koyduğu görevinki."""
    project, st = run.project, run.state
    keep: set[str] = set()
    for t in st.get("tasks", []):
        lane = t.get("lane")
        if not lane:
            continue
        if t.get("status") == "ready" and t.get("keep_changes") and Path(lane["path"]).is_dir():
            keep.add(_key(lane["path"]))
        else:
            close_lane(run, t)
    root = gitops.worktree_root(project)
    tag = run_tag(st["run_id"]) + "-"
    try:
        strays = [p for p in gitops.worktree_paths(project) if _key(p.parent) == _key(root)]
        if root.is_dir():
            strays += list(root.iterdir())
        seen: set[str] = set()
        for p in strays:
            k = _key(p)
            if p.name.startswith(tag) and k not in keep and k not in seen:
                seen.add(k)
                gitops.worktree_remove(project, p)
        kept = {t["lane"]["branch"] for t in st.get("tasks", []) if t.get("lane")}
        for b in gitops.branches(project, run_branch(run) + LANE_INFIX):
            if b not in kept:
                gitops.git(["branch", "-q", "-D", b], project, check=False)
        gitops.git(["worktree", "prune"], project, check=False)
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except (gitops.GitError, OSError) as e:
        run.log("warn", f"Şerit temizliği tamamlanamadı: {e}")
