"""Felaket koruması (belirtim §5.10).

Kurallar kategorilere ayrılır: disk ve sistem, proje dışına yazma, kimlik bilgisi, git,
dışarıya yayın, internetten indirilen betiği çalıştırma ve Jev'in iç dosyaları (.git/, .jev/).

İki kullanım:
- Claude: PreToolUse kancası olarak `python -m jev.guard --kosu <koşu> --proje <proje>`.
  Araç çağrısı stdin'den JSON olarak gelir. Reddedilirse 2 çıkış koduyla stderr'e gerekçe yazılır.
- Codex: adaptör, --json akışındaki her komutu `check_command` ile denetler. `kill` işaretli
  bir karar gelirse süreç ağacını durdurur. Bu önleyici değil, durdurucudur (bkz. docs/cli-notlari.md).

Komut çözümleme kasıtlı olarak basittir: amaç tam bir kabuk ayrıştırıcısı değil, felaket
sınıfındaki komutları komut konumunda yakalamak ve veri metinlerinde yanlış alarm vermemektir.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

CATEGORY_TR = {
    "disk_sistem": "disk ve sistem",
    "proje_disi": "proje dışına yazma",
    "kimlik": "kimlik bilgisi",
    "git": "git",
    "yayin": "dışarıya yayın",
    "indir_calistir": "indirilen betiği çalıştırma",
    "ic_dosya": "Jev/git iç dosyası",
}
# Görevi "sorunlu" yapan kategoriler (§5.6). Diğer engellemeler yalnızca kaydedilir.
SERIOUS = {"disk_sistem", "kimlik", "yayin", "indir_calistir"}


@dataclass
class Verdict:
    category: str
    rule: str
    reason: str
    kill: bool = True  # Codex'te süreç durdurulsun mu

    @property
    def serious(self) -> bool:
        return self.category in SERIOUS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["serious"] = self.serious
        d["category_tr"] = CATEGORY_TR.get(self.category, self.category)
        return d


# --- yollar -------------------------------------------------------------------

_ENV_PS = re.compile(r"\$\{?env:([A-Za-z_][A-Za-z0-9_()]*)\}?", re.I)
_ENV_CMD = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")
_DRIVE_ABS = re.compile(r"^[a-zA-Z]:\\")
_DRIVE_ONLY = re.compile(r"^[a-zA-Z]:$")
_GITBASH = re.compile(r"^/([a-zA-Z])(?=/|$)")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _inside(path: str, base: str | Path) -> bool:
    a, b = _norm(str(path)), _norm(str(base))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:  # farklı sürücüler
        return False


def _same(a: str | Path, b: str | Path) -> bool:
    return _norm(str(a)) == _norm(str(b))


def _temp_dir() -> str:
    return os.path.realpath(tempfile.gettempdir())


def _in_temp(path: str) -> bool:
    """%TEMP% kısa adla (C:\\Users\\ADMINI~1\\…) gelebilir; realpath uzun adı verir: ikisi de geçici klasördür."""
    raw = tempfile.gettempdir()
    return _inside(path, raw) or _inside(path, _temp_dir())


def _protected_roots() -> list[str]:
    home = Path.home()
    env = os.environ
    cands = [
        home, home.parent, home / "AppData", home / "AppData" / "Roaming", home / "AppData" / "Local",
        home / "Documents", home / "Desktop", home / "Downloads", home / "OneDrive", home / "Pictures",
        env.get("SystemRoot", r"C:\Windows"), env.get("ProgramFiles", r"C:\Program Files"),
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), env.get("ProgramData", r"C:\ProgramData"),
        env.get("APPDATA", ""), env.get("LOCALAPPDATA", ""), _temp_dir(),
    ]
    return [str(c) for c in cands if c]


def _system_dirs() -> list[str]:
    env = os.environ
    return [env.get("SystemRoot", r"C:\Windows"), env.get("ProgramFiles", r"C:\Program Files"),
            env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), env.get("ProgramData", r"C:\ProgramData")]


def _expand(tok: str, cwd: Path) -> str:
    s = tok.strip()
    low = s.lower()
    for pre in ("microsoft.powershell.core\\filesystem::", "filesystem::"):
        if low.startswith(pre):
            s = s[len(pre):]
            break
    s = _ENV_PS.sub(lambda m: os.environ.get(m.group(1), m.group(0)), s)
    s = _ENV_CMD.sub(lambda m: os.environ.get(m.group(1), m.group(0)), s)
    home = str(Path.home())
    if s == "~" or s.startswith(("~/", "~\\")):
        s = home + s[1:]
    s = re.sub(r"^\$home(?![A-Za-z0-9_])", lambda m: home, s, flags=re.I)
    s = re.sub(r"^\$(?:pwd|psscriptroot)(?![A-Za-z0-9_])", lambda m: str(cwd), s, flags=re.I)
    m = _GITBASH.match(s)
    if m:
        s = m.group(1).upper() + ":\\" + s[3:]
    return s


def _target(tok: str, cwd: Path) -> tuple[str, str | None] | None:
    """Bir yol parçasını mutlak yola çevirir. (yol, joker) döner; joker: None | 'all' | 'pattern'.
    Çözülemeyen değişken içeriyorsa None."""
    if not tok:
        return None
    s = _expand(tok, cwd)
    if "$" in s or "`" in s:
        return None
    if s == "/" or s == "/*":  # kabukta kök
        return (os.path.splitdrive(str(cwd))[0] + "\\", "all" if s.endswith("*") else None)
    s = s.replace("/", "\\")
    wild = None
    if "*" in s or "?" in s:
        parts = s.split("\\")
        idx = next(i for i, p in enumerate(parts) if "*" in p or "?" in p)
        wild = "all" if parts[idx] in ("*", "*.*") and idx == len(parts) - 1 else "pattern"
        s = "\\".join(parts[:idx]) or "."
        if _DRIVE_ONLY.match(s):
            s += "\\"
    if _DRIVE_ABS.match(s) or s.startswith("\\\\"):
        p = s
    elif _DRIVE_ONLY.match(s):
        p = s + "\\"
    elif s.startswith("\\"):
        p = os.path.splitdrive(str(cwd))[0] + s
    else:
        p = str(cwd) + "\\" + s
    return os.path.normpath(p), wild


def _is_drive_root(p: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:\\?$", p)) or p in ("\\", "\\\\")


def classify_delete(tok: str, cwd: Path, project: Path) -> Verdict | None:
    t = _target(tok, cwd)
    if t is None:
        return None
    path, wild = t
    if _is_drive_root(path) or any(_same(path, r) for r in _protected_roots()) or \
            (_inside(project, path) and not _same(project, path)):
        return Verdict("disk_sistem", "toplu-silme",
                       f"'{tok}' hedefli silme engellendi (sürücü kökü, kullanıcı ya da sistem klasörü).")
    if _same(path, project):
        if wild in (None, "all"):
            return Verdict("ic_dosya", "proje-koku-silme", "Proje klasörünün tamamını silme engellendi.")
        return None
    if _inside(path, project):
        return _internal(path, project)
    if _in_temp(path):
        return None
    if any(_inside(path, d) for d in _system_dirs()):
        return Verdict("disk_sistem", "sistem-silme", f"Sistem klasöründe silme engellendi: {path}")
    return Verdict("proje_disi", "disari-silme", f"Proje dışında silme engellendi: {path}")


def _internal(path: str, project: Path) -> Verdict | None:
    rel = os.path.relpath(_norm(path), _norm(str(project)))
    first = rel.split(os.sep)[0].lower()
    if first in (".git", ".jev"):
        return Verdict("ic_dosya", "ic-dosya", f"Jev'in iç dosyalarına dokunma engellendi: {first}\\")
    return None


def classify_write(tok: str, cwd: Path, project: Path) -> Verdict | None:
    cred = scan_credentials(tok)
    if cred:
        return cred
    t = _target(tok, cwd)
    if t is None:
        return None
    path, _ = t
    if _inside(path, project):
        return _internal(path, project)
    if _in_temp(path):
        return None
    if any(_inside(path, d) for d in _system_dirs()) or _is_drive_root(path):
        return Verdict("disk_sistem", "sistem-yazma", f"Sistem klasörüne yazma engellendi: {path}")
    return Verdict("proje_disi", "disari-yazma", f"Proje dışına yazma engellendi: {path}")


# --- kimlik bilgileri -----------------------------------------------------------------

_CRED_PATTERNS = [
    (re.compile(r"\.codex\\auth\.json"), "Codex giriş bilgisi"),
    (re.compile(r"\.credentials\.json"), "Claude giriş bilgisi"),
    (re.compile(r"(?:^|[\\\s'\"=:~(])\.ssh(?:\\|$|[\s'\";)])"), "SSH anahtarları"),
    (re.compile(r"\\(?:google\\chrome|microsoft\\edge|bravesoftware\\brave-browser|chromium|vivaldi)\\user data"),
     "tarayıcı profili"),
    (re.compile(r"\\opera software\\"), "tarayıcı profili"),
    (re.compile(r"\\mozilla\\firefox\\profiles"), "tarayıcı profili"),
    (re.compile(r"\.git-credentials"), "git kimlik bilgisi"),
    (re.compile(r"(?:^|[\\\s'\"])[_.]netrc(?![\w.])"), "netrc kimlik bilgisi"),
    (re.compile(r"\.aws\\credentials"), "AWS kimlik bilgisi"),
    (re.compile(r"\.docker\\config\.json"), "Docker kimlik bilgisi"),
    (re.compile(r"\.pypirc(?![\w.])"), "PyPI kimlik bilgisi"),
    (re.compile(r"\\microsoft\\(?:credentials|protect|vault)(?:\\|$)"), "Windows kimlik kasası"),
]


def scan_credentials(text: str) -> Verdict | None:
    if not text:
        return None
    t = re.sub(r"\\+", r"\\", text.lower().replace("/", "\\"))
    for rx, what in _CRED_PATTERNS:
        if rx.search(t):
            return Verdict("kimlik", "kimlik-dosyasi", f"Kimlik bilgisine erişim engellendi ({what}).")
    return None


# --- komut ayrıştırma -------------------------------------------------------------------

_HERE_PS = re.compile(r"@(['\"])\r?\n.*?\r?\n\1@", re.S)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


def _strip_data(script: str) -> str:
    """Here-string ve heredoc gövdelerini (veri) çıkarır."""
    s = _HERE_PS.sub("''", script)
    return _HEREDOC.sub("", s)


def _segments(script: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    q = None
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if q:
            cur.append(ch)
            if q == '"' and ch in "\\`" and i + 1 < n and (ch == "`" or script[i + 1] in '"\\'):
                cur.append(script[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
            i += 1
            continue
        if ch in "'\"":
            q = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "`" and i + 1 < n:
            if script[i + 1] in "\r\n":  # PowerShell satır devamı
                i += 2
                continue
            cur.append(ch)
            cur.append(script[i + 1])
            i += 2
            continue
        if ch in ";|\n\r{}()" or (ch == "&" and not (cur and cur[-1] == ">")):
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def _words(seg: str) -> list[str]:
    words: list[str] = []
    cur: list[str] = []
    q = None
    has = False
    i, n = 0, len(seg)
    while i < n:
        ch = seg[i]
        if q:
            if q == '"' and ch in "\\`" and i + 1 < n and (ch == "`" or seg[i + 1] in '"\\'):
                cur.append(seg[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
                i += 1
                continue
            cur.append(ch)
            i += 1
            continue
        if ch in "'\"":
            q = ch
            has = True
            i += 1
            continue
        if ch.isspace() or ch == ",":
            if cur or has:
                words.append("".join(cur))
            cur, has = [], False
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur or has:
        words.append("".join(cur))
    return words


def _cmd_name(w: str) -> str:
    b = re.split(r"[\\/]", w)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if b.endswith(ext):
            return b[: -len(ext)]
    return b


_PREFIXES = {"&", ".", "call", "exec", "sudo", "time", "nohup", "command", "builtin", "env", "start", "cmd.exe"}


def _strip_prefix(words: list[str]) -> list[str]:
    w = list(words)
    while w:
        if len(w) >= 2 and w[1] == "=" and w[0].startswith("$"):  # $x = komut
            w = w[2:]
            continue
        if w[0].lower() in _PREFIXES - {"cmd.exe"} or re.match(r"^[A-Za-z_]\w*=", w[0]):
            w = w[1:]
            continue
        break
    return w


_REDIR = re.compile(r"^(?:\d|\*)?(>>?)(.*)$")
_NULL_TARGETS = {"$null", "nul", "null", "/dev/null", "nul:", "\\\\.\\nul"}


def _split_redirects(args: list[str]) -> tuple[list[str], list[str]]:
    rest, targets = [], []
    i = 0
    while i < len(args):
        a = args[i]
        m = _REDIR.match(a)
        if m:
            t = m.group(2)
            if not t and i + 1 < len(args):
                t = args[i + 1]
                i += 1
            if t and not t.startswith("&") and t.lower() not in _NULL_TARGETS:
                targets.append(t)
            i += 1
            continue
        if a == "<" or a.startswith("<"):
            i += 2 if a == "<" else 1
            continue
        rest.append(a)
        i += 1
    return rest, targets


def _is_flag(a: str) -> bool:
    return len(a) > 1 and a[0] == "-" and not a[1:2].isdigit()


def _flag_value(args: list[str], names: tuple[str, ...]) -> list[str]:
    out = []
    for i, a in enumerate(args):
        al = a.lower()
        for n in names:
            if al == n and i + 1 < len(args):
                out.append(args[i + 1])
            elif al.startswith(n + ":") and len(al) > len(n) + 1:
                out.append(a[len(n) + 1:])
    return out


_VALUE_FLAGS = ("-value", "-encoding", "-itemtype", "-type", "-name", "-newname", "-filter", "-include",
                "-exclude", "-erroraction", "-ea", "-warningaction", "-wa", "-stream", "-inputobject",
                "-delimiter", "-width", "-credential", "-destination", "-dest", "-path", "-literalpath",
                "-lp", "-pspath", "-filepath", "-argumentlist", "-args", "-workingdirectory", "-verb",
                "-windowstyle", "-outvariable", "-ov", "-errorvariable", "-ev")
_PATH_FLAGS = ("-path", "-literalpath", "-lp", "-pspath", "-filepath")


def _positionals(args: list[str]) -> list[str]:
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if _is_flag(a):
            if a.lower() in _VALUE_FLAGS and ":" not in a:
                skip = True
            continue
        out.append(a)
    return out


_DEL_CMDS = {"remove-item", "rm", "ri", "del", "erase", "rmdir", "rd", "rimraf"}
_WRITE_FIRST = {"set-content", "add-content", "ac", "out-file", "new-item", "ni", "tee-object",
                "clear-content", "clc", "rename-item", "rni", "ren"}
_WRITE_ALL = {"mkdir", "md", "touch", "truncate", "tee"}
_COPY_CMDS = {"copy-item", "cpi", "copy", "cp", "move-item", "mi", "move", "mv", "robocopy", "xcopy"}
_MOVE_CMDS = {"move-item", "mi", "move", "mv"}
_CD_CMDS = {"cd", "set-location", "sl", "chdir", "pushd", "push-location"}
_SHELLS_PS = {"powershell", "pwsh"}
_SHELLS_SH = {"bash", "sh", "zsh", "dash"}
_PY = {"python", "python3", "py", "pythonw"}
_PY_DEL = re.compile(r"(?:rmtree|removedirs|rmdir|remove|unlink)\s*\(\s*[rRbBuU]?(['\"])(.+?)\1")

_SYS_ALWAYS = {
    "diskpart": "diskpart", "bcdedit": "bcdedit", "bootrec": "bootrec", "format-volume": "Format-Volume",
    "clear-disk": "Clear-Disk", "initialize-disk": "Initialize-Disk", "remove-partition": "Remove-Partition",
    "stop-computer": "Stop-Computer", "restart-computer": "Restart-Computer", "shutdown": "shutdown",
    "set-mppreference": "Set-MpPreference", "add-mppreference": "Add-MpPreference", "fsutil": "fsutil",
    "takeown": "takeown", "mountvol": "mountvol",
}


def _sys(rule: str, what: str) -> Verdict:
    return Verdict("disk_sistem", rule, f"Sistem komutu engellendi: {what}")


def _check_git(args: list[str]) -> Verdict | None:
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    if i >= len(args):
        return None
    sub = args[i].lower()
    rest = args[i + 1:]
    low = [x.lower() for x in rest]

    def v(rule: str, why: str, kill: bool = True, cat: str = "git") -> Verdict:
        return Verdict(cat, rule, why, kill)

    if sub == "push":
        return v("git-push", "git push engellendi: Jev dışarıya kod göndermez.", cat="yayin")
    if sub == "credential" or sub.startswith("credential-"):
        return v("git-credential", "git kimlik deposuna erişim engellendi.", cat="kimlik")
    if sub == "reset":
        hard = any(x in ("--hard", "--merge", "--keep") for x in low)
        return v("git-reset", "git reset engellendi: geri almayı Jev yapar.", kill=hard)
    if sub == "clean":
        return v("git-clean", "git clean engellendi.")
    if sub == "switch":
        return v("git-switch", "Dal değiştirme engellendi (git switch).")
    if sub == "checkout":
        if "--" in rest:
            return None
        branchy = any(x in ("-b", "-B", "--orphan", "--detach", "-f", "--force") for x in rest)
        paths = [x for x in rest if not x.startswith("-")]
        if not branchy and paths and all(x in (".", "..") or "*" in x or re.search(r"\.\w{1,8}$", x)
                                         for x in paths):
            return None
        return v("git-checkout", "Dal değiştirme engellendi (git checkout). Dosya geri yüklemek için: git checkout -- <dosya>")
    if sub == "rebase":
        return v("git-rebase", "git rebase engellendi.")
    if sub == "branch" and any(x in ("-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force", "-c", "-C",
                                     "--copy") for x in rest):
        return v("git-branch", "Dal silme/taşıma engellendi.")
    if sub == "stash":
        if low and low[0] in ("list", "show"):
            return None
        return v("git-stash", "git stash engellendi.", kill=False)
    if sub == "tag":
        if any(x in ("-d", "--delete", "-f", "--force") for x in rest):
            return v("git-tag", "Etiket silme engellendi.", kill=False)
        return None
    if sub in ("commit", "merge", "cherry-pick", "revert", "am", "init", "notes"):
        return v(f"git-{sub}", f"git {sub} engellendi: commit'i Jev atar.", kill=False)
    if sub in ("filter-branch", "filter-repo", "update-ref", "replace", "gc", "prune", "symbolic-ref"):
        return v(f"git-{sub}", f"git {sub} engellendi.")
    if sub == "reflog" and low and low[0] in ("expire", "delete"):
        return v("git-reflog", "git reflog temizliği engellendi.")
    if sub == "worktree" and low and low[0] in ("remove", "prune", "move"):
        return v("git-worktree", "git worktree değişikliği engellendi.")
    if sub == "config" and any(x in ("--global", "--system") for x in low):
        return v("git-config", "Genel git ayarını değiştirme engellendi.")
    return None


_PUBLISH = [
    ("npm", "publish"), ("pnpm", "publish"), ("yarn", "publish"), ("twine", "upload"), ("docker", "push"),
    ("cargo", "publish"), ("poetry", "publish"), ("gem", "push"), ("dotnet", "nuget"), ("flit", "publish"),
    ("hatch", "publish"), ("uv", "publish"),
]


def _check_named(name: str, args: list[str], cwd: Path, project: Path, depth: int) -> Verdict | None:
    low = [a.lower() for a in args]
    if name in _SYS_ALWAYS:
        return _sys(name, _SYS_ALWAYS[name])
    if name == "format" and any(re.match(r"^[a-zA-Z]:\\?$", a) or a.lower().startswith("/fs:") for a in args):
        return _sys("format", "format")
    if name == "reg" and low and low[0] in ("delete", "import", "restore", "unload", "load"):
        return _sys("reg", f"reg {low[0]}")
    if name == "reg" and low and low[0] == "add" and any(a.startswith(("hklm", "hkey_local_machine")) for a in low):
        return _sys("reg", "reg add HKLM")
    if name in ("vssadmin", "wbadmin") and any(a in ("delete", "resize") for a in low):
        return _sys(name, f"{name} delete")
    if name == "wmic" and "shadowcopy" in low and "delete" in low:
        return _sys("wmic", "wmic shadowcopy delete")
    if name == "cipher" and any(a.startswith("/w") for a in low):
        return _sys("cipher", "cipher /w")
    if name == "sc" and low and low[0] in ("delete", "stop", "config", "create", "failure"):
        return _sys("sc", f"sc {low[0]}")
    if name == "net" and low and low[0] in ("user", "localgroup", "accounts", "stop", "share"):
        return _sys("net", f"net {low[0]}")
    if name in ("cmdkey", "vaultcmd"):
        return Verdict("kimlik", name, f"Kimlik bilgisi aracı engellendi: {name}")
    if name == "gh":
        if low and low[0] == "auth":
            return Verdict("kimlik", "gh-auth", "gh auth engellendi.")
        if low and low[0] not in ("--version", "version", "help", "--help"):
            return Verdict("yayin", "gh", "GitHub CLI işlemi engellendi: Jev dışarıya yayın yapmaz.")
    for tool, sub in _PUBLISH:
        if name == tool and sub in low:
            return Verdict("yayin", f"{tool}-{sub}", f"Paket yayınlama engellendi: {tool} {sub}")
    if name == "git":
        return _check_git(args)

    # silme
    if name in _DEL_CMDS:
        cmd_flags = name in ("del", "erase", "rd", "rmdir")
        for a in _flag_value(args, _PATH_FLAGS) + [
                p for p in _positionals(args) if not (cmd_flags and re.match(r"^/[sqfpa]$", p, re.I))]:
            vv = classify_delete(a, cwd, project)
            if vv:
                return vv
        return None
    # yazma
    if name in _WRITE_FIRST or name == "sc":
        pos = _positionals(args)
        for a in _flag_value(args, _PATH_FLAGS) + pos[:1]:
            vv = classify_write(a, cwd, project)
            if vv:
                return vv
        return None
    if name in _WRITE_ALL:
        for a in _positionals(args):
            vv = classify_write(a, cwd, project)
            if vv:
                return vv
        return None
    if name in _COPY_CMDS:
        pos = _positionals(args)
        dests = _flag_value(args, ("-destination", "-dest"))
        srcs = _flag_value(args, _PATH_FLAGS)
        if not dests and len(pos) >= 2:
            if name in ("robocopy", "xcopy"):
                dests, srcs = [pos[1]], srcs + [pos[0]]
            else:
                dests, srcs = [pos[-1]], srcs + pos[:-1]
        else:
            srcs = srcs + pos
        for a in dests:
            vv = classify_write(a, cwd, project)
            if vv:
                return vv
        if name in _MOVE_CMDS:
            for a in srcs:
                vv = classify_delete(a, cwd, project)
                if vv:
                    return vv
        return None
    # iç içe kabuklar
    if depth < 4:
        inner = _inner_script(name, args)
        if inner is not None:
            return _analyze(inner, cwd, project, depth + 1)
    if name in _PY and "-c" in args:
        code = " ".join(args[args.index("-c") + 1:])
        for m in _PY_DEL.finditer(code):
            vv = classify_delete(m.group(2), cwd, project)
            if vv and vv.category in ("disk_sistem", "proje_disi"):
                return vv
    return None


def _inner_script(name: str, args: list[str]) -> str | None:
    low = [a.lower() for a in args]
    if name in _SHELLS_PS:
        for i, a in enumerate(low):
            if a in ("-encodedcommand", "-enc", "-e", "-ec") and i + 1 < len(args):
                try:
                    return base64.b64decode(args[i + 1]).decode("utf-16-le", "replace")
                except (ValueError, TypeError):
                    return None
            if len(a) >= 2 and "-command".startswith(a) and a != "-":
                return " ".join(args[i + 1:])
        pos, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a.startswith("-"):
                skip = a.lower() in ("-executionpolicy", "-ep", "-inputformat", "-outputformat", "-windowstyle",
                                     "-version", "-configurationname", "-workingdirectory", "-file", "-f")
                continue
            pos.append(a)
        return " ".join(pos) if pos else None
    if name == "cmd":
        for i, a in enumerate(low):
            if a in ("/c", "/k", "/r"):
                return " ".join(args[i + 1:])
        return None
    if name in _SHELLS_SH:
        for i, a in enumerate(args):
            if a.startswith("-") and not a.startswith("--") and "c" in a[1:] and i + 1 < len(args):
                return args[i + 1]
        return None
    if name in ("iex", "invoke-expression"):
        return " ".join(a for a in args if a.lower() != "-command")
    if name in ("start-process", "saps"):
        fp = _flag_value(args, ("-filepath",)) or _positionals(args)[:1]
        al = _flag_value(args, ("-argumentlist", "-args"))
        return " ".join(fp + al) if fp else None
    return None


# Veri taşıyan argümanlar kimlik taramasına girmez (ör. Set-Content -Value "...").
def _data_words(name: str, args: list[str]) -> set[int]:
    idx: set[int] = set()
    low = [a.lower() for a in args]
    if name in ("echo", "write-output", "write-host", "printf", "write", "write-verbose"):
        return set(range(len(args)))
    if name in ("set-content", "add-content", "sc", "ac", "out-file", "tee-object", "new-item", "ni"):
        for i, a in enumerate(low):
            if a in ("-value", "-inputobject") and i + 1 < len(args):
                idx.add(i + 1)
        pos_i = [i for i, a in enumerate(args) if not _is_flag(a) and (i == 0 or low[i - 1] not in _VALUE_FLAGS)]
        idx.update(pos_i[1:])
    return idx


# --- indir ve çalıştır ---------------------------------------------------------
# İnternetten gelen metni doğrudan bir yorumlayıcıya vermek (irm … | iex, curl … | sh) kimsenin denetlemediği
# kodu çalıştırmaktır. Dosyaya indirmek (iwr -OutFile, curl -o) ve API okumak (irm … | ConvertTo-Json) serbesttir.

_FETCH = r"(?:iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)(?:\.exe)?"
_FETCH_ANY = rf"(?:(?<![\w-]){_FETCH}(?![\w-])|\.download(?:string|data)\s*\()"
_SHELL_EXEC = r"(?:iex|invoke-expression|sh|bash|zsh|dash|ksh|pwsh|powershell|cmd)(?:\.exe)?(?=$|[\s;&|)])"
_CODE_EXEC = r"(?:python3?|py|node|perl|ruby)(?:\.exe)?(?:\s+-)?(?=\s*(?:$|[;&|)]))"
_DOWNLOAD_EXEC = [
    # indirici … | yorumlayıcı (arada başka borular olabilir; komut ayırıcı ; ya da satır sonu geçilmez)
    re.compile(rf"{_FETCH_ANY}[^;\n]*\|\s*(?:&\s*)?(?:sudo\s+)?(?:{_SHELL_EXEC}|{_CODE_EXEC})", re.M),
    # iex (indirici …), [scriptblock]::Create((irm …)), iex "$(irm …)"
    re.compile(rf"(?<![\w-])(?:iex|invoke-expression|\[scriptblock\]::create)(?![\w-])[^;\n|]*?{_FETCH_ANY}", re.M),
    # sh -c "$(curl …)", bash <(curl …)
    re.compile(rf"(?<![\w.-])(?:sh|bash|zsh|dash|ksh|source)(?![\w-])[^;\n|]*?(?:\$|<)\(\s*{_FETCH}(?![\w-])", re.M),
]


def _code_shape(script: str) -> str:
    """Tırnak içindeki veri boşlukla örtülür (yazılan metin yanlış alarm vermesin). Çift tırnak içindeki $( … )
    alt ifadesi ise çalışır (PowerShell ve bash): o kalır."""
    out: list[str] = []
    q = None
    depth = 0
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if q == "'":
            out.append(ch if ch == "'" else " ")
            if ch == "'":
                q = None
            i += 1
            continue
        if q == '"':
            if depth:
                depth += (ch == "(") - (ch == ")")
                out.append(ch)
            elif ch == "$" and i + 1 < n and script[i + 1] == "(":
                depth = 1
                out.append("$(")
                i += 1
            elif ch in "\\`" and i + 1 < n:
                out.append("  ")
                i += 1
            else:
                out.append(ch if ch == '"' else " ")
                if ch == '"':
                    q = None
            i += 1
            continue
        if ch in "'\"":
            q = ch
        out.append(ch)
        i += 1
    return "".join(out)


def _download_exec(script: str) -> Verdict | None:
    shape = _code_shape(script).lower()
    for rx in _DOWNLOAD_EXEC:
        m = rx.search(shape)
        if m:
            what = " ".join(m.group(0).split())
            return Verdict("indir_calistir", "indir-calistir",
                           f"İnternetten indirilen betiği doğrudan çalıştırma engellendi ({_clip(what, 80)}). "
                           "Aracı paket yöneticisiyle kur (npm, pip, winget) ya da dosyayı indirip içeriğini incele.")
    return None


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _analyze(script: str, cwd: Path, project: Path, depth: int = 0) -> Verdict | None:
    stack: list[Path] = []
    script = _strip_data(script)
    vv = _download_exec(script)
    if vv:
        return vv
    for seg in _segments(script):
        words = _strip_prefix(_words(seg))
        if not words:
            continue
        name = _cmd_name(words[0])
        args, redirects = _split_redirects(words[1:])
        inner = _inner_script(name, args) if depth < 4 else None
        data = _data_words(name, args)
        scan = " ".join([words[0]] + [a for i, a in enumerate(args) if i not in data and
                                      (inner is None or a not in inner)] + redirects)
        cred = scan_credentials(scan)
        if cred:
            return cred
        for t in redirects:
            vv = classify_write(t, cwd, project)
            if vv:
                return vv
        if name in _CD_CMDS:
            pos = _positionals(args) or _flag_value(args, ("-path", "-literalpath"))
            if pos:
                t = _target(pos[0], cwd)
                if t:
                    if name in ("pushd", "push-location"):
                        stack.append(cwd)
                    cwd = Path(t[0])
            continue
        if name in ("popd", "pop-location"):
            if stack:
                cwd = stack.pop()
            continue
        vv = _check_named(name, args, cwd, project, depth)
        if vv:
            return vv
    return None


def check_command(command: str, project: Path, cwd: Path | None = None) -> Verdict | None:
    """Bir kabuk komutunu (PowerShell, cmd ya da bash) kurallardan geçirir."""
    if not command or not command.strip():
        return None
    project = Path(os.path.realpath(project))
    return _analyze(command, Path(cwd) if cwd else project, project)


def check_read_path(path: str) -> Verdict | None:
    return scan_credentials(path)


def check_tool(tool: str, tin: dict, project: Path) -> Verdict | None:
    """Claude Code araç çağrısı (PreToolUse) için karar."""
    project = Path(os.path.realpath(project))
    tin = tin if isinstance(tin, dict) else {}
    if tool in ("Bash", "PowerShell"):
        return check_command(str(tin.get("command") or ""), project)
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = str(tin.get("file_path") or tin.get("notebook_path") or "")
        return classify_write(p, project, project) if p else None
    if tool in ("Read", "Grep", "Glob", "LS", "NotebookRead"):
        for key in ("file_path", "path", "pattern", "notebook_path"):
            v = scan_credentials(str(tin.get(key) or ""))
            if v:
                return v
    return None


def check_codex_item(item: dict, project: Path) -> Verdict | None:
    """Codex --json öğesi (command_execution / file_change) için karar."""
    t = item.get("type")
    if t == "command_execution":
        return check_command(str(item.get("command") or ""), project)
    if t == "file_change":
        for ch in item.get("changes") or []:
            p = ch.get("path") if isinstance(ch, dict) else None
            if p:
                v = classify_write(str(p), Path(project), Path(project))
                if v:
                    return v
    return None


def record(run_dir: Path, verdict: Verdict, *, provider: str, tool: str, detail: str, agent: str | None,
           task: str | None, action: str) -> dict:
    """Kararı guard.jsonl'a yazar. action: engellendi | durduruldu | kaydedildi."""
    from .util import append_jsonl, iso, shorten
    rec = {"ts": iso(), "saglayici": provider, "ajan": agent, "gorev": task, "arac": tool,
           "girdi": shorten(detail, 400), "eylem": action, **verdict.to_dict()}
    append_jsonl(Path(run_dir) / "guard.jsonl", rec)
    return rec


