"""Doğrulama komutları: PowerShell'de, proje klasöründe, zaman aşımıyla.

Komut, BOM'lu UTF-8 bir .ps1 dosyasına yazılıp `powershell -File` ile çalıştırılır. Windows PowerShell 5.1
BOM'suz betikleri ANSI kod sayfasıyla okur; `-Command` ile verilen Türkçe karakterler de bozulabilir.

Başarı ölçüsü:
- `exit N` çağrılırsa N.
- Son deyim bir komut hattıysa (ör. `python -m pytest`, `Get-Item x`) onun `$?` değeri; yerel programlarda
  bu çıkış koduna eşittir.
- Son deyim `if`/`foreach` gibi bir denetim deyimiyse başarılı sayılır (ör. `...; if ($LASTEXITCODE -ne 1) { exit 1 }`).
  PowerShell `if`ten sonra `$?`'yi güncellemediği için bu ayrım betiğin kendi sözdizim ağacından yapılır.
- Çıktının son satırı tam olarak `False` ise başarısız sayılır (ör. tek başına `Test-Path yok.txt`).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .adapters.base import child_env, kill_tree
from .util import tail_lines

_PRELUDE = """$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8
$global:LASTEXITCODE = 0
"""
_EPILOGUE = """
$__jev_ok = $?
$__jev_code = $LASTEXITCODE
$__jev_last = $null
try {
  $__jev_ast = [System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$null, [ref]$null)
  $__jev_st = @($__jev_ast.EndBlock.Statements)
  for ($__jev_i = 1; $__jev_i -lt $__jev_st.Count; $__jev_i++) {
    if ($__jev_st[$__jev_i].Extent.Text -eq '$__jev_ok = $?') { $__jev_last = $__jev_st[$__jev_i - 1]; break }
  }
} catch {}
if ($__jev_last -ne $null -and -not ($__jev_last -is [System.Management.Automation.Language.PipelineAst])) { exit 0 }
if (-not $__jev_ok) { if ($__jev_code) { exit $__jev_code }; exit 1 }
exit 0
"""


@dataclass
class CmdResult:
    command: str
    passed: bool
    exit_code: int | None
    output: str
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False

    def tail(self, n: int = 150) -> str:
        return tail_lines(self.output, n)

    def to_dict(self, n: int = 60) -> dict:
        return {"command": self.command, "passed": self.passed, "exit_code": self.exit_code,
                "duration_s": round(self.duration_s, 1), "timed_out": self.timed_out,
                "output_tail": self.tail(n)}


def _script_dir(script_dir: Path | None) -> Path:
    d = script_dir or Path(tempfile.gettempdir()) / "jev-dogrulama"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_command(command: str, cwd: Path, timeout_s: float = 600, *, script_dir: Path | None = None,
                env_extra: dict | None = None, cancel: threading.Event | None = None) -> CmdResult:
    """Bir PowerShell komutunu çalıştırır. stdout ve stderr birleştirilir."""
    d = _script_dir(script_dir)
    script = d / f"dogrula-{uuid.uuid4().hex[:10]}.ps1"
    script.write_text(_PRELUDE + command.strip() + "\n" + _EPILOGUE, encoding="utf-8-sig")
    argv = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script)]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    t0 = time.monotonic()
    chunks: list[bytes] = []
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=child_env(env_extra), creationflags=flags)
    except OSError as e:
        _unlink(script)
        return CmdResult(command, False, None, f"PowerShell başlatılamadı: {e}", 0.0)

    def reader() -> None:
        for chunk in iter(lambda: proc.stdout.read1(8192), b""):
            chunks.append(chunk)

    t = threading.Thread(target=reader, daemon=True, name="jev-dogrulama")
    t.start()
    timed_out = cancelled = False
    try:
        while True:
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                cancelled = True
            elif time.monotonic() - t0 > timeout_s:
                timed_out = True
            if cancelled or timed_out:
                kill_tree(proc.pid)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
    except KeyboardInterrupt:
        kill_tree(proc.pid)
        raise
    finally:
        t.join(timeout=5)
        if not t.is_alive():  # boruyu hâlâ tutan bir torun süreç varsa okuyucu bloklu kalır; o zaman dokunulmaz
            try:
                proc.stdout.close()
            except OSError:
                pass
        _unlink(script)
    out = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
    if timed_out:
        out += f"\n[jev: zaman aşımı, {int(timeout_s)} sn]"
    code = proc.returncode
    passed = code == 0 and not timed_out and not cancelled
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if passed and lines and lines[-1] == "False":
        passed = False
        out += "\n[jev: komut False döndürdü; başarısız sayıldı]"
    return CmdResult(command, passed, code, out, time.monotonic() - t0, timed_out, cancelled)


def _unlink(p: Path) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


def run_all(commands: list[str], cwd: Path, timeout_s: float, *, script_dir: Path | None = None,
            on_result=None, cancel: threading.Event | None = None) -> list[CmdResult]:
    """Komutları sırayla çalıştırır; hepsi çalışır (ilk hatada durmaz)."""
    out = []
    for c in commands:
        if not (c or "").strip():
            continue
        r = run_command(c, cwd, timeout_s, script_dir=script_dir, cancel=cancel)
        out.append(r)
        if on_result:
            on_result(r)
        if r.cancelled:
            break
    return out
