"""Codex CLI adaptörü (`codex exec --json`). Olay biçimi M0'da doğrulandı (docs/cli-notlari.md)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .. import guard
from ..discovery import find_cli
from ..sema import extract_json
from ..util import shorten
from .base import (AgentActivity, AgentAdapter, AgentResult, CallSpec, ActivityFn, base_paths, child_env,
                   command_activity, empty_usage, run_process, write_prompt)


class CodexParser:
    """`--json` satırlarını etkinliğe, kullanıma ve hata metnine çevirir. Koruma denetimi de burada."""

    def __init__(self, on_activity: ActivityFn, on_item=None):
        self.on_activity = on_activity
        self.on_item = on_item  # item.started / file_change öğeleri için (koruma)
        self.usage = empty_usage()
        self.last_message = ""
        self.errors: list[str] = []  # hata kanalı: üst düzey error, turn.failed, error öğeleri
        self.failed = False
        self.thread_id = None
        self.commands: list[dict] = []

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line or not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "thread.started":
            self.thread_id = ev.get("thread_id")
        elif t == "turn.started":
            self.on_activity(AgentActivity("thinking", "Düşünüyor…"))
        elif t == "turn.completed":
            u = ev.get("usage") or {}
            for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
                self.usage[k] += int(u.get(k) or 0)
        elif t == "turn.failed":
            self.failed = True
            err = ev.get("error")
            self.errors.append(err.get("message", "") if isinstance(err, dict) else str(err or "turn.failed"))
        elif t == "error":  # yeniden bağlanma uyarıları da bu türde gelebilir; tek başına başarısızlık değil
            self.errors.append(str(ev.get("message") or ev.get("error") or line))
        elif t in ("item.started", "item.updated", "item.completed"):
            item = ev.get("item") or {}
            if isinstance(item, dict):
                self._item(t, item)

    def _item(self, t: str, item: dict) -> None:
        it = item.get("type")
        if it == "command_execution":
            cmd = str(item.get("command") or "")
            if t == "item.started":
                if self.on_item:
                    self.on_item(item)
                self.on_activity(command_activity(cmd))
            elif t == "item.completed":
                self.commands.append({"command": cmd, "exit_code": item.get("exit_code")})
        elif it == "file_change":
            if self.on_item and t in ("item.started", "item.completed"):
                self.on_item(item)
            if t != "item.updated":
                for ch in item.get("changes") or []:
                    if isinstance(ch, dict) and ch.get("path"):
                        self.on_activity(AgentActivity("editing", f"{ch.get('kind', 'değişiklik')}: {ch['path']}",
                                                       str(ch["path"])))
        elif it == "agent_message" and t == "item.completed":
            self.last_message = str(item.get("text") or "")
            self.on_activity(AgentActivity("message", shorten(self.last_message, 300)))
        elif it == "reasoning" and t == "item.completed":
            txt = str(item.get("text") or item.get("summary") or "")
            self.on_activity(AgentActivity("thinking", shorten(txt, 200) or "Düşünüyor…"))
        elif it == "error":
            self.errors.append(str(item.get("message") or ""))
        elif it == "mcp_tool_call" and t == "item.started":
            self.on_activity(AgentActivity("command", f"{item.get('server', '')}.{item.get('tool', '')}"))
        elif it == "web_search" and t == "item.started":
            self.on_activity(AgentActivity("reading", f"web araması: {item.get('query', '')}"))
        elif it == "todo_list" and t != "item.updated":
            items = item.get("items") or []
            done = sum(1 for x in items if isinstance(x, dict) and x.get("completed"))
            self.on_activity(AgentActivity("thinking", f"Yapılacaklar: {done}/{len(items)}"))


class CodexAdapter(AgentAdapter):
    provider = "codex"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._exe = None

    def exe(self) -> Path | None:
        if self._exe is None:
            self._exe = find_cli("codex", self.cfg.get("cli", "codex", default="auto")).path
        return self._exe

    def argv(self, spec: CallSpec, exe: Path, schema_path: Path | None, out_path: Path) -> list[str]:
        g = lambda k, d=None: self.cfg.get("cli", k, default=d)  # noqa: E731
        a = [str(exe), "exec"]
        for img in spec.images or []:  # resimler doğrudan modele eklenir
            a += ["-i", str(img)]
        a += ["-m", spec.model, "-c", f'model_reasoning_effort="{spec.effort}"',
              "--skip-git-repo-check", "--ephemeral", "--json", "-C", str(spec.cwd)]
        if spec.web:
            a += ["-c", 'web_search="live"']
        if spec.mode == "readonly":
            a += ["-s", "read-only"]
        elif spec.full_access:
            a += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            a += ["-s", "workspace-write"]
        if g("codex_kullanici_ayarini_yoksay", True):
            a.append("--ignore-user-config")
        if g("codex_windows_sandbox"):
            a += ["-c", f'windows.sandbox="{g("codex_windows_sandbox")}"']
        if g("codex_service_tier"):
            a += ["-c", f'service_tier="{g("codex_service_tier")}"']
        if schema_path is not None:
            a += ["--output-schema", str(schema_path), "-o", str(out_path)]
        a += [str(x) for x in (g("codex_ek_argumanlar", []) or [])]
        a.append("-")
        return a

    def run(self, spec: CallSpec, on_activity: ActivityFn) -> AgentResult:
        paths = base_paths(spec)
        write_prompt(spec)
        exe = self.exe()
        if exe is None:
            return AgentResult(False, error_kind="crash", paths=paths,
                               error_text="codex.exe bulunamadı. Codex masaüstü uygulaması kurulu mu?")
        out_path = spec.path("out.json")
        schema_path = None
        if spec.schema is not None:
            schema_path = spec.path("schema.json")
            schema_path.write_text(json.dumps(spec.schema, ensure_ascii=False), encoding="utf-8")
        if out_path.exists():
            out_path.unlink()

        kill = threading.Event()
        guard_hits: list[dict] = []

        def on_item(item: dict) -> None:
            if not (spec.guard and spec.project):
                return
            v = guard.check_codex_item(item, spec.project)
            if v is None:
                return
            detail = item.get("command") or json.dumps(item.get("changes"), ensure_ascii=False)
            rec = guard.record(spec.run_dir or spec.call_dir.parent, v, provider="codex",
                               tool=str(item.get("type")), detail=str(detail), agent=spec.agent,
                               task=spec.task_id, action="durduruldu" if v.kill else "kaydedildi")
            guard_hits.append(rec)
            on_activity(AgentActivity("guarded", f"Koruma: {v.reason}"))
            if v.kill:
                kill.set()

        parser = CodexParser(on_activity, on_item)
        env = child_env({"JEV_AJAN": spec.agent, "JEV_GOREV": spec.task_id or ""})
        proc = run_process(self.argv(spec, exe, schema_path, out_path), cwd=spec.cwd, env=env,
                           stdin_text=spec.prompt, stdout_path=spec.path("stdout.jsonl"),
                           stderr_path=spec.path("stderr.txt"), timeout_s=spec.timeout_s, on_line=parser.feed,
                           cancel=spec.cancel, kill_request=kill)

        text = parser.last_message
        structured = None
        if spec.schema is not None:
            raw = out_path.read_text(encoding="utf-8-sig", errors="replace") if out_path.exists() else ""
            structured = extract_json(raw) if raw.strip() else None
            if structured is None:
                structured = extract_json(text)
        res = AgentResult(ok=False, structured=structured, text=text, exit_code=proc.exit_code,
                          duration_s=proc.duration_s, usage=parser.usage, paths=paths, guard=guard_hits)
        if proc.start_error:
            res.error_kind, res.error_text = "crash", proc.start_error
        elif proc.cancelled:
            res.error_kind, res.error_text = "cancelled", "Kullanıcı durdurdu."
        elif proc.killed:
            res.error_kind = "guard"
            res.error_text = "Koruma, tehlikeli bir komut yüzünden ajanı durdurdu: " + \
                             "; ".join(h.get("reason", "") for h in guard_hits if h.get("kill"))
        elif proc.timed_out:
            res.error_kind, res.error_text = "timeout", f"Zaman aşımı ({spec.timeout_s} sn)."
        elif proc.exit_code == 0 and not parser.failed:
            res.ok = True
        else:
            err = "\n".join([e for e in parser.errors if e] + [proc.stderr_tail.strip()]).strip()
            res.error_text = err or f"Codex {proc.exit_code} koduyla çıktı."
            res.error_kind, res.reset_at = self.classify(res.error_text)
            res.quota_hit = res.error_kind == "quota"
        if res.ok and structured is None and self.is_refusal(text):  # güvenlik reddi başarı diye bitmiş olabilir
            res.ok, res.error_kind, res.error_text = False, "refusal", text
        return res
