"""Ajan çıktısını ofis etkinliğine çevirme: Codex ve Claude ayrıştırıcıları, komut sınıflandırma,
ortam süzgeci ve komut satırı oluşturucular."""
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _ortak
from jev.adapters.base import (AgentActivity, CallSpec, child_env, command_activity, is_test_command,
                               unwrap_shell)
from jev.adapters.claude import ClaudeAdapter, ClaudeParser, env_filter, tool_activity
from jev.adapters.codex import CodexAdapter, CodexParser
from jev.sema import schema_text

CFG = _ortak.make_config()
PS = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '
CWD = Path("C:\\jevtest\\Proje")


def ev(**kw):
    return json.dumps(kw, ensure_ascii=False)


def item(event, **kw):
    return ev(type=event, item=kw)


class Collector:
    def __init__(self):
        self.acts = []
        self.items = []

    def __call__(self, act):
        self.acts.append(act)

    def pairs(self):
        return [(a.kind, a.text) for a in self.acts]


def spec(tmp, **kw):
    base = dict(agent="sonnet", model="claude-sonnet-5-5", provider="claude", prompt="Görev: ığüşöç", cwd=tmp,
                mode="full", effort="high", timeout_s=60, call_dir=tmp / "cagri", call_name="T01-1-sonnet",
                phase="worker")
    base.update(kw)
    return CallSpec(**base)


class CommandActivityTests(unittest.TestCase):
    def test_is_test_command(self):
        for cmd in ("pytest -q", "py -m unittest discover", "npm test", "yarn run test", "pnpm test",
                    "dotnet test", "./gradlew test", "cargo test --all", "go test ./...", "Invoke-Pester",
                    "npx vitest run"):
            self.assertTrue(is_test_command(cmd), cmd)
        for cmd in ("git status", "echo testing", "npm install", "latest", "contest", "", None):
            self.assertFalse(is_test_command(cmd), cmd)

    def test_unwrap_shell(self):
        self.assertEqual(unwrap_shell(PS + '"python -m unittest -v"'), "python -m unittest -v")
        self.assertEqual(unwrap_shell("bash -lc 'npm test'"), "npm test")
        self.assertEqual(unwrap_shell('cmd /c "type a.txt"'), "type a.txt")
        self.assertEqual(unwrap_shell("git status"), "git status")
        self.assertEqual(unwrap_shell(""), "")

    def test_kinds(self):
        cases = [
            (PS + '"python -m unittest -v"', "testing", "python -m unittest -v", None),
            ("Set-Content -Path src/app.py -Value 'x'", "editing", None, "src/app.py"),
            ("Out-File -FilePath 'notlar.md'", "editing", None, "notlar.md"),
            ("Add-Content günlük.txt 'satır'", "editing", None, "günlük.txt"),
            ("Get-Content README.md", "reading", None, "README.md"),
            ("cat src/main.py | head", "reading", None, "src/main.py"),
            ("type 'a b.txt'", "reading", None, "a b.txt"),
            ('Set-Content -Path "Yeni Klasör\\not.txt" -Value x', "editing", None, "Yeni Klasör\\not.txt"),
            ("Set-Content -Value 'x.y z' foo.txt", "editing", None, "foo.txt"),
            ("git status; Get-Content x.txt", "command", None, None),
            ("Set-Content x.txt 'a'; python -m pytest", "testing", None, None),
            ("", "command", "", None),
        ]
        for cmd, kind, text, path in cases:
            act = command_activity(cmd)
            self.assertEqual(act.kind, kind, cmd)
            if text is not None:
                self.assertEqual(act.text, text, cmd)
            self.assertEqual(act.path, path, cmd)


