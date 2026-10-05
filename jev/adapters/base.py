"""Ajan adaptörleri için ortak parçalar: çağrı tanımı, sonuç, etkinlik, süreç yürütme.

Süreçler yeni bir süreç grubunda başlatılır (Ctrl+C alt süreçlere gitmez; orkestratör
onları kendisi kapatır). Prompt stdin'den verilir. stdout satır satır okunur; her satır
ham kayda yazılır ve ayrıştırıcıya verilir. Zaman aşımı, iptal ya da koruma kararıyla
tüm süreç ağacı `taskkill /T /F` ile kapatılır.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..quota import matches_any, parse_reset

ERROR_KINDS = ("none", "timeout", "crash", "quota", "schema", "auth", "transient", "cancelled", "guard")
ACTIVITY_KINDS = ("thinking", "reading", "editing", "command", "testing", "message", "guarded")

_TEST_RX = re.compile(
    r"(?i)(?:\b(?:pytest|py\.test|nose2|tox|nox|jest|vitest|mocha|phpunit|rspec|ctest|invoke-pester)\b"
    r"|\bunittest\b|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|\b(?:go|cargo|dotnet|mvn|gradlew?|mix|deno)\s+test\b)")
# Yol tırnak içindeyse boşluk içerebilir ('Yeni Klasör\not.txt'); tırnaksızsa boşlukta biter.
_WRITE_RX = re.compile(
    r"(?i)^\s*(?:set-content|add-content|out-file|new-item|sc|ac)\b.*?(?:-(?:literal)?path\s+|-filepath\s+|\s)"
    r"(?:(['\"])(?P<q>[^'\"]+\.[A-Za-z0-9]{1,8})\1|(?P<p>[^'\"\s;|]+\.[A-Za-z0-9]{1,8}))")
_READ_RX = re.compile(r"(?i)^\s*(?:get-content|gc|cat|type)\s+(?:-(?:literal)?path\s+)?"
                      r"(?:(['\"])(?P<q>[^'\"]+)\1|(?P<p>[^'\"\s;|]+))")

ActivityFn = Callable[["AgentActivity"], None]


@dataclass
class AgentActivity:
    kind: str  # thinking | reading | editing | command | testing | message | guarded
    text: str
    path: str | None = None


@dataclass
class CallSpec:
    """Bir ajan çağrısının tanımı. Gateway doldurur, adaptör çalıştırır."""
    agent: str
    model: str
    provider: str
    prompt: str
    cwd: Path
    mode: str  # readonly | full
    effort: str
    timeout_s: int
    call_dir: Path
    call_name: str
    phase: str  # plan | worker | brain | review | fix | repair | test
    schema: dict | None = None
    schema_name: str | None = None
    task_id: str | None = None
    attempt: int = 0
    round: int = 1
    run_dir: Path | None = None
    project: Path | None = None
    guard: bool = False
    full_access: bool = True
    cancel: threading.Event | None = None
    extra: dict = field(default_factory=dict)
    images: list = field(default_factory=list)  # ajanın bakacağı resimler (son kontroldeki kareler)
    tools: str | None = None  # None: kipin araçları · "": araçsız, yalnızca düşünür (yeni projede plan, onarım)
    web: bool = False  # internette arama ve sayfa okuma (araştırma görevleri)

    def path(self, suffix: str) -> Path:
        return self.call_dir / f"{self.call_name}.{suffix}"


@dataclass
class AgentResult:
    ok: bool
    structured: Any = None
    text: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    usage: dict = field(default_factory=dict)
    quota_hit: bool = False
    reset_at: _dt.datetime | None = None
    quota_shared: bool = False  # hesabın ortak kota penceresi doldu: aynı sağlayıcının bütün ajanları soğur
    error_kind: str = "none"
    error_text: str = ""
    paths: dict = field(default_factory=dict)
    guard: list = field(default_factory=list)  # koruma kayıtları (Codex canlı izleme)

    def summary(self) -> dict:
        return {"ok": self.ok, "exit_code": self.exit_code, "duration_s": round(self.duration_s, 1),
                "error_kind": self.error_kind, "error_text": self.error_text[-2000:], "usage": self.usage,
                "quota_hit": self.quota_hit, "reset_at": self.reset_at.isoformat() if self.reset_at else None,
                "quota_shared": self.quota_shared, "paths": self.paths}


def empty_usage() -> dict:
    return {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0,
            "cost_usd": 0.0}


class AgentAdapter:
    provider = ""

    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, spec: CallSpec, on_activity: ActivityFn) -> AgentResult:  # pragma: no cover - arayüz
        raise NotImplementedError

    # --- hata sınıflandırma (yalnızca CLI'ın kendi hata kanallarında) -----------------------
    def is_refusal(self, text: str) -> bool:
        """Modelin güvenlik filtresi isteği yanıtlamadı ([kota] ret_kaliplari)."""
        return matches_any(text or "", (self.cfg.get("kota", "ret_kaliplari", default=[]) or []))

    def classify(self, error_text: str) -> tuple[str, _dt.datetime | None]:
        kota = self.cfg.get("kota", default={}) or {}
        if self.is_refusal(error_text):
            return "refusal", None
        if matches_any(error_text, kota.get("kaliplar", [])):
            return "quota", parse_reset(error_text)
        if matches_any(error_text, kota.get("oturum_kaliplari", [])):
            return "auth", None
        if matches_any(error_text, kota.get("gecici_kaliplar", [])):
            return "transient", None
        return "crash", None


# --- etkinlik yardımcıları ----------------------------------------------------------------

def is_test_command(cmd: str) -> bool:
    return bool(_TEST_RX.search(cmd or ""))


def unwrap_shell(cmd: str) -> str:
    """Codex'in `powershell.exe -Command "..."` sarmalını açar; görüntüleme içindir."""
    from .. import guard
    try:
        words = guard._words(cmd)
        if not words:
            return cmd
        name = guard._cmd_name(words[0])
        if name in ("powershell", "pwsh", "cmd", "bash", "sh"):
            inner = guard._inner_script(name, words[1:])
            if inner:
                return inner.strip()
    except Exception:
        pass
    return cmd