def hook_settings(run_dir: Path, project: Path) -> dict:
    """Claude için --settings içeriği (PreToolUse kancası)."""
    exe = sys.executable
    script = Path(__file__).resolve().parent / "kanca.py"
    cmd = f'"{exe}" "{script}" --saglayici claude --kosu "{run_dir}" --proje "{project}"'
    return {"hooks": {"PreToolUse": [{
        "matcher": "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|Read|Grep|Glob",
        "hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jev.guard", description="Jev felaket koruması kancası")
    ap.add_argument("--saglayici", default="claude")
    ap.add_argument("--kosu", required=True, help="koşu klasörü (guard.jsonl buraya yazılır)")
    ap.add_argument("--proje", required=True, help="proje klasörü")
    args = ap.parse_args(argv)
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    tool = str(payload.get("tool_name") or "")
    tin = payload.get("tool_input") or {}
    try:
        verdict = check_tool(tool, tin, Path(args.proje))
    except Exception as e:  # koruma hatası aracı engellemesin; ama kayda geçsin
        try:
            from .util import append_jsonl, iso
            append_jsonl(Path(args.kosu) / "guard.jsonl", {"ts": iso(), "hata": repr(e), "arac": tool})
        except Exception:
            pass
        return 0
    if verdict is None:
        return 0
    detail = tin.get("command") or tin.get("file_path") or tin.get("path") or json.dumps(tin, ensure_ascii=False)
    record(Path(args.kosu), verdict, provider=args.saglayici, tool=tool, detail=str(detail),
           agent=os.environ.get("JEV_AJAN"), task=os.environ.get("JEV_GOREV"), action="engellendi")
    sys.stderr.write(f"JEV KORUMASI: {verdict.reason} Bu işlem yapılamaz. Görevine başka bir yoldan devam et; "
                     f"ilerleyemiyorsan status=blocked de.\n")
    sys.stderr.flush()
    return 2


if __name__ == "__main__":
    sys.exit(main())