class CodexParserTests(unittest.TestCase):
    def setUp(self):
        self.col = Collector()
        self.p = CodexParser(self.col, self.col.items.append)

    def feed(self, *lines):
        for line in lines:
            self.p.feed(line)

    def test_normal_turn(self):
        cmd = PS + '"python -m unittest -v"'
        self.feed(
            "Reading prompt from stdin...", "", "{bozuk", "[1, 2]",
            ev(type="thread.started", thread_id="01a0dff5"),
            ev(type="turn.started"),
            item("item.started", id="item_1", type="command_execution", command=cmd, exit_code=None),
            item("item.completed", id="item_1", type="command_execution", command=cmd, exit_code=0,
                 aggregated_output="Merhaba, dünya! ığşçöü\r\n"),
            item("item.completed", id="item_2", type="agent_message", text="Tamam, bitti."),
            ev(type="turn.completed", usage={"input_tokens": 15361, "cached_input_tokens": 12032,
                                             "cache_write_input_tokens": 0, "output_tokens": 5,
                                             "reasoning_output_tokens": 0}),
            ev(type="turn.completed", usage={"input_tokens": 100, "cached_input_tokens": None, "output_tokens": 7,
                                             "reasoning_output_tokens": 3}),
        )
        self.assertEqual(self.p.thread_id, "01a0dff5")
        self.assertEqual(self.p.last_message, "Tamam, bitti.")
        self.assertEqual(self.p.commands, [{"command": cmd, "exit_code": 0}])
        self.assertEqual(self.p.usage, {"input_tokens": 15461, "cached_input_tokens": 12032, "output_tokens": 12,
                                        "reasoning_output_tokens": 3, "cost_usd": 0.0})
        self.assertEqual(self.col.pairs(), [("thinking", "Düşünüyor…"), ("testing", "python -m unittest -v"),
                                            ("message", "Tamam, bitti.")])
        self.assertEqual([i["id"] for i in self.col.items], ["item_1"])  # koruma yalnızca başlangıçta bakar
        self.assertFalse(self.p.failed)
        self.assertEqual(self.p.errors, [])

    def test_failures_and_errors(self):
        self.feed(
            ev(type="error", message="Reconnecting... 1/5"),
            item("item.completed", id="item_0", type="error", message="`--dangerously-bypass-hook-trust` açık"),
            ev(type="turn.failed", error={"message": "You've hit your usage limit."}),
            ev(type="turn.failed", error="düz metin hata"),
            ev(type="turn.failed"),
        )
        self.assertTrue(self.p.failed)
        self.assertEqual(self.p.errors, ["Reconnecting... 1/5", "`--dangerously-bypass-hook-trust` açık",
                                         "You've hit your usage limit.", "düz metin hata", "turn.failed"])

    def test_top_level_error_alone_is_not_failure(self):
        self.feed(ev(type="error", error="stream disconnected"))
        self.assertFalse(self.p.failed)
        self.assertEqual(self.p.errors, ["stream disconnected"])

    def test_file_change(self):
        changes = [{"path": "src/a.py", "kind": "add"}, {"path": "b.md"}, {"kind": "delete"}, "bozuk"]
        self.feed(item("item.started", id="f1", type="file_change", changes=changes),
                  item("item.updated", id="f1", type="file_change", changes=changes),
                  item("item.completed", id="f1", type="file_change", changes=changes[:1]))
        self.assertEqual(self.col.pairs(), [("editing", "add: src/a.py"), ("editing", "değişiklik: b.md"),
                                            ("editing", "add: src/a.py")])
        self.assertEqual(self.col.acts[0].path, "src/a.py")
        self.assertEqual(len(self.col.items), 2)

    def test_other_items(self):
        self.feed(
            item("item.completed", type="reasoning", text="Önce testleri   okuyacağım."),
            item("item.completed", type="reasoning", summary="Özet düşünce"),
            item("item.completed", type="reasoning"),
            item("item.started", type="reasoning", text="görünmez"),
            item("item.started", type="mcp_tool_call", server="fs", tool="read"),
            item("item.started", type="web_search", query="python argparse"),
            item("item.started", type="todo_list", items=[{"text": "a", "completed": True},
                                                        {"text": "b", "completed": False}, "x"]),
            item("item.updated", type="todo_list", items=[]),
            item("item.completed", type="todo_list", items=[]),
            item("item.completed", type="bilinmeyen_tur"),
            ev(type="item.started", item="bozuk"),
        )
        self.assertEqual(self.col.pairs(), [
            ("thinking", "Önce testleri okuyacağım."), ("thinking", "Özet düşünce"), ("thinking", "Düşünüyor…"),
            ("command", "fs.read"), ("reading", "web araması: python argparse"),
            ("thinking", "Yapılacaklar: 1/3"), ("thinking", "Yapılacaklar: 0/0")])

    def test_long_message_is_shortened_for_office_only(self):
        text = "uzun " * 200
        self.feed(item("item.completed", type="agent_message", text=text))
        self.assertEqual(self.p.last_message, text)
        self.assertEqual(len(self.col.acts[0].text), 300)
        self.assertTrue(self.col.acts[0].text.endswith("…"))

    def test_without_item_hook(self):
        p = CodexParser(self.col)
        p.feed(item("item.started", type="command_execution", command="git status"))
        p.feed(item("item.started", type="file_change", changes=[{"path": "a.py", "kind": "update"}]))
        self.assertEqual(self.col.pairs(), [("command", "git status"), ("editing", "update: a.py")])


