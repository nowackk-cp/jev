"""Git yardımcıları (belirtim §5.9).

Jev git durumunu yalnızca kendisi değiştirir: dal, checkpoint, geri alma ve commit. Push ve rebase yapılmaz;
birleştirme (merge) yalnızca paralel görevlerin kendi dallarını Jev'in koşu dalına almak içindir, kullanıcının dalına
hiçbir şey birleştirilmez.
Genel git ayarlarına dokunulmaz; depoda kimlik tanımlı değilse commit'e `-c user.name/-c user.email` eklenir.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

JEV_IDENTITY = ("Jev", "jev@localhost")
GITIGNORE_DEFAULT = """# Jev
.jev/
__pycache__/
*.pyc
.venv/
venv/
node_modules/
dist/
build/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
.env
.DS_Store
Thumbs.db
"""


class GitError(RuntimeError):
    pass


def _env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def git(args: list[str], cwd: Path, *, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    argv = ["git", "-c", "core.quotepath=false", "-c", "i18n.logOutputEncoding=utf-8", *args]
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, env=_env(), timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as e:
        raise GitError("git bulunamadı. Git kurulu ve PATH'te olmalı.") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {' '.join(args[:2])} zaman aşımına uğradı.") from e
    out = subprocess.CompletedProcess(argv, r.returncode, r.stdout.decode("utf-8", "replace"),
                                      r.stderr.decode("utf-8", "replace"))
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} başarısız ({r.returncode}): {out.stderr.strip() or out.stdout.strip()}")
    return out


def out(args: list[str], cwd: Path, **kw) -> str:
    return git(args, cwd, **kw).stdout.strip()


# --- depo --------------------------------------------------------------------------------

def is_repo(path: Path) -> bool:
    r = git(["rev-parse", "--show-toplevel"], path, check=False)
    if r.returncode != 0:
        return False
    top = Path(r.stdout.strip())
    try:
        return os.path.samefile(top, path)
    except OSError:
        return False


def inside_repo(path: Path) -> bool:
    return git(["rev-parse", "--is-inside-work-tree"], path, check=False).returncode == 0


def has_commits(cwd: Path) -> bool:
    return git(["rev-parse", "--verify", "-q", "HEAD"], cwd, check=False).returncode == 0


def identity_args(cwd: Path) -> list[str]:
    """Depoda (ya da genel ayarda) kimlik yoksa commit için geçici kimlik."""
    name = git(["config", "user.name"], cwd, check=False).stdout.strip()
    mail = git(["config", "user.email"], cwd, check=False).stdout.strip()
    if name and mail:
        return []
    return ["-c", f"user.name={JEV_IDENTITY[0]}", "-c", f"user.email={JEV_IDENTITY[1]}"]


def ensure_exclude(cwd: Path) -> None:
    """`.jev/` her zaman .git/info/exclude içinde olsun: commit edilmez, git clean silmez."""
    gdir = Path(out(["rev-parse", "--git-dir"], cwd))
    if not gdir.is_absolute():
        gdir = cwd / gdir
    p = gdir / "info" / "exclude"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    if not any(ln.strip() in (".jev", ".jev/", "/.jev", "/.jev/") for ln in text.splitlines()):
        with p.open("a", encoding="utf-8", newline="\n") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write(".jev/\n")


def init_repo(cwd: Path, first_message: str = "jev: proje başlangıcı") -> str:
    """Yeni proje: git init, .gitignore, ilk commit. HEAD'i döndürür."""
    cwd.mkdir(parents=True, exist_ok=True)
    if not is_repo(cwd):
        git(["init", "-q"], cwd)
    ensure_exclude(cwd)
    gi = cwd / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE_DEFAULT, encoding="utf-8", newline="\n")
    if not has_commits(cwd):
        git(["add", "-A"], cwd)
        git([*identity_args(cwd), "commit", "-q", "--allow-empty", "-m", first_message], cwd)
    return head(cwd)


def status_lines(cwd: Path) -> list[str]:
    s = git(["status", "--porcelain=v1", "-uall"], cwd).stdout
    return [ln for ln in s.splitlines() if ln.strip() and not _is_jev_path(ln[3:])]


