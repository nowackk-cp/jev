"""Küçük yardımcılar: zaman, atomik yazma, slug, metin kısaltma."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(t: _dt.datetime | None = None) -> str:
    return (t or now()).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        try:
            t = _dt.datetime.fromisoformat(s)
            return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None


def local_hhmm(t: _dt.datetime | None) -> str:
    if t is None:
        return "?"
    return t.astimezone().strftime("%H:%M")


def clock(seconds: float) -> str:
    """Süre, ofisteki sayaçla aynı biçimde: 38 → 0:38, 3725 → 1:02:05."""
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_VOWELS = "aıoueiöü"
_ONES = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]


def _spoken_tail(word: str) -> str:
    """Ek uyumu için okunuştaki son sözcük: '08:30' → 'otuz', '12:00' → 'iki', 'Sonnet' → 'sonnet'."""
    w = word.strip().lower()
    m = re.search(r"(?:(\d{1,2}):)?(\d+)$", w)
    if not m:
        return w
    n = int(m.group(2))
    if m.group(1) is not None and n == 0:  # 08:00 → saat okunur
        n = int(m.group(1))
    if n == 0:
        return "sıfır"
    if n % 10:
        return _ONES[n % 10]
    if n % 100:
        return _TENS[(n % 100) // 10]
    return "yüz" if n % 1000 else "bin"


def suffix(word: str, kind: str) -> str:
    """Özel ada ya da saate gelen kesme işaretli hâl eki.
    kind: 'e' yönelme (Luna'ya), 'de' bulunma (08:30'da), 'den' ayrılma (üç → 3'ten), 'in' tamlayan (Opus'un)."""
    tail = _spoken_tail(word)
    vowels = [c for c in tail if c in _VOWELS]
    last = vowels[-1] if vowels else "e"
    back = last in "aıou"
    end_vowel = bool(tail) and tail[-1] in _VOWELS
    hard = bool(tail) and tail[-1] in "çfhkpsşt"
    if kind == "e":
        s = ("y" if end_vowel else "") + ("a" if back else "e")
    elif kind == "de":
        s = ("t" if hard else "d") + ("a" if back else "e")
    elif kind == "den":
        s = ("t" if hard else "d") + ("an" if back else "en")
    elif kind == "in":
        s = ("n" if end_vowel else "") + {"a": "ı", "ı": "ı", "o": "u", "u": "u",
                                         "e": "i", "i": "i", "ö": "ü", "ü": "ü"}[last] + "n"
    else:
        raise ValueError(kind)
    return "'" + s


def ek(word: str, kind: str) -> str:
    """ek('Luna', 'e') → "Luna'ya"; ek('08:30', 'de') → "08:30'da"."""
    return word + suffix(word, kind)


def slugify(text: str, max_len: int = 40) -> str:
    s = text.translate(TR_MAP).lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_len:
        cut = s[:max_len]
        if s[max_len] != "-" and "-" in cut[max_len // 2:]:  # kelimenin ortasından kesme
            cut = cut[:cut.rindex("-")]
        s = cut.rstrip("-")
    return s or "proje"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for i in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows: okuyucu dosyayı kısa süre tutuyor olabilir.
                import time
                time.sleep(0.05 * (i + 1))
        else:
            os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def ends_mid_line(path: Path) -> bool:
    """Dosya yarım bir satırla mı bitiyor (yazan süreç satırın ortasında öldüyse)?"""
    try:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except OSError:  # dosya yok ya da boş
        return False


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lead = "\n" if ends_mid_line(path) else ""  # yeni kayıt yarım kalmış satıra yapışıp kaybolmasın
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(lead + json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def tail_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def tail_file(path: Path, n: int = 200, max_bytes: int = 400_000) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return tail_lines(data, n)


def shorten(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: max(0, n - 1)] + "…"


def jev_home() -> Path:
    """Kullanıcıya ait kalıcı klasör. Testler JEV_HOME ile değiştirir."""
    p = Path(os.environ.get("JEV_HOME") or (Path.home() / ".jev"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def expand_path(s: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(s)))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def refresh_path() -> list[str]:
    """Windows: kayıt defterindeki güncel PATH'in (makine + kullanıcı) bu süreçte eksik olan girdilerini sona
    ekler. Bir ajan winget ile araç kurduğunda Jev yeniden başlatılmadan o aracı bulur (doğrulama ve sonraki
    ajanlar bu süreçten türer). Mevcut sıra korunur. Eklenen girdileri döndürür."""
    if os.name != "nt":
        return []
    import winreg
    found: list[str] = []
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
        except OSError:
            continue
        found += [p.strip() for p in winreg.ExpandEnvironmentStrings(str(val)).split(";") if p.strip()]
    cur = os.environ.get("PATH", "")
    have = {os.path.normcase(os.path.normpath(p)) for p in cur.split(";") if p.strip()}
    added = []
    for p in found:
        key = os.path.normcase(os.path.normpath(p))
        if key not in have:
            have.add(key)
            added.append(p)
    if added:
        os.environ["PATH"] = cur.rstrip(";") + ";" + ";".join(added)
    return added
