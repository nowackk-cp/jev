# CLI notları (M0 bulguları)

Tarih: 2026-09-27. Makine: Windows 11, PowerShell 5.1, Python 3.13.15, Git 2.55.

## Yollar ve sürümler

| CLI | Yol | Sürüm |
|---|---|---|
| Codex | `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe` (PATH'te değil) | `codex-cli 0.158.0-alpha.2.1` |
| Claude Code | `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\<sürüm>\claude.exe` (PATH'te değil) | `2.1.281` |

- Claude masaüstü uygulaması paketli (MSIX) kurulu. Uygulamanın içinden başlayan işlemler (bu oturumun kabuğu gibi) aynı dosyayı `%APPDATA%\Claude\claude-code\<sürüm>\claude.exe` yolunda görür. Kullanıcının kendi terminali, uygulamanın Terminal paneli dahil, o yolu **göremez**: `%APPDATA%` yoluyla verilen giriş komutu kullanıcıda "is not recognized" hatası verdi (2026-09-28). `discovery.py` iki yere de bakar, aynı sürümde paketteki gerçek yolu seçer.
- Codex'in `<hash>` klasörü güncellemeyle değişiyor: prompt yazılırken `13995fba801849b0` (0.155), M0'da `faa963e871dd422c` (0.158). `discovery.py` en son değiştirilen `codex.exe`'yi seçer. Codex gerçek `%LOCALAPPDATA%` altında; paketli değil.
- `~/.local/bin/claude.exe` yok.
- `--help` çıktıları `docs/cli-yardim/` altında.

## Oturum durumu

- `codex login status` → `Logged in using ChatGPT`.
- `claude auth status` (temiz ortam) → `"loggedIn": false`. Masaüstü uygulamasının içindeki Claude Code oturumu kendi girişini ayrı bir yoldan sağlıyor; bağımsız `claude.exe` için kullanıcının bir kez şunu çalıştırması gerekiyor:
  ```powershell
  & "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.281\claude.exe" auth login
  ```
  Varsayılan (`--claudeai`) abonelik girişidir. `--console` API faturalandırması kullanır; **kullanılmamalı**.
- Claude Code içinden çalışırken ortamda `CLAUDECODE`, `CLAUDE_CODE_*`, `CLAUDE_PID`, `ANTHROPIC_BASE_URL` gibi değişkenler bulunuyor. Jev, Claude alt süreçlerini başlatırken bunları temizler (`cli.claude_ortam_temizle`). Böylece `jev` nereden çalıştırılırsa çalıştırılsın alt süreç kullanıcının kendi terminalindeki gibi davranır. `ANTHROPIC_API_KEY` de temizlenir; yoksa Claude Code abonelik yerine API anahtarını kullanabilir.

## Codex `exec`

Doğrulanan komut (prompt stdin'den):

```
codex.exe exec -m gpt-6-luna -c model_reasoning_effort="low" --skip-git-repo-check --ephemeral --json
    -C <dizin> -s read-only --ignore-user-config -c windows.sandbox="elevated" -
```

- Açılış süresi: kullanıcı ayarıyla 3,7 sn; `--ignore-user-config` + `-c windows.sandbox="elevated"` ile 3,2 sn. İkincisi eklenti yönergelerini yüklemediği için çağrı başına ~1.500 giriş token'ı daha az harcıyor. **Varsayılan: `codex_kullanici_ayarini_yoksay = true`.**
- `--ignore-user-config` ile kullanıcının `service_tier="priority"` ayarı da uygulanmaz; `cli.codex_service_tier` ile istenirse verilir.
- `-s read-only` Windows'ta `windows.sandbox="elevated"` ile çalışıyor.
- Tam yetki: `--dangerously-bypass-approvals-and-sandbox` çalışıyor; ajan PowerShell üzerinden dosya yazdı ve çalıştırdı.
- Yapısal çıktı: `--output-schema <dosya> -o <out.json>` çalışıyor. Aynı JSON son `agent_message` öğesinin `text` alanında da geliyor. Katı şema (`additionalProperties:false`, tüm alanlar `required`, `["integer","null"]` birleşimi) kabul edildi.
- Türkçe karakterler hem stdout JSONL'da hem de dosyalarda bozulmadı. Not: PowerShell 5.1'in `Set-Content -Encoding utf8` komutu dosyaya BOM ekliyor; Python bunu sorunsuz okur.

### Olay biçimi (`--json`)

```json
{"type":"thread.started","thread_id":"01a0dff5-4632-7f32-81ef-5488042b9043"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"\"C:\\\\WINDOWS\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe\" -Command \"...; python selam.py\"","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"...","aggregated_output":"Merhaba, dünya! ığşçöü\r\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"`--dangerously-bypass-hook-trust` is enabled. ..."}}
{"type":"turn.completed","usage":{"input_tokens":15361,"cached_input_tokens":12032,"cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}
```

- Öğe türleri: `agent_message`, `command_execution`, `error` (uyarı olarak da kullanılıyor; tek başına başarısızlık değildir). Kaynak koddaki diğer türler (`reasoning`, `file_change`, `mcp_tool_call`, `web_search`, `todo_list`) ayrıştırıcıda savunmacı biçimde desteklenir.
- Başarısızlık: `turn.failed` ya da `error` olayı (öğe değil, üst düzey) ve sıfır dışı çıkış kodu.

### Codex kancaları

- `codex features list` → `hooks stable true`. İkili dosyada `PreToolUse`, `hookSpecificOutput.permissionDecision`, çıkış kodu 2 ile engelleme, `--dangerously-bypass-hook-trust` ve proje `.codex/config.toml`/`hooks.json` izleri var.
- Denenenler (hepsi `gpt-6-luna`, `low`, `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`):
  1. `-c hooks.PreToolUse=[{matcher="*", hooks=[{type="command", command=...}]}]` → kanca çağrılmadı.
  2. Aynısı `matcher=".*"` ile → çağrılmadı.
  3. Proje klasöründe `.codex/hooks.json` (Claude biçimi) → çağrılmadı.
  4. 3 + `-c projects.'<yol>'.trust_level="trusted"` → çağrılmadı.
- Sonuç: Bu sürümde kullanıcının `~/.codex` klasörüne dokunmadan Codex'e ön-çalıştırma kancası verilemiyor. **Yedek yol (uygulandı):**
  - Jev, Codex'in `--json` akışındaki her `command_execution` öğesini `item.started` anında koruma kurallarından geçirir. Felaket sınıfında bir komut görürse ajanın süreç ağacını `taskkill /T /F` ile hemen durdurur ve `guard.jsonl`'a yazar.
  - Görev bitince ayrıca denetim yapılır (silinen izlenen dosyalar, HEAD/dal değişimi, `.jev/` ve `.git/` iç dosyaları).
  - Bu yol **önleyici değil, durdurucu**dur: komut başlamış olabilir. README'de açıkça yazılıdır.

## Claude Code `-p`

Doğrulanan bayraklar (`--help`): `-p`, `--model`, `--effort low|medium|high|xhigh|max`, `--output-format text|json|stream-json`, `--json-schema`, `--dangerously-skip-permissions`, `--permission-mode`, `--tools`, `--settings <dosya-veya-json>`, `--setting-sources`, `--strict-mcp-config`, `--no-session-persistence`, `--verbose`, `--include-partial-messages`, `--fallback-model`, `--append-system-prompt`, `--add-dir`.

- `--bare` kullanılmaz: abonelik (OAuth) girişini okumaz.
- Yeni: `--restricted` bypass modunu reddettiği için kullanılmaz. `--safe-mode` kancaları kapattığı için kullanılmaz.

Olay biçimi ve kancanın bypass modunda çalışması, kullanıcı giriş yaptıktan sonra doğrulanıp aşağıya eklenecek.

### Kota olayı (`rate_limit_event`)

1 Ekim 2026'daki gerçek koşuda görüldü. Her çağrının başında gelir. `status` üç değer alır: `allowed`, `allowed_warning` (pencere doluyor) ya da `rejected` (kota doldu). `resetsAt` Unix saniyesidir. `rateLimitType` dolan pencereyi söyler; `five_hour` ve `seven_day` hesabın ortak pencereleridir. Aynı koşuda Opus'un çağrısı pencereyi %98, Sonnet'inki %100 gördü, sıfırlanma anı ikisinde de aynıydı.

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1790851200,"rateLimitType":"five_hour","isUsingOverage":false,"unifiedWindows":{"five_hour":{"utilization":1,"resetsAt":1790851200},"seven_day":{"utilization":0.85,"resetsAt":1791306000}}}}
```

Kota dolunca ardından sentetik bir `assistant` mesajı gelir: `"error":"rate_limit"`, metin `You've hit your session limit · resets 1:40pm (Europe/Istanbul)`. Sonra `is_error: true` ve `api_error_status: 429` olan bir `result` gelir ve süreç 1 koduyla çıkar. Jev önce bu olayı okur; metin kalıpları yedektir.