class ClaudeParserTests(unittest.TestCase):
    def setUp(self):
        self.col = Collector()
        self.p = ClaudeParser(self.col, CWD)

    def test_stream(self):
        lines = [
            "hata değil düz metin", "{bozuk",
            ev(type="system", subtype="init", session_id="s1", model="claude-sonnet-5-5"),
            ev(type="system", subtype="hook_response"),
            ev(type="assistant", message={"content": [
                {"type": "thinking", "thinking": "Planı düşünüyorum"},
                {"type": "text", "text": "Merhaba"},
                {"type": "text", "text": ""},
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(CWD / "src" / "a.py")}},
                "bozuk"]}),
            ev(type="assistant", message={"content": [{"type": "thinking", "thinking": ""}]}),
            ev(type="user", message={"content": [
                {"type": "tool_result", "content": "JEV KORUMASI: proje dışına yazma engellendi"}]}),
            ev(type="user", message={"content": [
                {"type": "tool_result", "content": [{"type": "text", "text": "JEV KORUMASI: git push"}]}]}),
            ev(type="user", message={"content": [{"type": "tool_result", "content": "normal çıktı"}]}),
            ev(type="user", message={"content": "düz metin"}),
            ev(type="assistant", error="rate_limit", message={"content": []}),
            ev(type="result", subtype="success", is_error=False, result="bitti",
               usage={"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100,
                      "output_tokens": 7}, total_cost_usd=0.25),
        ]
        for line in lines:
            self.p.feed(line)
        self.assertEqual(self.p.init["session_id"], "s1")
        self.assertEqual(self.p.last_text, "Merhaba")
        self.assertEqual(self.p.guard_blocks, 2)
        self.assertEqual(self.p.errors, ["rate_limit"])
        self.assertEqual(self.p.result["result"], "bitti")
        self.assertEqual(self.col.pairs(), [
            ("thinking", "Düşünüyor…"), ("thinking", "Planı düşünüyorum"), ("message", "Merhaba"),
            ("reading", f"{os.path.join('src', 'a.py')} okuyor"), ("thinking", "Düşünüyor…"),
            ("guarded", "Koruma: proje dışına yazma engellendi"), ("guarded", "Koruma: git push")])
        self.assertEqual(self.p.usage(), {"input_tokens": 115, "cached_input_tokens": 100, "output_tokens": 7,
                                          "reasoning_output_tokens": 0, "cost_usd": 0.25})

    def test_usage_without_result(self):
        self.assertEqual(self.p.usage(), {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                          "reasoning_output_tokens": 0, "cost_usd": 0.0})


class ToolActivityTests(unittest.TestCase):
    def test_tools(self):
        inside = str(CWD / "src" / "a.py")
        rel = os.path.join("src", "a.py")
        cases = [
            ("Bash", {"command": "pytest -q"}, "testing", "pytest -q", None),
            ("PowerShell", {"command": "Get-Content a.txt"}, "reading", "Get-Content a.txt", "a.txt"),
            ("Read", {"file_path": inside}, "reading", f"{rel} okuyor", inside),
            ("Read", {"file_path": "c:\\JEVTEST\\proje\\src\\a.py"}, "reading", f"{rel} okuyor",
             "c:\\JEVTEST\\proje\\src\\a.py"),
            ("Read", {"file_path": "D:\\başka\\b.py"}, "reading", "D:\\başka\\b.py okuyor", "D:\\başka\\b.py"),
            ("Read", {"file_path": "src/a.py"}, "reading", "src/a.py okuyor", "src/a.py"),
            ("NotebookRead", {"notebook_path": "n.ipynb"}, "reading", "n.ipynb okuyor", "n.ipynb"),
            ("LS", {"path": "src"}, "reading", "src okuyor", "src"),
            ("Grep", {"pattern": "TODO", "path": "src"}, "reading", "arıyor: TODO", "src"),
            ("Glob", {"pattern": "**/*.py"}, "reading", "dosya arıyor: **/*.py", None),
            ("Write", {"file_path": inside}, "editing", f"{rel} düzenliyor", inside),
            ("Edit", {"file_path": "b.py"}, "editing", "b.py düzenliyor", "b.py"),
            ("MultiEdit", {"file_path": "c.py"}, "editing", "c.py düzenliyor", "c.py"),
            ("NotebookEdit", {"notebook_path": "n.ipynb"}, "editing", "n.ipynb düzenliyor", "n.ipynb"),
            ("TodoWrite", {"todos": []}, "thinking", "Yapılacaklar listesini güncelliyor", None),
            ("WebFetch", {"url": "https://example.com"}, "reading", "web: https://example.com", None),
            ("WebSearch", {"query": "argparse"}, "reading", "web: argparse", None),
            ("Task", {"description": "testleri yaz"}, "thinking", "Alt görev: testleri yaz", None),
            ("Agent", {"description": "incele"}, "thinking", "Alt görev: incele", None),
            ("mcp__fs__read", {}, "command", "mcp__fs__read", None),
            ("Read", None, "reading", " okuyor", None),
        ]
        for name, inp, kind, text, path in cases:
            self.assertEqual(tool_activity(name, inp, CWD), AgentActivity(kind, text, path), (name, inp))