def _is_jev_path(p: str) -> bool:
    p = p.strip().strip('"').replace("\\", "/")
    return p == ".jev" or p.startswith(".jev/")


def is_clean(cwd: Path) -> bool:
    return not status_lines(cwd)


def head(cwd: Path) -> str:
    return out(["rev-parse", "HEAD"], cwd)


def current_branch(cwd: Path) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, check=False).stdout.strip()


def branch_exists(cwd: Path, name: str) -> bool:
    return git(["rev-parse", "--verify", "-q", f"refs/heads/{name}"], cwd, check=False).returncode == 0


def start_branch(cwd: Path, name: str) -> None:
    """Koşu dalına geçer; yoksa HEAD'den açar."""
    if current_branch(cwd) == name:
        return
    if branch_exists(cwd, name):
        git(["checkout", "-q", name], cwd)
    else:
        git(["checkout", "-q", "-b", name], cwd)


# --- checkpoint, geri alma, commit ----------------------------------------------------

def checkpoint(cwd: Path) -> str:
    return head(cwd)


def rollback(cwd: Path, cp: str, branch: str | None = None) -> None:
    """Çalışma ağacını checkpoint'e döndürür. `.jev/` korunur."""
    if branch and current_branch(cwd) != branch:
        git(["checkout", "-q", "-f", branch], cwd)
    git(["reset", "-q", "--hard", cp], cwd)
    git(["clean", "-q", "-fd", "-e", ".jev"], cwd)


def commit_all(cwd: Path, message: str, allow_empty: bool = True) -> str | None:
    """Tüm değişiklikleri commit eder; yeni HEAD'i döndürür. Değişiklik yoksa ve boş commit istenmezse None."""
    git(["add", "-A"], cwd)
    staged = git(["diff", "--cached", "--quiet"], cwd, check=False).returncode != 0
    if not staged and not allow_empty:
        return None
    args = [*identity_args(cwd), "commit", "-q", "--no-verify", "-m", message]
    if not staged:
        args.insert(-2, "--allow-empty")
    git(args, cwd)
    return head(cwd)


def commit_paths(cwd: Path, paths: list[str], message: str) -> str | None:
    existing = [p for p in paths if (cwd / p).exists()]
    if not existing:
        return None
    git(["add", "--", *existing], cwd)
    if git(["diff", "--cached", "--quiet"], cwd, check=False).returncode == 0:
        return None
    git([*identity_args(cwd), "commit", "-q", "--no-verify", "-m", message], cwd)
    return head(cwd)


# --- inceleme ------------------------------------------------------------------------

def changes_since(cwd: Path, cp: str) -> dict:
    """Checkpoint'ten bu yana değişenler (commit edilmemişler dahil).

    Dönüş: {"added": [...], "modified": [...], "deleted": [...], "all": [...]} (yol listeleri, `.jev/` hariç)
    """
    res = {"added": [], "modified": [], "deleted": []}
    for ln in git(["diff", "--name-status", "--no-renames", cp], cwd).stdout.splitlines():
        parts = ln.split("\t", 1)
        if len(parts) != 2 or _is_jev_path(parts[1]):
            continue
        code, path = parts[0][:1], parts[1]
        key = {"A": "added", "D": "deleted"}.get(code, "modified")
        res[key].append(path)
    for p in out(["ls-files", "--others", "--exclude-standard"], cwd).splitlines():
        if p and not _is_jev_path(p) and p not in res["added"]:
            res["added"].append(p)
    res["all"] = sorted(set(res["added"] + res["modified"] + res["deleted"]))
    return res


def diff_stat(cwd: Path, base: str, target: str | None = None) -> str:
    args = ["diff", "--stat=120", base] + ([target] if target else [])
    return out(args, cwd, check=False)


def shortstat_since(cwd: Path, cp: str) -> str:
    s = out(["diff", "--shortstat", cp], cwd, check=False)
    new = [p for p in out(["ls-files", "--others", "--exclude-standard"], cwd, check=False).splitlines()
           if p and not _is_jev_path(p)]
    if new:
        s = (s + ", " if s else "") + f"{len(new)} yeni dosya"
    return s or "değişiklik yok"


