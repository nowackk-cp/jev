"""Claude Code adaptörü (`claude -p --output-format stream-json`).

stream-json olayları savunmacı biçimde ayrıştırılır: `system`, `assistant` (text / tool_use / thinking),
`user` (tool_result), `rate_limit_event` (kota penceresi) ve son `result` olayı (`is_error`, `result`,
`structured_output`, `usage`, `total_cost_usd`). Gerçek biçim oturum açıldıktan sonra M0'da doğrulanır
(docs/cli-notlari.md).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .. import guard
from ..discovery import find_cli
from ..quota import epoch_reset
from ..sema import extract_json, schema_text
from ..util import atomic_write_json, shorten
from .base import (AgentActivity, AgentAdapter, AgentResult, CallSpec, ActivityFn, base_paths, child_env,
                   command_activity, empty_usage, run_process, write_prompt)

_ALWAYS_STRIP = {"CLAUDECODE", "CLAUDE_PID", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
_KEEP = {"CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CONFIG_DIR"}
# Hesap genelindeki kota pencereleri: bütün Claude modelleri (Opus, Sonnet) aynı pencereyi kullanır.
# Modele özgü pencereler (ör. seven_day_opus) yalnızca o modeli durdurur.
_SHARED_WINDOWS = {"five_hour", "seven_day"}


def env_filter(inside_claude: bool):
    """Alt sürece geçmeyecek değişkenler. Claude Code oturumunun içinden çalışıyorsak o oturuma ait
    CLAUDE_CODE_* değişkenleri de temizlenir; kullanıcının kendi terminalinde ise onun ayarlarına dokunulmaz."""
    def strip(k: str) -> bool:
        ku = k.upper()
        if ku in _KEEP:
            return False
        if ku in _ALWAYS_STRIP or ku.startswith(("CLAUDE_AGENT_SDK", "CLAUDE_PREVIEW")):
            return True
        return inside_claude and ku.startswith("CLAUDE_CODE_")
    return strip


def _rel(path: str, cwd: Path) -> str:
    try:
        p = Path(path)
        if p.is_absolute():
            return os.path.relpath(p, cwd) if str(p).lower().startswith(str(cwd).lower()) else str(p)
    except (ValueError, OSError):
        pass
    return path


def tool_activity(name: str, inp: dict, cwd: Path) -> AgentActivity:
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Bash", "PowerShell"):
        return command_activity(str(inp.get("command") or ""))
    path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    shown = _rel(path, cwd) if path else ""
    if name in ("Read", "NotebookRead", "LS"):
        return AgentActivity("reading", f"{shown} okuyor", path or None)
    if name == "Grep":
        return AgentActivity("reading", f"arıyor: {inp.get('pattern', '')}", path or None)
    if name == "Glob":
        return AgentActivity("reading", f"dosya arıyor: {inp.get('pattern', '')}", path or None)
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return AgentActivity("editing", f"{shown} düzenliyor", path or None)
    if name == "TodoWrite":
        return AgentActivity("thinking", "Yapılacaklar listesini güncelliyor")
    if name in ("WebFetch", "WebSearch"):
        return AgentActivity("reading", f"web: {inp.get('url') or inp.get('query') or ''}")
    if name in ("Task", "Agent"):
        return AgentActivity("thinking", f"Alt görev: {inp.get('description', '')}")
    return AgentActivity("command", name)


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


class ClaudeParser:
    def __init__(self, on_activity: ActivityFn, cwd: Path):
        self.on_activity = on_activity
        self.cwd = cwd
        self.result: dict | None = None
        self.last_text = ""
        self.errors: list[str] = []
        self.init: dict | None = None
        self.rate_limit: dict | None = None  # son rate_limit_event bilgisi
        self.guard_blocks = 0
        self.refused = False  # API'nin güvenlik reddi (stop_reason "refusal")

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self.init = ev
            self.on_activity(AgentActivity("thinking", "Düşünüyor…"))
        elif t == "assistant":
            if ev.get("error"):
                self.errors.append(str(ev.get("error")))
            msg = ev.get("message") or {}
            if msg.get("stop_reason") == "refusal":
                self.refused = True
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text"):
                    self.last_text = str(b["text"])
                    self.on_activity(AgentActivity("message", shorten(self.last_text, 300)))
                elif bt == "thinking":
                    self.on_activity(AgentActivity("thinking", shorten(str(b.get("thinking") or ""), 200)
                                                   or "Düşünüyor…"))
                elif bt == "tool_use":
                    self.on_activity(tool_activity(str(b.get("name") or ""), b.get("input") or {}, self.cwd))
        elif t == "user":
            msg = ev.get("message") or {}
            for b in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    txt = _block_text(b.get("content"))
                    if "JEV KORUMASI" in txt:
                        self.guard_blocks += 1
                        self.on_activity(AgentActivity("guarded", shorten(txt.replace("JEV KORUMASI:", "Koruma:"),
                                                                         200)))
        elif t == "rate_limit_event":
            info = ev.get("rate_limit_info")
            if isinstance(info, dict):
                self.rate_limit = info
        elif t == "result":
            self.result = ev
            if ev.get("stop_reason") == "refusal":
                self.refused = True

    def usage(self) -> dict:
        u = empty_usage()
        r = self.result or {}
        ru = r.get("usage") or {}
        cache_read = int(ru.get("cache_read_input_tokens") or 0)
        u["input_tokens"] = int(ru.get("input_tokens") or 0) + int(ru.get("cache_creation_input_tokens") or 0) \
            + cache_read
        u["cached_input_tokens"] = cache_read
        u["output_tokens"] = int(ru.get("output_tokens") or 0)
        u["cost_usd"] = float(r.get("total_cost_usd") or 0.0)
        return u


class ClaudeAdapter(AgentAdapter):
    provider = "claude"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._exe = None

    def exe(self) -> Path | None:
        if self._exe is None:
            self._exe = find_cli("claude", self.cfg.get("cli", "claude", default="auto")).path
        return self._exe

    def settings_path(self, spec: CallSpec) -> Path | None:
        """Koruma kancasının ayar dosyası. Kanca proje klasörünü bilir; paralel görev kendi çalışma ağacında
        (projenin kardeş klasöründe) çalıştığı için o ağacın dosyası ayrıdır."""
        if not (spec.guard and spec.run_dir and spec.project):
            return None
        run_dir, project = Path(spec.run_dir), Path(spec.project)
        name = "jev-guard-settings.json"
        home = run_dir.parent.parent.parent if run_dir.parent.name == "runs" and run_dir.parent.parent.name == ".jev"             else None
        if home is not None and os.path.normcase(os.path.abspath(home)) != os.path.normcase(os.path.abspath(project)):
            key = hashlib.sha1(os.path.normcase(os.path.abspath(project)).encode("utf-8")).hexdigest()[:8]
            name = f"jev-guard-settings-{key}.json"
        p = run_dir / name
        if not p.exists():
            atomic_write_json(p, guard.hook_settings(run_dir, project))
        return p

    def argv(self, spec: CallSpec, exe: Path, settings: Path | None) -> list[str]:
        a = [str(exe), "-p", "--model", spec.model, "--effort", spec.effort, "--output-format", "stream-json",
             "--verbose", "--no-session-persistence"]
        if spec.schema is not None:
            a += ["--json-schema", schema_text(spec.schema)]
        if spec.mode == "readonly":
            tools = "Read,Grep,Glob" if spec.tools is None else spec.tools
            if spec.web and spec.tools is None:
                tools += ",WebSearch,WebFetch"
            a += ["--tools", tools, "--dangerously-skip-permissions"]
        elif spec.full_access:
            a += ["--dangerously-skip-permissions"]
        else:
            a += ["--permission-mode", "acceptEdits"]
            if spec.web:
                a += ["--allowedTools", "WebSearch,WebFetch"]
        if settings is not None:
            a += ["--settings", str(settings)]
        a += [str(x) for x in (self.cfg.get("cli", "claude_ek_argumanlar", default=[]) or [])]
        return a

    def env(self, spec: CallSpec) -> dict:
        strip = None
        if self.cfg.get("cli", "claude_ortam_temizle", default=True):
            strip = env_filter(inside_claude=bool(os.environ.get("CLAUDECODE")))
        return child_env({"JEV_AJAN": spec.agent, "JEV_GOREV": spec.task_id or ""}, strip)

    def run(self, spec: CallSpec, on_activity: ActivityFn) -> AgentResult:
        paths = base_paths(spec)
        write_prompt(spec)
        exe = self.exe()
        if exe is None:
            return AgentResult(False, error_kind="crash", paths=paths,
                               error_text="claude.exe bulunamadı. Claude masaüstü uygulaması kurulu mu?")
        parser = ClaudeParser(on_activity, spec.cwd)
        proc = run_process(self.argv(spec, exe, self.settings_path(spec)), cwd=spec.cwd, env=self.env(spec),
                           stdin_text=spec.prompt, stdout_path=spec.path("stdout.jsonl"),
                           stderr_path=spec.path("stderr.txt"), timeout_s=spec.timeout_s, on_line=parser.feed,
                           cancel=spec.cancel)
        r = parser.result or {}
        text = str(r.get("result") or parser.last_text or "")
        structured = None
        if spec.schema is not None:
            so = r.get("structured_output")
            structured = so if isinstance(so, (dict, list)) else extract_json(text)
            if structured is not None:
                spec.path("out.json").write_text(json.dumps(structured, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
        res = AgentResult(ok=False, structured=structured, text=text, exit_code=proc.exit_code,
                          duration_s=proc.duration_s, usage=parser.usage(), paths=paths)
        is_error = bool(r.get("is_error")) or (parser.result is not None and
                                                str(r.get("subtype", "success")) != "success")
        if proc.start_error:
            res.error_kind, res.error_text = "crash", proc.start_error
        elif proc.cancelled:
            res.error_kind, res.error_text = "cancelled", "Kullanıcı durdurdu."
        elif proc.timed_out:
            res.error_kind, res.error_text = "timeout", f"Zaman aşımı ({spec.timeout_s} sn)."
        elif proc.exit_code == 0 and parser.result is not None and not is_error:
            res.ok = True
        else:
            parts = list(parser.errors)
            if is_error or proc.exit_code not in (0, None) or parser.result is None:
                parts.append(text)
            parts.append(proc.stderr_tail.strip())
            res.error_text = "\n".join(p for p in parts if p).strip() or f"Claude {proc.exit_code} koduyla çıktı."
            res.error_kind, res.reset_at = self.classify(res.error_text)
            rl = parser.rate_limit or {}
            if rl.get("status") == "rejected":  # CLI'ın kendi kota bildirimi: hata metninden güvenilir
                res.error_kind = "quota"
                res.reset_at = epoch_reset(rl.get("resetsAt")) or res.reset_at
                res.quota_shared = str(rl.get("rateLimitType") or "") in _SHARED_WINDOWS
            res.quota_hit = res.error_kind == "quota"
        if not (proc.start_error or proc.cancelled or proc.timed_out) and res.error_kind != "quota" and (
                parser.refused or (structured is None and self.is_refusal(text))):
            # güvenlik reddi: CLI bazen başarı diye bitirir, metin yalnızca ret iletisidir
            res.ok, res.error_kind = False, "refusal"
            res.error_text = res.error_text or text or "Model isteği güvenlik gerekçesiyle yanıtlamadı."
        return res