class EnvTests(unittest.TestCase):
    def test_env_filter(self):
        outside, inside = env_filter(False), env_filter(True)
        for k in ("CLAUDECODE", "claudecode", "CLAUDE_PID", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                  "ANTHROPIC_BASE_URL", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_PREVIEW_URL"):
            self.assertTrue(outside(k) and inside(k), k)
        for k in ("CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CONFIG_DIR", "PATH", "ANTHROPIC_MODEL", "USERPROFILE"):
            self.assertFalse(outside(k) or inside(k), k)
        self.assertFalse(outside("CLAUDE_CODE_ENTRYPOINT"))
        self.assertTrue(inside("CLAUDE_CODE_ENTRYPOINT"))

    def test_child_env(self):
        with mock.patch.dict(os.environ, {"JEV_TEST_SIL": "1", "JEV_TEST_KAL": "1"}):
            env = child_env({"JEV_AJAN": "sol", "JEV_YOK": None, "JEV_SAYI": 3}, lambda k: k == "JEV_TEST_SIL")
        self.assertNotIn("JEV_TEST_SIL", env)
        self.assertEqual(env["JEV_TEST_KAL"], "1")
        self.assertEqual((env["PYTHONUTF8"], env["PYTHONIOENCODING"]), ("1", "utf-8"))
        self.assertEqual((env["JEV_AJAN"], env["JEV_SAYI"]), ("sol", "3"))
        self.assertNotIn("JEV_YOK", env)

    def test_claude_env_strips_session_variables(self):
        fake = {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "ANTHROPIC_API_KEY": "x",
                "CLAUDE_CONFIG_DIR": "c"}
        s = spec(_ortak.temp_dir("ortam"), task_id="T03")
        with mock.patch.dict(os.environ, fake):
            env = ClaudeAdapter(CFG).env(s)
            kept = ClaudeAdapter(_ortak.make_config(cli={"claude_ortam_temizle": False})).env(s)
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY"):
            self.assertNotIn(k, env)
            self.assertIn(k, kept)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "c")
        self.assertEqual((env["JEV_AJAN"], env["JEV_GOREV"]), ("sonnet", "T03"))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}):
            os.environ.pop("CLAUDECODE", None)
            self.assertIn("CLAUDE_CODE_ENTRYPOINT", ClaudeAdapter(CFG).env(spec(_ortak.temp_dir("ortam"))))


class ArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = _ortak.temp_dir("argv")
        self.exe = self.tmp / "claude.exe"
        self.schema = {"type": "object", "properties": {"ad": {"type": "string"}}}

    def test_claude_full_access_with_schema_and_settings(self):
        s = spec(self.tmp, schema=self.schema)
        settings = self.tmp / "jev-guard-settings.json"
        self.assertEqual(ClaudeAdapter(CFG).argv(s, self.exe, settings), [
            str(self.exe), "-p", "--model", "claude-sonnet-5-5", "--effort", "high", "--output-format", "stream-json",
            "--verbose", "--no-session-persistence", "--json-schema", schema_text(self.schema),
            "--dangerously-skip-permissions", "--settings", str(settings), "--strict-mcp-config"])

    def test_claude_modes(self):
        a = ClaudeAdapter(CFG)
        ro = a.argv(spec(self.tmp, mode="readonly", full_access=False), self.exe, None)
        self.assertEqual(ro[10:], ["--tools", "Read,Grep,Glob", "--dangerously-skip-permissions",
                                   "--strict-mcp-config"])
        limited = a.argv(spec(self.tmp, full_access=False), self.exe, None)
        self.assertEqual(limited[10:], ["--permission-mode", "acceptEdits", "--strict-mcp-config"])
        extra = ClaudeAdapter(_ortak.make_config(cli={"claude_ek_argumanlar": ["--debug", 5]}))
        self.assertEqual(extra.argv(spec(self.tmp), self.exe, None)[-2:], ["--debug", "5"])
        for argv in (ro, limited):
            self.assertNotIn("--bare", argv)

    def test_claude_settings_path(self):
        run_dir = self.tmp / "kosu"
        run_dir.mkdir()
        a = ClaudeAdapter(CFG)
        self.assertIsNone(a.settings_path(spec(self.tmp)))
        self.assertIsNone(a.settings_path(spec(self.tmp, run_dir=run_dir, project=self.tmp, guard=False)))
        self.assertIsNone(a.settings_path(spec(self.tmp, run_dir=run_dir, project=self.tmp)))
        self.assertFalse((run_dir / "jev-guard-settings.json").exists())
        p = a.settings_path(spec(self.tmp, run_dir=run_dir, project=self.tmp, guard=True))
        self.assertEqual(p, run_dir / "jev-guard-settings.json")
        hooks = json.loads(p.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        self.assertIn("kanca.py", hooks[0]["hooks"][0]["command"])

    def test_codex(self):
        s = spec(self.tmp, agent="sol", model="gpt-6-sol", provider="codex")
        exe = self.tmp / "codex.exe"
        schema_path, out = self.tmp / "s.json", self.tmp / "o.json"
        self.assertEqual(CodexAdapter(CFG).argv(s, exe, schema_path, out), [
            str(exe), "exec", "-m", "gpt-6-sol", "-c", 'model_reasoning_effort="high"', "--skip-git-repo-check",
            "--ephemeral", "--json", "-C", str(self.tmp), "--dangerously-bypass-approvals-and-sandbox",
            "--ignore-user-config", "-c", 'windows.sandbox="elevated"', "--output-schema", str(schema_path),
            "-o", str(out), "-"])

    def test_codex_modes_and_options(self):
        exe = self.tmp / "codex.exe"
        a = CodexAdapter(_ortak.make_config(cli={"codex_kullanici_ayarini_yoksay": False, "codex_windows_sandbox": "",
                                                 "codex_service_tier": "flex", "codex_ek_argumanlar": ["--oss"]}))
        ro = a.argv(spec(self.tmp, mode="readonly", effort="xhigh"), exe, None, self.tmp / "o.json")
        self.assertEqual(ro[5], 'model_reasoning_effort="xhigh"')
        self.assertEqual(ro[11:], ["-s", "read-only", "-c", 'service_tier="flex"', "--oss", "-"])
        ws = a.argv(spec(self.tmp, full_access=False), exe, None, self.tmp / "o.json")
        self.assertEqual(ws[11:13], ["-s", "workspace-write"])
        self.assertNotIn("--ignore-user-config", ws)
        self.assertNotIn("--output-schema", ws)


class RunTests(unittest.TestCase):
    """Sahte ajan süreciyle uçtan uca adaptör çalıştırma: prompt stdin'den gider, çıktı ayrıştırılır."""

    def setUp(self):
        self.tmp = _ortak.temp_dir("kosum")

    def fake_exe(self, lines, code=0, stderr=""):
        script = self.tmp / "sahte_ajan.py"
        script.write_text(
            "import sys\n"
            "sys.stdin.read()\n"
            f"for line in {lines!r}:\n"
            "    print(line, flush=True)\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({code})\n", encoding="utf-8")
        return script

    def run_claude(self, lines, code=0, stderr="", **kw):
        import sys
        a = ClaudeAdapter(CFG)
        script = self.fake_exe(lines, code, stderr)
        a._exe = Path(sys.executable)
        with mock.patch.object(ClaudeAdapter, "argv", lambda self_, s, e, st: [str(e), str(script)]):
            s = spec(self.tmp, **kw)
            return a.run(s, Collector()), s

    def test_claude_success_with_structured_output(self):
        res, s = self.run_claude([
            ev(type="system", subtype="init"),
            ev(type="result", subtype="success", is_error=False, result='{"ad": "Işık"}',
               structured_output={"ad": "Işık"}, usage={"input_tokens": 3, "output_tokens": 2},
               total_cost_usd=0.01)], schema={"type": "object"})
        self.assertTrue(res.ok, res.error_text)
        self.assertEqual(res.structured, {"ad": "Işık"})
        self.assertEqual(json.loads(s.path("out.json").read_text(encoding="utf-8")), {"ad": "Işık"})
        self.assertEqual(s.path("prompt.md").read_text(encoding="utf-8"), "Görev: ığüşöç")
        self.assertEqual(res.usage["output_tokens"], 2)

    def test_claude_quota_error(self):
        res, _ = self.run_claude([ev(type="result", subtype="success", is_error=True,
                                     result="Claude AI usage limit reached")], code=1)
        self.assertFalse(res.ok)
        self.assertEqual(res.error_kind, "quota")
        self.assertTrue(res.quota_hit)

    def test_claude_rate_limit_event(self):
        """Gerçek CLI çıktısının biçimi (1 Ekim 2026): kota bildirimi metinden önce rate_limit_event'te gelir."""
        reset = int(time.time()) + 3600
        msg = "You've hit your session limit · resets 1:40pm (Europe/Istanbul)"
        for window, shared in (("five_hour", True), ("seven_day", True), ("seven_day_opus", False)):
            with self.subTest(window=window):
                info = {"status": "rejected", "resetsAt": reset, "rateLimitType": window, "isUsingOverage": False,
                        "unifiedWindows": {"five_hour": {"utilization": 1, "resetsAt": reset}}}
                res, s = self.run_claude([
                    ev(type="system", subtype="init"), ev(type="rate_limit_event", rate_limit_info=info),
                    ev(type="assistant", error="rate_limit", message={"content": [{"type": "text", "text": msg}]}),
                    ev(type="result", subtype="success", is_error=True, result=msg, api_error_status=429)], code=1)
                self.assertEqual((res.error_kind, res.quota_hit, res.quota_shared), ("quota", True, shared))
                self.assertEqual(int(res.reset_at.timestamp()), reset)  # metindeki saatten değil, olaydan
                self.assertEqual(res.summary()["quota_shared"], shared)

    def test_claude_rate_limit_warning_is_not_an_error(self):
        info = {"status": "allowed_warning", "resetsAt": int(time.time()) + 600, "rateLimitType": "five_hour",
                "utilization": 0.98}
        res, _ = self.run_claude([ev(type="system", subtype="init"), ev(type="rate_limit_event", rate_limit_info=info),
                                  ev(type="result", subtype="success", is_error=False, result="tamam")])
        self.assertTrue(res.ok, res.error_text)
        self.assertEqual((res.error_kind, res.quota_hit, res.quota_shared), ("none", False, False))

    def test_claude_no_result_is_crash(self):
        res, _ = self.run_claude(["düz çıktı"], code=0, stderr="beklenmedik bir şey oldu")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_kind, "crash")
        self.assertIn("beklenmedik", res.error_text)

    def test_claude_cancelled(self):
        cancel = threading.Event()
        cancel.set()
        res, _ = self.run_claude([ev(type="result", subtype="success", result="x")], cancel=cancel)
        self.assertIn(res.error_kind, ("cancelled", "none"))  # süreç iptalden önce bitebilir

    def test_codex_turn_failed(self):
        import sys
        a = CodexAdapter(CFG)
        script = self.fake_exe([ev(type="turn.failed", error={"message": "stream error: 503 Service Unavailable"})],
                               code=1)
        a._exe = Path(sys.executable)
        with mock.patch.object(CodexAdapter, "argv", lambda self_, s, e, sp, o: [str(e), str(script)]):
            res = a.run(spec(self.tmp, agent="sol", model="gpt-6-sol", provider="codex"), Collector())
        self.assertFalse(res.ok)
        self.assertEqual(res.error_kind, "transient")
        self.assertIn("503", res.error_text)

    def test_missing_exe(self):
        a = CodexAdapter(CFG)
        with mock.patch.object(CodexAdapter, "exe", lambda self_: None):
            res = a.run(spec(self.tmp, agent="sol", model="gpt-6-sol", provider="codex"), Collector())
        self.assertEqual((res.ok, res.error_kind), (False, "crash"))
        self.assertIn("codex.exe bulunamadı", res.error_text)


if __name__ == "__main__":
    unittest.main()