def command_activity(cmd: str) -> AgentActivity:
    shown = unwrap_shell(cmd or "")
    if is_test_command(shown):
        return AgentActivity("testing", shown)
    first = re.split(r"[;\n]|&&|\|\|", shown, maxsplit=1)[0]
    m = _WRITE_RX.match(first)
    if m:
        return AgentActivity("editing", shown, m.group("q") or m.group("p"))
    m = _READ_RX.match(first)
    if m:
        return AgentActivity("reading", shown, m.group("q") or m.group("p"))
    return AgentActivity("command", shown)


# --- süreç yürütme ------------------------------------------------------------------------

@dataclass
class ProcOutcome:
    exit_code: int | None
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False
    killed: bool = False  # koruma kararıyla durduruldu
    stderr_tail: str = ""
    start_error: str = ""


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:  # pragma: no cover - Windows dışı
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass


def child_env(extra: dict | None = None, strip: Callable[[str], bool] | None = None) -> dict:
    """Ajan alt sürecinin ortamı. Jev modelinin anahtarı (TYPESAFE_*) hiçbir ajana geçmez."""
    env = {k: v for k, v in os.environ.items()
           if not (strip and strip(k)) and not k.upper().startswith("TYPESAFE_")}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def run_process(argv: list[str], *, cwd: Path, env: dict, stdin_text: str, stdout_path: Path,
                stderr_path: Path, timeout_s: float, on_line: Callable[[str], None],
                cancel: threading.Event | None = None, kill_request: threading.Event | None = None,
                poll_s: float = 0.2) -> ProcOutcome:
    """Süreci çalıştırır; stdout satırlarını `on_line`a verir. KeyboardInterrupt'ta ağacı kapatıp yeniden yükseltir."""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(cwd), env=env, creationflags=flags,
                                start_new_session=(os.name != "nt"))
    except OSError as e:
        stderr_path.write_text(f"başlatılamadı: {e}\n", encoding="utf-8")
        return ProcOutcome(None, 0.0, start_error=str(e))

    err_buf: list[bytes] = []
    err_size = [0]

    def feed_stdin() -> None:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    def read_stdout() -> None:
        with stdout_path.open("wb") as raw:
            for bline in iter(proc.stdout.readline, b""):
                raw.write(bline)
                raw.flush()
                try:
                    on_line(bline.decode("utf-8", "replace").rstrip("\r\n"))
                except Exception as e:  # ayrıştırıcı hatası süreci durdurmasın
                    raw.write(f"\n[jev: ayrıştırıcı hatası: {e!r}]\n".encode("utf-8"))

    def read_stderr() -> None:
        with stderr_path.open("wb") as raw:
            for chunk in iter(lambda: proc.stderr.read1(4096) if hasattr(proc.stderr, "read1")
                              else proc.stderr.read(4096), b""):
                raw.write(chunk)
                raw.flush()
                err_buf.append(chunk)
                err_size[0] += len(chunk)
                while err_size[0] > 65536 and len(err_buf) > 1:
                    err_size[0] -= len(err_buf.pop(0))

    threads = [threading.Thread(target=f, daemon=True, name=f"jev-{f.__name__}")
               for f in (feed_stdin, read_stdout, read_stderr)]
    for t in threads:
        t.start()

    out = ProcOutcome(None, 0.0)
    try:
        while True:
            try:
                out.exit_code = proc.wait(timeout=poll_s)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                out.cancelled = True
            elif kill_request is not None and kill_request.is_set():
                out.killed = True
            elif time.monotonic() - t0 > timeout_s:
                out.timed_out = True
            if out.cancelled or out.killed or out.timed_out:
                kill_tree(proc.pid)
                try:
                    out.exit_code = proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
    except KeyboardInterrupt:
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=15)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            pass
        raise
    finally:
        for t in threads[1:]:
            t.join(timeout=5)
        out.duration_s = time.monotonic() - t0
        # Okuyucusu bitmiş boruları kapat. Arka planda kalan bir torun süreç boruyu hâlâ tutuyorsa
        # okuyucu bloklu kalır; o boruyu kapatmaya çalışmak da bloklayacağından dokunulmaz.
        for t, pipe in zip(threads[1:], (proc.stdout, proc.stderr)):
            if not t.is_alive():
                try:
                    pipe.close()
                except OSError:
                    pass
    out.stderr_tail = b"".join(err_buf).decode("utf-8", "replace")[-8000:]
    return out


def write_prompt(spec: CallSpec) -> Path:
    p = spec.path("prompt.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(spec.prompt, encoding="utf-8")
    return p


def base_paths(spec: CallSpec) -> dict:
    return {"prompt": str(spec.path("prompt.md")), "stdout": str(spec.path("stdout.jsonl")),
            "stderr": str(spec.path("stderr.txt")), "out": str(spec.path("out.json"))}