def log_oneline(cwd: Path, base: str | None = None, n: int = 60) -> str:
    args = ["log", "--oneline", f"-n{n}"] + ([f"{base}..HEAD"] if base else [])
    return out(args, cwd, check=False)


def file_tree(cwd: Path, limit: int = 300) -> str:
    files = out(["ls-files"], cwd, check=False).splitlines()
    files += [p for p in out(["ls-files", "--others", "--exclude-standard"], cwd, check=False).splitlines() if p]
    files = sorted({f for f in files if f and not _is_jev_path(f)})
    more = len(files) - limit
    lines = files[:limit]
    if more > 0:
        lines.append(f"… ve {more} dosya daha")
    return "\n".join(lines)


def count_commits(cwd: Path, base: str) -> int:
    s = out(["rev-list", "--count", f"{base}..HEAD"], cwd, check=False)
    return int(s) if s.isdigit() else 0


# --- paralel görevler: her görev kendi çalışma ağacında (git worktree) ----------------------------

# Projede git'in yok saydığı bu klasörler görev ağacına bağlanır: bağımlılıklar her ağaçta yeniden kurulmaz
SHARED_DIRS = ("node_modules", ".venv", "venv")


class MergeConflict(GitError):
    """Görev dalı koşu dalına birleşemedi. `files`: çakışan dosyalar."""

    def __init__(self, files: list[str], text: str):
        super().__init__(text)
        self.files = files


def worktree_root(project: Path) -> Path:
    """Görev ağaçlarının klasörü: projenin kardeşi (`<proje>--jev-wt`). Proje içinde olsaydı test araçları
    (node --test, jest) kopyaları da tarardı; sistemin geçici klasörü ise Windows'ta yolları uzatırdı."""
    project = Path(project)
    return project.parent / f"{project.name}--jev-wt"


def worktree_add(project: Path, path: Path, branch: str, base: str) -> None:
    """`base` commit'inden `branch` dalıyla yeni bir çalışma ağacı açar; aynı yerde eskisi varsa önce kaldırır."""
    path = Path(path)
    worktree_remove(project, path, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "-q", "-f", "-B", branch, str(path), base], project)


def worktree_remove(project: Path, path: Path, branch: str | None = None) -> None:
    """Çalışma ağacını (ve verilirse dalını) kaldırır. Paylaşılan klasör bağlantıları önce çözülür: silme işlemi
    bağlantının içine girip projenin asıl node_modules klasörüne dokunmasın."""
    path = Path(path)
    if path.exists() or _is_link(path):
        unlink_shared(path)
        git(["worktree", "remove", "--force", "--force", str(path)], project, check=False)
        if path.exists():
            _rmtree(path)
    git(["worktree", "prune"], project, check=False)
    if branch and branch_exists(project, branch):
        git(["branch", "-q", "-D", branch], project, check=False)


def worktree_paths(project: Path) -> list[Path]:
    """Depoya kayıtlı çalışma ağaçları (ana ağaç dahil)."""
    r = git(["worktree", "list", "--porcelain"], project, check=False)
    return [Path(ln[len("worktree "):].strip()) for ln in r.stdout.splitlines() if ln.startswith("worktree ")]


def branches(project: Path, prefix: str) -> list[str]:
    """`prefix` ile başlayan yerel dallar."""
    s = out(["for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}*"], project, check=False)
    return [b for b in s.splitlines() if b.strip()]


def link_shared(project: Path, lane: Path) -> list[str]:
    """Projede git'in yok saydığı bağımlılık klasörlerini görev ağacına bağlar (Windows'ta junction). Görev
    bağımlılıkları yeniden kurmadan test çalıştırabilir. Bağlanan klasörlerin adlarını döndürür."""
    linked: list[str] = []
    for name in SHARED_DIRS:
        src, dst = Path(project) / name, Path(lane) / name
        if not src.is_dir() or dst.exists() or _is_link(dst):
            continue
        if git(["check-ignore", "-q", name], project, check=False).returncode != 0:
            continue  # izlenen klasör: ağaçta zaten var ya da görev onu değiştirebilir
        try:
            _make_link(src, dst)
        except OSError:
            continue
        linked.append(name)
    return linked


def unlink_shared(lane: Path) -> None:
    """Görev ağacındaki bağlantıları kaldırır; bağlantının gösterdiği asıl klasöre dokunmaz."""
    for name in SHARED_DIRS:
        p = Path(lane) / name
        if _is_link(p):
            try:
                if os.name == "nt":
                    os.rmdir(p)
                else:
                    os.unlink(p)
            except OSError:
                pass


def _make_link(src: Path, dst: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(src), str(dst))
    else:
        os.symlink(src, dst, target_is_directory=True)


def _is_link(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return getattr(st, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _rmtree(path: Path) -> None:
    def retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def squash_merge(project: Path, branch: str, message: str) -> str | None:
    """Görev dalını koşu dalına tek commit olarak birleştirir ve yeni HEAD'i döndürür. Çakışmada birleştirme geri
    alınır (ana ağaç önceki commit'e döner) ve MergeConflict fırlatılır."""
    before = head(project)
    r = git([*identity_args(project), "merge", "--squash", branch], project, check=False)
    if r.returncode == 0:
        return commit_all(project, message)
    files = [f for f in out(["diff", "--name-only", "--diff-filter=U"], project, check=False).splitlines() if f]
    git(["reset", "-q", "--hard", before], project, check=False)
    git(["clean", "-q", "-fd", "-e", ".jev"], project, check=False)
    raise MergeConflict(files, f"{r.stdout.strip()} {r.stderr.strip()}".strip())


def update_from(cwd: Path, commit: str, message: str) -> None:
    """`commit`'i bu ağacın dalına birleştirir (görev şeridini koşu dalının son hâline getirir). Çakışmada birleştirme
    geri alınır (ağaç önceki commit'inde kalır) ve MergeConflict fırlatılır."""
    before = head(cwd)
    r = git([*identity_args(cwd), "merge", "-q", "--no-edit", "-m", message, commit], cwd, check=False)
    if r.returncode == 0:
        return
    files = [f for f in out(["diff", "--name-only", "--diff-filter=U"], cwd, check=False).splitlines() if f]
    git(["merge", "--abort"], cwd, check=False)
    git(["reset", "-q", "--hard", before], cwd, check=False)
    git(["clean", "-q", "-fd", "-e", ".jev"], cwd, check=False)
    raise MergeConflict(files, f"{r.stdout.strip()} {r.stderr.strip()}".strip())


def pick_changes(cwd: Path, rev: str) -> list[str]:
    """`rev`'i bu ağaca commit etmeden birleştirir (merge --squash): görevin değişiklikleri dalın son hâline üç yollu
    uygulanır ve çakışan dosyalar döndürülür (içlerinde git çakışma işaretleri kalır). Sonunda index temizlenir:
    ağaçta yalnızca sıradan, commit edilmemiş değişiklikler durur. Değişiklikler hiç uygulanamazsa GitError."""
    r = git([*identity_args(cwd), "merge", "--squash", rev], cwd, check=False)
    files = [f for f in out(["diff", "--name-only", "--diff-filter=U"], cwd, check=False).splitlines() if f]
    git(["reset", "-q"], cwd, check=False)
    if r.returncode != 0 and not files:
        raise GitError(f"Değişiklikler taşınamadı: {(r.stderr or r.stdout).strip()[:500]}")
    return files


def carry(project: Path, path: Path, branch: str, base: str, rev: str) -> list[str]:
    """Çakışan görev şeridini yeniden kurar: `path`'teki çalışma ağacı `base`'ten (koşu dalının son hâli) baştan açılır
    ve `rev`'deki iş commit edilmeden üstüne birleştirilir. Çakışan dosyaları döndürür."""
    keep = f"{branch}-onceki"
    git(["branch", "-f", keep, rev], project)
    try:
        worktree_add(project, path, branch, base)
        link_shared(project, path)
        return pick_changes(path, keep)
    finally:
        git(["branch", "-q", "-D", keep], project, check=False)
