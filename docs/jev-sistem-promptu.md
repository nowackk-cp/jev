# JEV · Çok Ajanlı Yazılım Ofisi · Kurulum Promptu

> Bu belge "Jev" adlı sistemi sıfırdan tasarlayıp kurman için gereken her şeyi içerir. Başka bir kaynağa ihtiyacın yok. Kullanıcının dili Türkçe.
>
> **Not (1 Ekim 2026):** Bu, sistemin ilk kurulum belirtimidir. Sistem kurulduktan sonra kullanıcının istekleriyle değişti. Son değişiklikte Astra ekipten çıkarıldı: planı ve görev kartlarını Opus tek çağrıda yazar, son kontrolü Sol 6.1 yapar. Belge bu değişikliğe göre güncellendi. Sonradan gelen özelliklerin ayrıntısı için güncel kaynak `README.md` ve `docs/tasarim.md`'dir: ölçekler (mini, küçük, orta, büyük), Jev modeli (TypeSafe), kalite kuralları ve paralel mod. Rol promptlarının yetkili metni `jev/prompts/` altındadır.

---

## 0. Ne kuruyorsun

Kullanıcı terminale tek bir cümle yazar: `jev "X için Y tasarla"`. Bunun üzerine bir "yazılım ofisi" çalışmaya başlar:

1. **Opus** (Claude Opus 5.5) isteği inceler ve tek çağrıda şunları yazar: ihtiyaçlar (araştırma, veri, web sitesi, araç, hazır kaynak), kısa bir plan, sözleşmeler ve görev kartları. **Jev** kartları kodla doğrular.
2. **Jev**, yani senin yazacağın Python orkestratörü, sırası gelen her kartı duruma göre bir işçi ajana verir. İşçiler: GPT-6.1-Sol, Claude Sonnet 5.5, GPT-6-Luna. İşçi yalnızca kendi kartını ve kartta adı geçen dosyaları okur.
3. Sorun çıkarsa Jev karar verir. Jev'in beyni Claude Opus 5.5'tir. İşçilerde takılan bir kartı Jev, Opus'a verebilir (eskalasyon).
4. İş bitince **Sol** (GPT-6.1-Sol) plana göre son kontrolü yapar ve bir rapor yazar.
5. Kullanıcı raporu okur. "Düzelt" derse bir düzeltme turu başlar.

Küçük işte (mini ya da küçük ölçek) plan, kart ve denetçi yoktur. Tek bir işçi işi baştan sona yapar, raporu Jev yazar.

Kullanıcı bütün bunları canlı bir **ofis arayüzünden** izler. Bu arayüzde her ajan, işçi kılığında sevimli bir karakterdir.

Modellere kullanıcının iki aboneliği üzerinden, **yalnızca resmî CLI'larla** erişilir:
- ChatGPT Plus → **Codex CLI**
- Claude Pro → **Claude Code**

**Rolün:** Kıdemli bir yazılım mimarı ve Python mühendisi olarak bu sistemi *bu klasörde* tasarla, kodla, test et ve belgele.

**Çalışma biçimin:**
1. Bu belgeyi baştan sona oku.
2. `docs/tasarim.md` dosyasına kısa bir uygulama planı yaz. Bu belgeden sapacağın yerleri gerekçeleriyle birlikte oraya ekle.
3. Kilometre taşlarını (§9) sırayla uygula. Her birinin sonunda testleri çalıştır ve kısa bir ilerleme notu ver.
4. Kullanıcıya yalnızca gerçekten tıkandığında soru sor, örneğin bir CLI'da oturum açılmamışsa. Tasarımdaki boşlukları makul varsayımlarla doldur ve bu varsayımları `docs/tasarim.md`'ye yaz.
5. Kendi oturumun kısıtlı bir sandbox içindeyse ve `codex`/`claude` çağrıları ağ ya da izin yüzünden başarısız oluyorsa, kullanıcıdan oturumu tam erişim modunda başlatmasını iste.

---

## 1. Değişmez kurallar

Bunlar kullanıcının kararlarıdır. Hiçbirini değiştirme.

1. Giriş tek cümledir: `jev "X için Y tasarla"`. Etkileşimli modda istek doğrudan yazılabilir.
2. **Opus** (`claude-opus-5-5`) planı ve görev kartlarını **tek çağrıda** yazar. Bu çağrıda ihtiyaçlar, amaç, modüller, mimari, başarı ölçütleri, sözleşmeler ve kartlar birlikte çıkar. Ayrı bir parçalama çağrısı yoktur.
3. Her görev kartı **kendi kendine yeter**. Kartta şunlar bulunur: hedef, okunacak ve yazılacak dosyalar, ilgili sözleşmelerin tanımı, doğrudan bağımlılıkların çıktıları, kabul ölçütleri ve doğrulama komutları. İşçi planı açmaz, klasörü taramaz; kart yetmezse "takıldım: …" diyerek döner. Opus kod yazmaz; yalnızca Jev'in kararıyla (eskalasyon) takılan bir kartı kodlar.
4. **Jev** sırası gelen her görevi, durumuna göre bir işçi ajana verir.
5. Sorun çıkarsa **Jev karar verir.** Jev'in karar beyni **Claude Opus 5.5**'tir. Opus'un kotası doluysa yedek beyin **GPT-6.1-Sol (xhigh)** olur.
6. Her şey bitince **Sol** plana göre son kontrolü yapar ve rapor yazar. Küçük işte raporu Jev yazar. **Akış burada durur.**
7. Kullanıcı raporu okur. Düzeltme turu yalnızca kullanıcı "düzelt" derse başlar. Bunu üç yoldan diyebilir: `jev duzelt` komutu, etkileşimli modda `düzelt` yazmak ya da arayüzdeki **Düzelt** düğmesi. Kullanıcı istemeden düzeltme turu **başlamaz**.
8. İşçi ajanlar **tam yetkiyle** çalışır: onay sormadan komut çalıştırır, dosya yazar. Tek sınır, kapatılabilen bir felaket korumasıdır (§5.10).
9. Kullanıcı süreci canlı bir **ofis arayüzünden** izler (§7).
10. Her ajan yalnızca rolünün izin verdiği aşamada çağrılabilir. Plan ve düzeltme kartları planlayıcının, son kontrol denetçinin (büyük işte kodun çoğunu denetçi yazdıysa yedek denetçinin), karar beynin işidir. İşçi çağrısı için `isci` rolü ya da Jev'in eskalasyon kararı gerekir. Bunu kodda, tek çağrı kapısında zorunlu kıl ve testle doğrula.
11. Plan ve kartlar (Opus) ile son kontrol (Sol) başka bir modele devredilmez. Kotaları doluysa beklenir ya da koşu duraklatılır. Tek istisna yedek denetçidir (`[jev] yedek_denetci`, §6): büyük işte kodun yarısından fazlasını Sol yazdıysa son kontrolü Opus yapar; Opus'a ulaşılamazsa beklenmez, son kontrolü Sol yapar.

---

## 2. Ortam

Aşağıdakiler kullanıcının makinesinde doğrulandı. Yollar ve sürümler güncellemelerle değişebileceği için hepsini M0'da **yeniden doğrula**.

### Genel
- Windows 11, Windows PowerShell 5.1. `pwsh` (PowerShell 7) yok kabul et.
- Python 3.13 (`python` ve `py` komutlarıyla), Git 2.55.
- Node/npm, WSL ve Docker **yok**. Bu yüzden orkestratör Python 3.11+ ile yazılır. Arayüz de Node gerektirmemeli.

### Codex CLI
- Sürüm: `codex-cli 0.155.0-alpha`. Codex masaüstü uygulamasıyla birlikte gelir ve **PATH'te değildir**. Yolu: `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe`. Birden çok `<hash>` klasörü olabilir; en son değiştirileni seç.
- Kullanıcı ChatGPT hesabıyla giriş yapmış durumda. Giriş bilgisi `~/.codex/auth.json` dosyasında. **Bu dosyayı asla okuma, yazdırma ya da loglara koyma.**
- Modeller (`~/.codex/models_cache.json` dosyasından):
  - `gpt-6-astra`: "Frontier intelligence for the most demanding work" (1 Ekim 2026'dan beri kullanılmıyor)
  - `gpt-6-sol`: "Workhorse model for coding and everyday work" (Sol için artık `gpt-6.1-sol` kullanılıyor)
  - `gpt-6-luna`: "Fast and affordable model for easier tasks"
  - Bağlam penceresi 272k token.
- Efor, `-c model_reasoning_effort="<seviye>"` ile verilir. Seviyeler: `low`, `medium`, `high`, `xhigh`, `max`.
  - `ultra` seviyesini **kullanma**. Kendi alt ajanlarını açıyor ve orkestrasyonla çakışır.
- Doğrulanmış `codex exec` seçenekleri:
  - Prompt stdin'den okunur: argüman vermezsen ya da `-` verirsen.
  - `-m/--model`, `-c key=value` (TOML değer)
  - `-s/--sandbox read-only|workspace-write|danger-full-access`
  - `--dangerously-bypass-approvals-and-sandbox`
  - `-C/--cd <dizin>`, `--add-dir`
  - `--skip-git-repo-check`, `--ephemeral`
  - `--ignore-user-config`: kullanıcı ayarını yüklemez, ama giriş bilgisini yine CODEX_HOME'dan alır.
  - `--ignore-rules`
  - `--output-schema <dosya>`
  - `--json`: olayları stdout'a JSONL olarak basar.
  - `-o/--output-last-message <dosya>`
  - `-p/--profile`
  - `resume`
- Kullanıcının `~/.codex/config.toml` dosyasında masaüstü uygulamasına özgü ayarlar var:
  - `model="gpt-6-sol"`, `model_reasoning_effort="high"`, `service_tier="priority"`
  - `[windows] sandbox="elevated"`
  - bir `notify` programı
  - başlangıç zaman aşımı 120 sn olan bir `node_repl` MCP sunucusu
  - çok sayıda eklenti

  Bunlar arayüzsüz (headless) çağrıları yavaşlatabilir. M0'da iki birleşimi karşılaştır:
  - kullanıcı ayarları açıkken,
  - `--ignore-user-config` + gerekli `-c` ayarlarıyla (örn. `-c windows.sandbox="elevated"`).

  Hızlı ve çalışan birleşimi seç, sonucu `docs/cli-notlari.md`'ye yaz. `service_tier` ayarını `jev.yaml` içinden seçilebilir yap.

### Claude Code
- Sürüm `2.1.281`. Claude masaüstü uygulamasıyla birlikte gelir ve **PATH'te değildir**. Yolu: `%APPDATA%\Claude\claude-code\<sürüm>\claude.exe`. En yüksek sürümü seç. Ayrıca PATH'e ve `~/.local/bin/claude.exe` yoluna da bak.
- Modeller: `claude-opus-5-5` (Opus 5.5) ve `claude-sonnet-5-5` (Sonnet 5.5). Takma adları: `opus`, `sonnet`.
- Doğrulanmış seçenekler:
  - `-p/--print` (prompt stdin'den de okunur)
  - `--model`, `--effort low|medium|high|xhigh|max`
  - `--output-format text|json|stream-json`, `--json-schema <şema-json>`
  - `--dangerously-skip-permissions`, `--permission-mode <mod>`
  - `--tools "Read,Grep,Glob"`: kullanılabilecek yerleşik araçları sınırlar.
  - `--allowed-tools`, `--disallowed-tools`
  - `--settings <dosya-veya-json>`: kancalar buradan verilir.
  - `--append-system-prompt`, `--add-dir`
  - `--no-session-persistence`
  - `--fallback-model`
  - `--strict-mcp-config`, `--mcp-config`, `--setting-sources`
  - `--include-partial-messages`, `--verbose`
- **`--bare` bayrağını KULLANMA.** Bu bayrak OAuth ile yapılan abonelik girişini okumaz, yalnızca API anahtarıyla çalışır. Kullanıcının Pro aboneliği devre dışı kalır.
- Masaüstü uygulamasıyla gelen `claude.exe` tek başına çalıştırıldığında oturum açık olmayabilir. M0'daki deneme mesajı başarısız olursa kullanıcıdan `claude auth login` (ya da `claude setup-token`) çalıştırmasını iste. **Giriş bilgilerine sen dokunma.**

### Abonelikler
- Aboneliklere yalnızca resmî CLI'lar üzerinden eriş. Abonelik token'larını başka bir araca taşımaya ya da OAuth dosyalarını okumaya çalışma.
- İki aboneliğin de 5 saatlik ve haftalık kullanım limitleri var. En pahalı çağrılar Opus'unkiler (plan ve kartlar, beyin kararları) ve yüksek eforlu Sol çağrıları. Tasarım kotayı korumalı (§5.8).

---

## 3. Ekip ve roller

| Kimlik | Model | CLI | Rol | Yetki | Varsayılan efor |
|---|---|---|---|---|---|
| `opus` | claude-opus-5-5 | claude | Planlayıcı (plan, görev kartları, düzeltme kartları) + Jev'in beyni + eskalasyon: işçilerde takılan kartı Jev'in kararıyla kodlar + yedek denetçi: büyük işte kodun çoğunu Sol yazdıysa son kontrolü yapar | Plan, beyin ve denetim: yalnızca `Read,Grep,Glob` (yeni projede plan araçsız). Eskalasyonda: tam yetki | plan: orta işte `high`, büyükte `xhigh` · eskalasyon: `high`, L: `xhigh` · denetim: `high` |
| `sol` | gpt-6.1-sol | codex | İşçi, ana kodlayıcı + son denetçi (orta ve büyük iş; büyük işte kodun çoğunu kendisi yazdıysa son kontrolü Opus yapar) | İşçi olarak: tam yetki. Denetimde: salt okuma (`-s read-only`); raporu orkestratör yazar | S: `medium`, M: `high`, L: `xhigh` · denetim: orta işte `medium`, büyükte `high` |
| `sonnet` | claude-sonnet-5-5 | claude | İşçi | Tam yetki | `high` |
| `luna` | gpt-6-luna | codex | İşçi: kolay/hızlı işler, belgeleme, yapılandırma | Tam yetki | `medium` |
| `jev` | (Python) | yerel | Orkestratör: sıra, atama, doğrulama, git, kota, olaylar, arayüz | — | — |

Jev'in beyni `opus`, yedeği `sol` (`xhigh`). Beyin çağrıları her zaman salt okumadır.

Opus'un tek bir Claude Pro kotası var. Bu kota plan ve kartlar, beyin kararları ve eskalasyon arasında (Sonnet ile birlikte) paylaşılır. Opus soğumadayken beyin yedeğe (Sol) düşer. Plan ve düzeltme kartları ise başka modele verilmez (kural 11).

Sol da iki işi birlikte taşır: ana kodlama ve son kontrol. Sol soğumadayken kod kartları sıradaki uygun işçiye geçer, son kontrol ise Sol'u bekler.

---

## 4. Akış

```
kullanıcı: jev "X için Y tasarla"
   │
   ▼
[0] ÖLÇEK ── Jev: mini · küçük · orta · büyük
   │        (mini ve küçükte [1] atlanır: tek işçi işi baştan sona yapar, gerekirse kendisi araştırır)
   ▼
[1] PLAN VE KARTLAR ── Opus (salt okuma; yeni projede araçsız) ──► plan.md + plan.json + tasks.json
   │        ihtiyaçlar, kısa plan, sözleşmeler ve görev kartları tek çağrıda
   │        → Jev kartları kodla doğrular (hata varsa hata listesi + önceki çıktı Opus'a geri, en fazla 2 kez)
   │        → aynı dosyaya dokunan kartları Jev kendisi sıraya koyar
   │        (büyük işte ya da --onay verildiyse burada kullanıcı onayı beklenir)
   ▼        AGENTS.md / CLAUDE.md üretilir, git dalı açılır
[2] UYGULAMA ── Jev döngüsü:
   │   hazır kart seç (araştırma kartları önce) → ajan seç → kartı hazırla → ajan çalışır (tam yetki)
   │   → Jev doğrular (verify komutları, testler, diff) → commit
   │   → sorun çıkarsa: kural katmanı (kota/geçici hata) ya da Jev'in beyni (Opus) karar verir
   ▼
[3] SON KONTROL ── Jev tüm doğrulamaları çalıştırır, kanıt paketini ve görsel kareleri hazırlar
   │        → Sol (salt okuma) ──► report.md + report.json   (mini ve küçükte raporu Jev yazar)
   │
   ■ DUR. Kullanıcı raporu okur.
   │
   └─ kullanıcı "düzelt" derse → [D] Opus eksiklerden düzeltme kartları çıkarır, Jev doğrular → [2] → [3] → yeni rapor → DUR
```

---

## 5. Teknik tasarım

### 5.1 Dizin yapısı

Bu bir öneri. İyileştirebilirsin; yaptığın değişiklikleri `docs/tasarim.md`'ye yaz.

```
./                                  ← bu depo
  pyproject.toml                    # konsol betiği: jev = jev.cli:main
                                    # bağımlılıklar: rich, pyyaml, jsonschema · test: pytest
  jev/
    cli.py  config.py  runner.py
    events.py                       # olay kanalı: events.jsonl + abonelere yayın (thread-safe)
    state.py                        # koşu durumu, atomik yazma, kilit, devam
    phases/  plan.py  dispatch.py  brain.py  review.py  fix.py   # plan.py planı ve görev kartlarını birlikte üretir
    adapters/  base.py  codex.py  claude.py  mock.py
    routing.py  verify.py  gitops.py  quota.py  context.py  guard.py  discovery.py
    mock/senaryo_varsayilan.yaml    # --kuru için senaryo
    prompts/*.md                    # rol promptları (§8), kullanıcı düzenleyebilir
    schemas/*.schema.json           # yapısal çıktı şemaları (§5.4)
    office/
      server.py                     # stdlib HTTP sunucusu + SSE
      static/  index.html  office.css  office.js  characters.js
    jev.yaml                        # varsayılan ayarlar
  tests/
  docs/  tasarim.md  cli-notlari.md
  README.md                         # Türkçe
```

Kullanıcıya ait ayar ve kalıcı kayıtlar `%USERPROFILE%\.jev\` altında durur:
- `jev.yaml`: varsayılanların üzerine yazar.
- `kota.json`: ajan soğumaları; koşular arasında paylaşılır.
- `bilinmeyen-hatalar.log`

Hedef projedeki koşu kayıtları:

```
<proje>/.jev/
  lock
  runs/<YYYYMMDD-HHMMSS>-<kısa-slug>/
    request.md  plan.md  plan.json  tasks.json  state.json
    events.jsonl  decisions.jsonl  guard.jsonl  progress.md  usage.json
    report.md  report.json            # düzeltme turlarında: report-2.md, report-2.json …
    calls/<sıra>-<aşama>-<görev>-<ajan>.{prompt.md, stdout.jsonl, stderr.txt, out.json}
```

`.jev/` her zaman `.git/info/exclude` dosyasına eklenir. Böylece hiç commit edilmez ve `git clean` ile silinmez.

### 5.2 Ayarlar (`jev.yaml` örneği)

```yaml
proje_koku: "%USERPROFILE%/jev-projeler"   # yeni projeler burada açılır
paralel: 1                                  # 1 = sıralı (varsayılan). Paralel mod opsiyonel (§9, M12)
plan_onayi: false                           # --onay bayrağı bunu true yapar
arayuz: {acik: true, port: 8765, tarayici_ac: true}   # port doluysa sıradaki boş port kullanılır
zaman_asimi_dk: {plan: 30, isci_S: 20, isci_M: 40, isci_L: 60, beyin: 10, denetim: 30, verify: 10}
sinirlar: {gorev_basina_deneme: 4, kosu_basina_beyin_cagrisi: 25, en_fazla_gorev: 40, paket_karakter: 60000, onarim_denemesi: 2, denetim_karesi: 6}
her_gorevde_tam_test: true
guvenlik: {tam_yetki: true, koruma: true}
cli:
  codex: auto            # veya tam yol
  claude: auto
  codex_kullanici_ayarini_yoksay: auto      # M0 sonucuna göre true/false
  codex_service_tier: null                  # null = dokunma
ajanlar:
  opus:   {saglayici: claude, model: claude-opus-5-5, roller: [planlayici, beyin, eskalasyon], efor: {varsayilan: high, plan: xhigh, L: xhigh, denetim: high}}
  sol:    {saglayici: codex,  model: gpt-6.1-sol,     roller: [isci, denetci], efor: {S: medium, M: high, L: xhigh, denetim: high}}
  sonnet: {saglayici: claude, model: claude-sonnet-5-5, roller: [isci], efor: {S: medium, M: high, L: high}}
  luna:   {saglayici: codex,  model: gpt-6-luna,      roller: [isci], efor: {S: medium, M: medium, L: high}}
jev:
  planlayici: opus
  denetci: sol
  yedek_denetci: {ajan: opus, olcekler: [buyuk]}   # kodun çoğunu denetçi yazdıysa son kontrolü yapar
  beyin: opus
  yedek_beyin: {ajan: sol, efor: xhigh}
yonlendirme:
  S: [luna, sonnet, sol]
  M: [sol, sonnet]
  L: [sol, sonnet]
  tur: {docs: [luna, sonnet], test: [sonnet, sol], config: [luna, sol], research: [sonnet, sol]}
  saglayici_dengesi: 2   # eşdeğer bir alternatif varsa aynı sağlayıcıya art arda en fazla 2 görev
  eskalasyon: [opus]     # 'isci' rolü olmayan ama Jev'in kararıyla takılan kartı alabilen ajanlar
kota:
  bekleme_esigi_dk: 30
  varsayilan_soguma_dk: 60
  kaliplar: ["usage limit", "rate[ _-]?limit", "limit reached", "hit your (?:\\w+ )?limit", "quota", "too many requests", "\\b429\\b"]
  # sıfırlanma zamanı ayrı ayrıştırılır ("resets 3pm", "try again at 15:42" …).
  # "reset" kelimesini kalıp yapma: "connection reset" gibi ağ hatalarıyla karışır.
```

Ayar yüklenirken şunları doğrula:
- `jev.planlayici` ajanının `planlayici` rolü, `jev.denetci` ajanının `denetci` rolü olmalı; `jev.beyin` ve `jev.yedek_beyin` tanımlı birer ajan olmalı.
- `jev.yedek_denetci` verilirse `ajan` tanımlı bir ajan, `olcekler` ölçek adlarından (`mini`, `kucuk`, `orta`, `buyuk`) oluşan bir liste olmalı. Yedek denetçi için rol gerekmez; boş liste ya da denetçinin kendisi yedeği kapatır.
- Sağlayıcı yalnızca `codex` ya da `claude` olabilir.
- Roller geçerli olmalı: `planlayici`, `denetci`, `beyin`, `eskalasyon`, `isci`.
- Yönlendirme listelerindeki her ajanın `isci` rolü olmalı.
- `yonlendirme.eskalasyon` listesindeki her ajanın `eskalasyon` ya da `isci` rolü olmalı.

### 5.3 Ajan adaptörleri

Ortak arayüz:

```python
class AgentAdapter:
    def run(self, *, agent: AgentSpec, prompt: str, cwd: Path,
            mode: Literal["readonly", "full"], schema: dict | None,
            effort: str, timeout_s: int, call_dir: Path,
            on_activity: Callable[[AgentActivity], None]) -> AgentResult: ...

# AgentResult alanları:
#   ok, structured (dict | None), text, exit_code, duration_s, usage (token sayıları),
#   quota_hit, reset_at, error_kind (none | timeout | crash | quota | schema | auth | transient),
#   ham kayıt yolları
# AgentActivity alanları:
#   kind (thinking | reading | editing | command | testing | message), text, path (isteğe bağlı)
```

**Codex komut şablonu.** Prompt her zaman stdin'den verilir, asla argüman olarak verilmez.
```
<codex.exe> exec -m <model> -c model_reasoning_effort="<efor>"
    [salt okuma]  -s read-only
    [tam yetki]   --dangerously-bypass-approvals-and-sandbox
    -C <proje> --skip-git-repo-check --ephemeral --json
    --output-schema <şema.json> -o <out.json>
    [M0 sonucuna göre]  --ignore-user-config -c windows.sandbox="elevated"
    -
```

**Claude komut şablonu.** Çalışma dizini proje klasörüdür, prompt stdin'den verilir.
```
<claude.exe> -p --model <model> --effort <efor>
    --output-format stream-json --verbose        # nihai sonuç "result" olayında gelir; M0'da doğrula
    --json-schema "<tek satır şema json>"
    --no-session-persistence
    [salt okuma]  --tools "Read,Grep,Glob" --dangerously-skip-permissions
    [tam yetki]   --dangerously-skip-permissions --settings <koşu klasöründeki jev-guard-settings.json>
```

**Uygulama notları:**
- **Yürütülebilir dosyayı bulma** (`discovery.py`): önce `shutil.which`, sonra bilinen klasörler (glob ile, en yeni sürüm). `jev ajanlar` hangi dosyanın seçildiğini ve sürümünü göstersin.
- **Süreç başlatma:**
  - `subprocess.Popen([...], stdin=PIPE, stdout=PIPE, stderr=PIPE, encoding="utf-8", errors="replace", cwd=..., env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)`
  - `env["PYTHONUTF8"]="1"`
  - Yeni süreç grubu sayesinde Ctrl+C alt süreçlere gitmez; orkestratör onları kendisi düzgünce kapatır.
- **Çıktıyı okuma:** stdout ayrı bir iş parçacığında satır satır okunur. Her satır hem ham kayıt dosyasına yazılır hem de etkinlik ayrıştırıcısına verilir.
- **Zaman aşımı:** `taskkill /PID <pid> /T /F` ile tüm süreç ağacı kapatılır.
- **Yapısal çıktı:**
  - Codex'te `-o` dosyasına yazılan son mesajdır.
  - Claude'da `result` olayındaki `structured_output` alanıdır. Bu alan yoksa `result` metninden JSON ayıkla.
  - Hangisinin nerede geldiğini M0'da doğrula.
  - Şemanın metnini promptun sonuna da ekle ("ÇIKTI ŞEMASI:").
  - Sonucu `jsonschema` ile doğrula. Geçersizse ajana hata listesini gönderip "yalnızca düzeltilmiş JSON'u döndür" de. Bu onarım çağrısını en fazla 2 kez yap.
- **Şemalar OpenAI katı moduna uymalı:**
  - Her nesnede `additionalProperties: false` olsun, bütün alanlar `required` listesinde olsun.
  - İsteğe bağlı alanları tip birleşimiyle ifade et, örneğin `["string","null"]`.
  - `pattern`, `format`, `minItems` gibi riskli anahtar kelimelerden kaçın. Bu tür ek kontrolleri Python tarafında yap.
  - Aynı şemalar Claude'da da kullanılır. İki CLI'da da M0'da dene.
- **Etkinlik ayrıştırma** (arayüzdeki konuşma balonları bundan beslenir):
  - Codex: `--json` olaylarındaki komut çalıştırma, dosya değişikliği, akıl yürütme özeti ve ajan mesajları.
  - Claude: `stream-json` içindeki `tool_use` blokları (Bash/PowerShell komutları, Edit/Write/Read dosya yolları) ve metin.
  - Bunların hepsini `AgentActivity` biçimine çevir. İçinde `pytest`, `unittest`, `npm test`, `go test`, `cargo test` vb. geçen komutlar `testing` sayılır.
  - Gerçek olay adlarını ve alanlarını M0'da küçük bir çağrıyla yakala. Örnek satırlarla birlikte `docs/cli-notlari.md`'ye yaz ve ayrıştırıcıyı bu örneklerle test et.
- **Kota tespiti:**
  - `kota.kaliplar` listesini **yalnızca CLI'ın kendi hata kanallarında** ara: stderr, hata olayları, `is_error` olan sonuçlar ve sıfır dışı çıkış kodu. Ajanın yazdığı kodda ya da araç çıktılarında arama; yanlış alarm üretir.
  - Claude'da önce `stream-json` içindeki `rate_limit_event` olayına bak: `status: "rejected"` kotanın dolduğunu, `resetsAt` (epoch) sıfırlanma anını söyler. `rateLimitType` `five_hour` ya da `seven_day` ise dolan pencere hesabın ortak penceresidir; aynı sağlayıcının bütün ajanları (Opus ve Sonnet) birlikte soğur. Modele özgü pencerede yalnızca çağrılan ajan soğur. Kalıplar yedektir.
  - Sıfırlanma zamanını ayrıştırmayı dene: epoch değeri, "resets 3pm", "try again at 15:42", "in 2h 14m" gibi. Başaramazsan `varsayilan_soguma_dk` kullan. Geçmişte kalmış bir saat en az 1 dakikalık soğuma sayılır; yoksa ajan hemen müsait görünür ve çağrı döngüye girer.
  - Tanınmayan hata metinlerini `%USERPROFILE%\.jev\bilinmeyen-hatalar.log` dosyasına yaz. Kalıplar sonradan bununla iyileştirilir.
- **Kullanım istatistikleri:** Claude sonucundaki `usage` alanını ve Codex JSONL'daki token olaylarını `usage.json` dosyasına yaz: her ajan için çağrı sayısı, süre, giriş ve çıkış token'ları.
- **Sahte adaptör** (`mock.py`), `--kuru` modu ve testler için:
  - YAML senaryodan okur. Belirli görevlerde başarısızlık, kota olayı, bozuk JSON, zaman aşımı ve `blocked` durumu üretebilir.
  - Gerçekçi etkinlik olaylarını ve gecikmeleri yayar: düşünüyor → dosya okuyor → düzenliyor → test. Böylece arayüz canlı görünür. `--kuru-hiz` ile hızlandırılabilir.
  - Sahte işçiler **gerçekten** küçük dosyalar yazar ki git, doğrulama ve geri alma yolları da test edilsin. Örnek: sahte `sol` bir görevde hatalı kod yazar ve test gerçekten kalır. Jev'in (sahte) beyni görevi `sonnet`'e verir, `sonnet` doğrusunu yazar.

### 5.4 Şemalar

Tam JSON Schema dosyalarını sen yaz. Alanlar şunlar:

1. **`plan.schema.json`** (Opus; plan ve görev kartları tek çıktıda):
   - `project_name`
   - `slug`: ASCII, kebab-case
   - `summary`: en fazla 10 satır
   - `plan_markdown`: plan; başlıkları §8.1'de (orta işte kısa, büyükte tam)
   - `stack`: `{language, frameworks[], runtime_notes}`
   - `commands`: `{install, test, run, lint}`. Her biri `string|null` ve PowerShell'de çalışır olmalı.
   - `modules[]`: `{id: "M1", name, responsibility, interfaces, depends_on[]}`
   - `success_criteria[]`: `{id: "SC1", statement, verification, command: string|null}`
   - `assumptions[]`, `out_of_scope[]`, `risks[]`
   - `conventions`: AGENTS.md'ye girecek kodlama kuralları
   - `scale: orta|buyuk|null`: iş, Jev'in ölçtüğünden belirgin biçimde büyükse
   - `needs[]`: `{kind: research|data|website|tool|resource, what, task: string|null}`
   - `contracts[]`: `{id: "C1", name, definition}`. Kartlar arasındaki arayüzlerdir.
   - `tasks[]`: görev kartları (biçimi aşağıda)
2. **`tasks.schema.json`** (Opus, düzeltme kartları; görev kartının biçimi):
   - `tasks[]`, her biri:
     - `id: "T01"`, `title`, `module`
     - `type: code|test|docs|config|research`
     - `description`: işçinin bilmesi gereken her şey; işçi planı görmez
     - `reads[]`: işçinin okuyacağı mevcut dosyalar
     - `outputs[]`: dosya yolları
     - `contracts[]`: kartın uyduğu ya da sağladığı sözleşmelerin kimlikleri
     - `acceptance[]`
     - `verify[]`: PowerShell komutları
     - `depends_on[]`, `covers_criteria[]`
     - `complexity: S|M|L`
     - `suggested_agent: sol|sonnet|luna|null`
     - `notes`
3. **`worker_result.schema.json`** (işçiler):
   - `status: done|blocked|failed`
   - `summary`
   - `changed_files[]`
   - `verification[]`: `{command, passed, output_tail}`
   - `blocker: string|null`
   - `notes`, `follow_ups[]`
4. **`jev_decision.schema.json`** (beyin):
   - `decision: retry|reassign|split|revise|skip|pause|abort`
   - `agent: sol|sonnet|luna|opus|null` (`opus`: eskalasyon)
   - `effort: low|medium|high|xhigh|max|null`
   - `rollback: bool`
   - `guidance`: sonraki denemeye not
   - `revised_task: görev|null`
   - `new_tasks[]`
   - `rationale`
   - `deviation: none|minor|major`
   - `report_note`
5. **`review.schema.json`** (Sol, son kontrol):
   - `verdict: basarili|kismen|basarisiz`
   - `summary`, `report_markdown`
   - `criteria[]`: `{id, status: met|partial|unmet|unverified, evidence, gap: string|null}`
   - `gaps[]`: `{id: "G1", criteria[], description, suggested_fix}`
   - `risks[]`, `next_steps[]`

Görev kartının biçimi **dört** yerde kullanılır: plan, Jev'in `split` ve `revise` kararları ve düzeltme turu. Hepsinde aynı doğrulama çalışır (`dag.validate_tasks`):
- Kimlikler geçerli ve benzersiz olmalı; var olan görevlerle çakışmamalı.
- `depends_on` yalnızca var olan görevleri göstermeli. Döngü olmamalı.
- `module`, `covers_criteria` ve `contracts` plandakileri göstermeli.
- `code` ve `test` kartlarında en az bir `verify`, `research` kartında `outputs` olmalı.
- Bir karta bağlanan ihtiyacın kartı var olmalı.
- Her başarı ölçütü (SC) ve her modül (M) en az bir kartla karşılanmalı.
- Görev sayısı ≤ `en_fazla_gorev` olmalı.

Plan ya da düzeltme kartları doğrulanamazsa hata listesi ve önceki çıktı Opus'a geri gönderilir (`onarim_denemesi`, varsayılan 2). Ardından Jev şu kartları kendisi sıraya koyar (`dag.link_shared_files`): aynı dosyayı yazan kartlar ve başka bir kartın yazdığı dosyayı okuyan kartlar.

### 5.5 Koşu ve görev durumları

Koşu aşamaları:
```
sizing → planning → awaiting_approval (büyük işte ya da --onay ile) → executing → reviewing → reported
```
Mini ve küçük işte `planning` atlanır (`sizing → executing`). Eski koşulardan kalan `decomposing` aşaması devam edilince plana döner.
Bunlara ek olarak `paused` ve `aborted` vardır. Düzeltme turu `fixing` aşamasıyla başlar, sonra yine `executing → reviewing → reported` yolunu izler. Tur numarası `round` alanında tutulur.

Görev durumları kodda İngilizce, arayüzde Türkçe gösterilir:

| Kod | Arayüz | Anlamı |
|---|---|---|
| `pending` | Bekliyor | Bağımlılıkları bitmedi |
| `ready` | Sırada | Atanmayı bekliyor |
| `running` | Çalışıyor | Bir ajan üzerinde çalışıyor |
| `verifying` | Doğrulanıyor | Jev doğrulama komutlarını çalıştırıyor |
| `done` | Bitti | Doğrulandı ve commit edildi |
| `needs_decision` | Sorunlu | Jev'in beyni karar verecek |
| `waiting_quota` | Kota bekliyor | Uygun ajanların hepsi soğumada |
| `split` | Bölündü | Alt görevlere ayrıldı |
| `skipped` | Atlandı | Jev'in kararıyla atlandı |
| `failed` | Başarısız | Deneme hakkı bitti |
| `blocked` | Engellendi | Bir bağımlılığı başarısız oldu ya da atlandı ve Jev devam edilemeyeceğine karar verdi |

Her durum değişikliğinde iki şey yapılır:
- `state.json` atomik olarak yazılır: önce geçici dosyaya yazılır, sonra `os.replace` ile yerine konur.
- Bir olay yayınlanır.

### 5.6 Jev: dağıtım ve doğrulama

Bu bir kural katmanıdır; model çağırmaz.

- **Hazır görev:** Bağımlılıklarının hepsi `done` olan görev hazırdır. Bir bağımlılık `skipped` ise, ancak Jev'in beyni bağımlı görevlerin devam edebileceğine karar verdiyse hazır sayılır.
- **Hangi görev önce:** Önce kritik yol, yani geçişli olarak en çok görevi bekleten görev. Eşitlikte düşük karmaşıklık, sonra kimlik sırası.
- **Hangi ajan:**
  1. Aday listesi: planın `suggested_agent` önerisi + `yonlendirme.tur[type]` (varsa) ya da `yonlendirme[complexity]`.
  2. Filtreler: ajanın `isci` rolü olmalı, kotası müsait olmalı ve bu görevde daha önce başarısız olmamış olmalı. Jev'in bir kararı aksini söylüyorsa bu son filtre uygulanmaz.
  3. Sağlayıcı dengesi uygulanır.
  4. Jev'in kararı belirli bir ajan ya da efor verdiyse o kullanılır.
  5. Hiçbir ajan müsait değilse görev `waiting_quota` durumuna geçer.
- **Görev kartı:** `context.py` §8.2'deki şablonu doldurur. Karta şunlar girer: kartın kendisi, kartta adı geçen sözleşmelerin tanımı (plandan) ve yalnızca doğrudan bağımlılıkların özetleri. Plan ve klasör ağacı gönderilmez. Paket `paket_karakter` sınırını aşarsa önce bağımlılık özetleri, sonra deneme geçmişi kısaltılır.
- **Doğrulama.** Ajan döndüğünde **Jev doğrular**; ajanın "bitti" demesine güvenilmez:
  1. Yapısal sonuç geçerli mi ve `status == done` mı?
  2. Görevin `verify` komutlarını proje klasöründe, zaman aşımıyla çalıştır:
     `powershell -NoProfile -NonInteractive -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; <komut>"`
  3. `her_gorevde_tam_test` açıksa `plan.json.commands.test` komutunu da çalıştır. Amaç, önceki görevlerin bozulup bozulmadığını görmek.
  4. `outputs` listesindeki dosyalar var mı? Kod görevinde diff boş olmamalı.
  5. Denetim:
     - İzlenen dosyalardan beklenmedik şekilde silinen var mı?
     - `.jev/` ya da `.git/` iç dosyalarına dokunulmuş mu?
     - `guard.jsonl` içinde engellenmiş bir girişim var mı? Engellemeler her durumda kaydedilir ve rapora girer. Görev ise yalnızca engellenen girişim "kimlik bilgisi" ya da "disk ve sistem" kategorisindeyse sorunlu sayılır. Örneğin engellenen bir `git commit` denemesi tek başına sorun değildir.
- **Başarılıysa:**
  - `git add -A`, ardından `git commit -m "jev(<id>): <başlık> [<ajan>]"`.
  - `progress.md` dosyasına 2-3 satırlık bir özet eklenir.
  - Görev `done` olur.
- **Başarısızsa:** §5.7'ye geçilir.

### 5.7 Jev: sorun yönetimi

**Önce kural katmanı.** Bu durumlarda beyin çağrılmaz ve deneme hakkı eksilmez:
- **Kota:**
  - Ajan sıfırlanma zamanına kadar soğumaya alınır. Dolan pencere hesabın ortak penceresiyse aynı sağlayıcının diğer ajanları da soğur.
  - Değişiklikler checkpoint'e geri alınır.
  - Görev sıradaki uygun ajana verilir.
  - Tüm işçiler soğumadaysa:
    - En erken sıfırlanma `bekleme_esigi_dk` içindeyse geri sayımla beklenir.
    - Değilse koşu `paused` yapılır, kaydedilir ve şu mesajla çıkılır: "Kota doldu; ~HH:MM'den sonra `jev devam`".
- **Geçici hata** (ağ, 5xx, "overloaded"): aynı ajanla 30-60 saniye sonra 1 kez daha denenir.
- **Bozuk JSON:** onarım çağrısı yapılır (en fazla 2).

**Diğer her durum Jev'in beynine gider:** doğrulama başarısız oldu, ajan `blocked` ya da `failed` dedi, zaman aşımı oldu, kapsam dışı değişiklik yapıldı, diff boş kaldı ya da koruma "kimlik bilgisi" veya "disk ve sistem" kategorisinde bir girişimi engelledi.

- **Kim karar verir:**
  - Beyin: `opus`, salt okuma modunda (`--tools "Read,Grep,Glob"`).
  - Opus soğumadaysa yedek beyin: `sol`, salt okuma sandbox'ında, `xhigh` eforla.
  - İkisi de müsait değilse basit kural uygulanır: görev bir kez farklı bir ajana verilir. Yine olmazsa koşu `paused` yapılır.
- **Beyne giden bağlam:**
  - plan özeti, ilgili modül ve ilgili başarı ölçütleri,
  - görev,
  - tüm deneme geçmişi: ajan, özet, hata ya da doğrulama çıktısının son ~150 satırı, diff istatistiği,
  - görev grafiğinin durumu,
  - müsait ajanlar ve soğumalar,
  - kalan limitler,
  - log dosyalarının yolları.
- **Kararların uygulanması:**
  - `retry`: aynı ajan, `guidance` notuyla tekrar dener.
  - `reassign`: görev `agent` alanındaki ajana, `effort` ve `guidance` ile verilir. `isci` rolü olmayan, yalnızca `yonlendirme.eskalasyon` listesindeki ajanlar ancak bu yolla iş alır (eskalasyon; varsayılanda Opus). Opus'un kotası plan ve beyinle ortak olduğu için daha ucuz bir ajan yetecekse o seçilir.
  - `split`: `new_tasks` DAG doğrulamasından geçer. Eski görev `split` olur. Ona bağımlı görevler, yeni alt görevlerin hepsine bağımlı hâle getirilir.
  - `revise`: görevin tanımı, okunacak dosyaları (`reads`), kabul ölçütleri ve verify komutları güncellenir. İşçi "takıldım: …" dediyse kart eksiktir; eksik bilgi karta eklenir. `covers_criteria` daraltılamaz. `plan.md` ve başarı ölçütleri **değiştirilemez**.
  - `skip`: görev `skipped` olur. Bir başarı ölçütünü karşılayan görev atlanıyorsa `deviation=major` zorunludur ve bu durum raporda öne çıkarılır.
  - `pause` / `abort`: durum kaydedilir ve kullanıcıya bildirilir.
  - `rollback=true` ise karar uygulanmadan önce checkpoint'e dönülür.
- **Kayıt:** Her karar `decisions.jsonl` dosyasına yazılır ve gerekçesiyle birlikte arayüze olay olarak gönderilir.
- **Sınırlar:**
  - Görev başına en fazla `gorev_basina_deneme` deneme yapılır; kota denemeleri sayılmaz. Bu sınır aşılırsa beyin yalnızca `skip`, `pause` ya da `abort` seçebilir.
  - Koşu başına `kosu_basina_beyin_cagrisi` aşılırsa koşu `paused` yapılır.
- **Kod garantisi:** Beyin kararı, işçi ya da eskalasyon rolü olmayan bir ajana (örneğin yalnızca denetçi olan bir ajana) görev veremez. Çağrı kapısı da rolü ve aşamayı denetler. Bunu birim testleriyle doğrula.

### 5.8 Kota ve dayanıklılık

- Soğuma kayıtları `%USERPROFILE%\.jev\kota.json` dosyasında tutulur, böylece koşular arasında da geçerlidir. `jev ajanlar` komutu bunları gösterir.
- Opus'un plan ya da düzeltme kartı çağrısında ya da Sol'un son kontrol çağrısında kota dolarsa bu işler başka modele verilmez (kural 11):
  - Sıfırlanmaya `bekleme_esigi_dk` kadar ya da daha az süre varsa beklenir.
  - Daha fazlaysa koşu `paused` yapılır.
- **Ctrl+C:**
  - Çalışan ajan durdurulur (süreç ağacıyla birlikte).
  - Değişiklikler geri alınır.
  - Görev `ready` durumuna döner.
  - Durum kaydedilir ve "`jev devam` ile sürdür" mesajı yazılır.
- **`jev devam`:**
  - `running` ya da `verifying` durumunda kalmış görevler checkpoint'e geri alınır ve `ready` yapılır.
  - Koşu kaldığı aşamadan sürer.
- **Kilit:** Aynı projede ikinci bir `jev` süreci çalışamaz. Bunun için `.jev/lock` dosyası ve PID kontrolü kullanılır. Artık çalışmayan bir sürece ait kilit temizlenir.

### 5.9 Git

- **Yeni proje:** `proje_koku` altında bir klasör açılır. Adı, istekten türetilen bir ASCII slug'dır (ı→i, ş→s, ğ→g, ç→c, ö→o, ü→u). Aynı adda klasör varsa sonuna `-2` eklenir. Ardından `git init`, `.gitignore` ve ilk commit yapılır.
- **Var olan proje** (`--proje <dizin>`): Çalışma ağacı temiz değilse işlem durur ve kullanıcıdan commit ya da stash yapması istenir. Otomatik stash yapılmaz.
- **Dal ve checkpoint:**
  - Koşu `jev/<run-id>` dalında çalışır.
  - Her görev denemesinden önce `HEAD` checkpoint olarak kaydedilir.
  - Geri alma: `git reset --hard <checkpoint>` ve ardından `git clean -fd -e .jev`.
- Push, merge ve rebase **yapılmaz**. Raporun sonunda kullanıcıya dalı nasıl birleştireceği anlatılır.
- İşçi ajanlar git durumunu değiştiren komutları kullanamaz: commit, reset, checkout, switch, rebase, push, clean, stash. Okuma komutları (status, diff, log) serbesttir.

### 5.10 Güvenlik: tam yetki ve felaket koruması

İşçiler, kullanıcının kararı gereği bypass bayraklarıyla çalışır. `guvenlik.koruma: true` iken ek olarak şu korumalar devrededir:

**Claude:** Her koşu için bir `jev-guard-settings.json` dosyası üretilir ve `--settings` ile verilir. Bu dosyada bir `PreToolUse` kancası bulunur. Beklenen biçim aşağıda; M0'da doğrula:
```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|Read",
  "hooks": [{"type": "command", "command": "\"<sys.executable tam yolu>\" -m jev.guard --saglayici claude --kosu \"<koşu klasörü>\""}]}]}}
```
- `jev.guard` araç çağrısını stdin'den JSON olarak okur.
- Reddederse Claude Code'un beklediği biçimde yanıt verir: 2 çıkış koduyla stderr'e gerekçe yazar ya da `permissionDecision: "deny"` içeren bir JSON döndürür.
- Her reddi `guard.jsonl` dosyasına yazar. Orkestratör bu dosyayı izler ve arayüze "Koruma: … engellendi" olayı gönderir.

Engellenecekler:
- **Disk ve sistem:**
  - `format`, `diskpart`, `bcdedit`, `reg delete`, `vssadmin delete`, `cipher /w`, `Stop-Computer`, `shutdown`.
  - Sürücü köküne ya da kullanıcı klasörünün tamamına yönelik `Remove-Item -Recurse`, `rm -rf`, `rd /s`.
- **Proje dışına yazma:** Proje klasörü ve sistem geçici klasörü dışına Write/Edit.
- **Kimlik bilgileri:** `~/.codex/auth.json`, `~/.claude/.credentials.json`, `~/.ssh`, tarayıcı profilleri, `cmdkey`.
- **Git:** `push`, `reset --hard`, `clean`, `checkout`/`switch` (dal değiştirme), `rebase`, `branch -D`.

Kancanın bypass modunda da çalıştığını zararsız bir test komutuyla doğrula.

**Codex:** Bu sürümde `.rules` desteği (execpolicy; `--ignore-rules` seçeneğinin varlığı buna işaret ediyor) ve kanca desteği (`--dangerously-bypass-hook-trust` seçeneğinin varlığı buna işaret ediyor) olabilir. Araştır:
- Bypass modunda da uygulanabiliyorsa aynı listeyi Codex'e de uygula.
- Uygulamak için kullanıcının `~/.codex` klasörüne bir şey eklemek gerekiyorsa **önce kullanıcıya sor.**
- Uygulanamıyorsa prompt kuralları ve görev sonrası denetimle (§5.6, madde 5) yetin ve bunu README'de açıkça yaz.

**Genel:**
- Giriş bilgisi dosyalarını hiçbir koşulda okuma, loglara yazma ya da prompta koyma.
- Ofis sunucusu için güvenlik kuralları §7.5'te.

### 5.11 Bağlam yönetimi

- **AGENTS.md ve CLAUDE.md:** Plan aşamasından sonra proje köküne `AGENTS.md` yazılır; Codex bu dosyayı kendiliğinden okur. İçeriği:
  - proje özeti,
  - yığın,
  - komutlar (install/test/run/lint),
  - kodlama kuralları (`conventions`),
  - çok ajanlı çalışma kuralları: yalnızca kendi görevin, git durumunu değiştirme, testleri çalıştır, PowerShell komutları kullan, sonunda JSON sonuç ver.

  `CLAUDE.md` dosyasına `@AGENTS.md` satırı konur; Claude Code bunu içe aktarır. Bu dosyalar zaten varsa üzerine yazılmaz. Bunun yerine `<!-- JEV:BASLA -->` … `<!-- JEV:BITIR -->` arasına bir bölüm eklenir ya da o bölüm güncellenir.
- **İlerleme özeti:** `progress.md` dosyası, tamamlanan her görev için kısa bir özet tutar: hangi dosyalar, ne eklendi. İşçi kartına yalnızca doğrudan bağımlılıkların özetleri girer.
- **Kim ne kadar görür:**
  - Opus isteği ve ortamı alır. Var olan projede dosya ağacını da alır; yeni projede klasör taranmaz.
  - İşçi yalnızca kendi kartını alır (kural 3). Kartta plan özeti, ilgili modül, kartın sözleşmeleri, doğrudan bağımlılıkların özetleri ve ilgili başarı ölçütleri bulunur.
  - Denetçi kanıt paketini alır (§6).

### 5.12 Komutlar ve terminal

**Komutlar:**
- `jev "istek" [--proje DİZİN] [--onay] [--kuru] [--kuru-hiz N] [--ayrintili] [--arayuz-yok]`
- `jev`: Etkileşimli mod. "Ne tasarlayalım?" diye sorar. Rapordan sonra aynı oturumda şu komutları kabul eder: `düzelt [not]`, `durum`, `rapor`, `devam`, `ofis`, `çıkış`.
- `jev devam [run-id]`
- `jev durum [run-id]`
- `jev rapor [run-id]`
- `jev duzelt [run-id] ["not"]`
- `jev ofis [run-id]`
- `jev ajanlar [--test]`
- `jev gecmis`

`run-id` verilmezse içinde bulunulan proje klasörünün ya da `proje_koku` altındaki en son koşu kullanılır.

**Terminal ekranı** (rich ile):
- Görünenler: aşama şeridi, ilerleme çubuğu, çalışan ajan ve eylemi, son 5 olay, ofis sayfasının adresi.
- `--ayrintili` verilirse ajan etkinlikleri de akar.
- **Rapordan sonra süreç kapanmaz.** Etkileşimli komut satırına geçer ve ofis sunucusunu açık tutar. Böylece kullanıcı ister terminale `düzelt` yazar, ister arayüzdeki düğmeye basar.

**`jev ajanlar --test`:**
- Her ajana en düşük eforla "Yalnızca OK yaz" mesajı gönderir.
- Gösterdikleri: süre ve sonuç tablosu, CLI yolları ve sürümleri, soğumalar, kullanım istatistikleri.

---

## 6. Son kontrol ve düzeltme turu

**Son kontrol (Sol; büyük işte kodun çoğunu Sol yazdıysa Opus; mini ve küçük işte Jev):**
1. Jev önce tüm görevlerin `verify` komutlarını ve `commands.test`'i çalıştırıp sonuçları toplar. Kodu o zamandan beri değişmeyen bir komutun görevdeki sonucu yeniden kullanılır. Görsel bir sonuç varsa Jev onun karelerini de çıkarır (en çok `denetim_karesi` kadar): sayfa ekran görüntüsü, videodan anlar, PDF sayfaları. Kareler başsız tarayıcıyla, geçici profille ve kullanıcıya gösterilmeden çıkarılır.
2. Denetçi testleri kendisi çalıştırmaz ve klasörü taramaz; Jev'in hazırladığı kanıt paketini okur. Pakette şunlar bulunur:
   - istek ve plan,
   - kartların son durumları ve özetleri,
   - Jev'in kararları, sapmalar ve işçilerin seçimleri,
   - test ve doğrulama sonuçları,
   - `git diff --stat <başlangıç>..HEAD` çıktısı ve değişikliklerin metni,
   - görsel kareler (Codex'e resim olarak eklenir; Claude CLI resim almadığı için Opus kareleri prompttaki yollarından `Read` ile açar).
3. Çıktı `report.md` (Türkçe, başlıkları §8.4'te) ve `report.json` dosyalarıdır.
   - Arayüzde rapor görünümü açılır.
   - Terminalde bir özet ve şu mesaj görünür: "Düzeltmek için: `düzelt [not]` yaz ya da arayüzdeki Düzelt düğmesine bas".
   - İsteğe bağlı olarak bir Windows bildirimi gösterilir.
4. **Akış durur.**

Mini ve küçük işte denetçi çağrılmaz; raporu Jev doğrulama sonuçlarından kendisi yazar.

**Yedek denetçi: kimse kendi işini denetlemesin.** Ölçek `[jev] yedek_denetci.olcekler` listesindeyse (varsayılan yalnızca büyük) Jev son kontrolden önce kodu kimin yazdığını ölçer. Başlangıçtan HEAD'e görev commit'lerinde (`jev(T03): <başlık> [sol]`) ajan başına değişen satırları sayar (eklenen + silinen, `git log --numstat`). Kilit dosyaları, küçültülmüş dosyalar, veri dosyaları (`.json`, `.csv`, `.svg` …), ikili dosyalar, Jev'in ortak bağlamı (`AGENTS.md`, `CLAUDE.md`) ve görev dışı commit'ler sayılmaz. Satırların yarısından fazlası denetçinindiyse son kontrolü yedek denetçi (Opus, efor `high`) yapar. Her turda yeniden ölçülür; düzeltme commit'leri de sayılır.
- Yedek soğumadaysa beklenmez: son kontrol denetçide kalır.
- Yedeğin çağrısı yarıda kalırsa (kota, oturum, iki başarısız deneme) koşu beklemez ve duraklatılmaz: son kontrol aynı turda denetçiye döner.
- Son kontrolü kimin yaptığı ve nedeni terminalde, ofiste ("raporu Opus yazacak") ve rapor ekinde görünür: `- Son kontrol: Opus — kodun çoğunu Sol yazdı (%72)`.
- `olcekler = []` yedek denetçiyi kapatır; `["orta", "buyuk"]` orta işe de açar.

**Düzeltme turu.** Yalnızca kullanıcı istediğinde başlar:
1. Opus'a şunlar verilir:
   - plan,
   - son rapor (markdown, `gaps` ve `criteria`),
   - kullanıcının notu,
   - mevcut kartlar ve durumları,
   - `git log --oneline` çıktısı.
2. Opus düzeltme kartları üretir:
   - Kimlik öneki `D<tur>-01`, `D<tur>-02` … biçimindedir.
   - Aynı kart biçimi ve aynı doğrulama kullanılır; kartlar kendi kendine yeter.
   - Her kartın `notes` alanında kapattığı `gap` kimliği yazar.
3. Jev bu kartları aynı kurallarla dağıtır. Sorun çıkarsa yine Jev'in beyni karar verir.
4. Sol planın tamamını yeniden denetler ve `report-<tur>.md` yazar. Raporda önceki raporla arasındaki fark belirtilir. Akış yine durur.
5. Rapor tamamen başarılıysa ve kullanıcı bir not vermediyse "Düzeltilecek eksik yok" denir ve tur açılmaz.

Mini ve küçük işte düzeltmeyi yine tek işçi yapar; kullanıcının notu işçinin promptuna girer.

---

## 7. Ofis arayüzü

### 7.1 Genel
- `jev "istek"` başlarken sunucu da başlar ve tarayıcıda `http://127.0.0.1:<port>/?k=<erişim-anahtarı>` açılır.
- `jev ofis [run-id]` bitmiş ya da durmuş bir koşuyu açar. Olaylar `events.jsonl`'dan yüklenir. İsteğe bağlı bir "tekrar oynat" modu hızlandırılmış gösterim yapar.
- Sayfa tek parçadır: `index.html`, `office.css`, `office.js`, `characters.js`.
  - Framework, derleme adımı, CDN ve harici font yok; sistem fontları kullanılır.
  - İnternet olmadan da çalışır.
- Açık ve koyu tema vardır: `prefers-color-scheme` ile otomatik seçilir, elle de değiştirilebilir.
- **Ekran boyutları:** 1280 ve 1920 piksel genişlikte düzgün görünür. Dar pencerede (örneğin terminalin yanında 900 piksel) bölümler alt alta dizilir.
- **Dil:** Tüm metinler Türkçedir.
- **Erişilebilirlik:**
  - Animasyonlar `prefers-reduced-motion` ayarına uyar.
  - Durumlar yalnızca renkle değil, metinle de gösterilir.

### 7.2 Sahne
- **Üst şerit:**
  - proje adı,
  - aşama şeridi: Ölçekleme → Plan → Uygulama → Son kontrol → Rapor. Mini ve küçük işte Plan yoktur; onay gerekiyorsa Plan'dan sonra "Onay bekliyor" gelir; düzeltme turunda "Düzeltme 1" gibi görünür.
  - geçen süre,
  - ilerleme yüzdesi (biten / toplam),
  - bağlantı göstergesi.
- **Ana sahne:** Sıcak, sevimli bir ofis. Önden ya da hafif izometrik görünüm.
  - Görünüm: krem duvarlar, açık meşe parke, saksı bitkiler, pencere.
  - Çay köşesi: ince belli çay bardakları ve demlik.
  - **Camlı planlama odası:** Opus'un yeri. Opus planı ve kartları burada yazar; kartlar yazıldıkça yandaki beyaz tahtaya yapışkan notlar eklenir. İş bitince notlar duvardaki kanban panosuna uçar. Jev Opus'a danışırken odanın kapısına gelir.
  - **Üç çalışma masası:** Sol, Sonnet, Luna. Her masada monitör, kupa ve isim levhası var. Sol son kontrolü kendi masasında yapar.
  - **Şefin masası:** Opus yalnızca eskalasyonda, yani Jev takılan bir kartı ona verdiğinde, planlama odasından kapıdan çıkıp buraya geçer; iş bitince odasına döner.
  - **Jev:** Masalar arasında dolaşır.
  - **Duvar panosu (kanban):** Sütunlar: Bekliyor, Sırada, Çalışıyor, Doğrulanıyor, Bitti, Sorunlu. Kartlarda görev kimliği, kısa başlık ve atanan ajanın minik avatarı bulunur.
- **Sağ panel:**
  - zaman damgalı, ikonlu olay akışı,
  - Jev'in karar kartları: görev, karar, hedef ajan, gerekçe, sapma düzeyi.
- **Ajan kartları şeridi.** Her kartta avatar, model etiketi, durum, kota göstergesi ve çağrı/token sayısı bulunur. Bir karta tıklayınca şunlar görünür:
  - üzerinde çalıştığı görev,
  - canlı log sonu,
  - önceki denemeler.

### 7.3 Karakterler

SVG ile çizilmiş, sevimli, büyük kafalı (chibi) karakterler. `characters.js` içinde parametrik fonksiyonlarla üretilirler: ten, saç, kıyafet ve aksesuar birer parametredir.
- Hepsinde aynı kalınlıkta dış çizgi, yumuşak gölge ve yuvarlak hatlar olsun.
- Boşta hafifçe nefes alıp versinler ve ara sıra rastgele göz kırpsınlar.
- Masaüstü görünümde boyları yaklaşık 120-160 piksel olsun.
- **Marka logosu kullanma.** Sağlayıcıyı küçük bir renk şeridi ve bir etiketle belirt, örneğin "GPT-6.1-Sol" ya da "Claude Sonnet 5.5".

| Ajan | Kılık | Aksesuar | Yeri |
|---|---|---|---|
| Opus | Proje şefi: yelek ve gömlek | Klipsli pano, yapışkan notlar, kalem | Camlı planlama odası; eskalasyonda şefin masası |
| Jev | Ustabaşı: turuncu reflektörlü yelek, iş kaskı | Telsiz, elinde görev kartları | Ofiste dolaşır |
| Sol | Kıdemli usta: sarı baret, iş tulumu | Alet kemeri, dizüstü bilgisayar | 1. masa |
| Sonnet | Usta: mavi iş tulumu | Kulaklık, kulağında kalem | 2. masa |
| Luna | Çırak: kasket | Sırt çantası, küçük alet çantası | 3. masa |

### 7.4 Durumlar ve animasyonlar

| Durum | Görsel | Konuşma balonu örneği |
|---|---|---|
| `idle` | Elinde çay bardağı, bardaktan buhar yükseliyor | "Çay molası ☕" |
| `thinking` | İçinde noktalar yanıp sönen bir düşünce balonu | "Düşünüyor…" |
| `reading` | Gözleri ekranda, sayfa ikonu | "src/app.py okuyor" |
| `editing` | Klavye tıkırtısı, ekrandan yükselen kod satırları | "src/auth.py düzenliyor" |
| `command` | Terminal ikonu | "pip install -r requirements.txt" |
| `testing` | Kontrol listesi / deney tüpü | "pytest çalıştırıyor" |
| `done` | Yeşil ✓ ve küçük konfeti | "T03 bitti!" |
| `failed` | Ter damlası, kırmızı ünlem | "Testler kaldı (3/12)" |
| `blocked` | El kaldırmış, "?" işareti | "Bilgi eksik: …" |
| `sleeping` (kota) | Masaya kapanmış, "Zzz", yanında geri sayım | "Kota doldu, 14:32'de uyanır" |
| `planning` (Opus) | Camlı odada proje çizimini açıp çiziyor; notlar tahtaya düşüyor | "Planı çiziyor" |
| `dispatch` (Jev) | Kartı panodan alır, masaya yürür, bırakır | "T04 → Sonnet" |
| `consulting` (Jev → Opus) | Jev planlama odasının kapısına yürür, ikisinin üstünde birer düşünce balonu belirir | "Jev, Opus'a danışıyor" |
| `verifying` (Jev) | Elinde kontrol listesiyle masanın başında | "T04 doğrulanıyor" |
| `reviewing` (Sol) | Büyüteçle inceliyor | "Son kontrolü yapıyor" |
| `guarded` | Kısa süreliğine kırmızı bir kalkan ikonu | "Koruma: proje dışına yazma engellendi" |

Balon metinleri 60 karakterde kısaltılır; tam metin ajan kartında görünür.

Animasyonlar bir kuyruğa alınır. Olaylar çok hızlı gelirse animasyonlar kısalır; arayüz asla geride kalmaz.

### 7.5 Sunucu, olaylar ve güvenlik

**Sunucu:**
- `http.server.ThreadingHTTPServer` kullanılır ve yalnızca `127.0.0.1`'e bağlanır.
- Arka planda (daemon) bir iş parçacığında çalışır; orkestratörü asla bloklamaz.
- Olay kanalı iş parçacıkları arasında güvenlidir. Her abonenin kendi kuyruğu vardır.

**Uç noktalar:**
- `GET /`: `index.html`
- `GET /static/...`: statik dosyalar
- `GET /api/durum`: anlık görüntü. İçinde koşu, aşama, görevler, ajan durumları, son N olay, rapor özeti ve `last_seq` bulunur.
- `GET /api/olaylar?from=<seq>`: SSE akışı (`text/event-stream`).
  - Her olayda `id: <seq>` gönderilir.
  - `Last-Event-ID` başlığı desteklenir.
  - 15 saniyede bir `: ping` gönderilir.
- `GET /api/rapor`: rapor markdown'ı ve JSON'u.
- `GET /api/ajan/<ad>/log?tail=200`: ajanın log sonu. `<ad>` yalnızca bilinen ajan adlarından biri olabilir; yol parametreleri asla doğrudan dosya yolu olarak kullanılmaz.
- `POST /api/duzelt {not}`, `POST /api/onayla`, `POST /api/devam`:
  - `X-Jev-Key` başlığında erişim anahtarı zorunludur.
  - `Origin` başlığı kontrol edilir.
  - İstek yalnızca uygun durumda kabul edilir; örneğin düzeltme yalnızca `reported` durumunda başlatılabilir.
  - Düğmeler çift tıklamaya karşı kilitlenir.

**Olay zarfı:**
```json
{"seq": 42, "ts": "2026-09-27T10:15:03Z", "run": "<run-id>", "type": "...", "data": {}}
```

**Olay türleri:**
- `run.phase` `{phase, round}`
- `task.update` `{task_id, status, agent, attempt}`
- `agent.state` `{agent, state, task_id, text, until}`. `state` değerleri §7.4'teki durumlardır; `until` kota bitiş zamanıdır.
- `agent.activity` `{agent, kind, text, path}`
- `jev.dispatch` `{task_id, agent, reason}`
- `jev.consult` `{task_id, brain}`
- `jev.decision` `{task_id, decision, agent, rationale, deviation}`
- `quota` `{agent, until}`
- `verify` `{task_id, command, passed}`
- `guard.blocked` `{agent, tool, reason}`
- `report.ready` `{round, verdict}`
- `log` `{level, text}`

**İstemci tarafı:**
- Sayfa açılınca önce `/api/durum` çağrılır, ardından `/api/olaylar?from=last_seq` akışına bağlanılır.
- Bağlantı koparsa üstel geri çekilmeyle yeniden bağlanılır.
- Sayfa yenilendiğinde durum kaybolmaz.

**Rapor ve kontroller:**
- Rapor görünümü Markdown'u HTML'e çevirir. Bunun için harici kütüphane kullanılmaz; yalnızca başlık, liste, tablo, kod, kalın ve italik destekleyen küçük bir dönüştürücü yazılır.
- Model çıktısını HTML'e koymadan **önce mutlaka kaçışla**. Bu sayfada kontrol düğmeleri olduğu için XSS'e karşı korunmalı.
- **Düzelt** düğmesinin yanında bir not kutusu bulunur.
- `awaiting_approval` durumunda plan görünümü açılır. Yanında **Planı onayla** ve **Reddet ve çık** düğmeleri olur.
- `paused` durumunda duraklama nedeni ve **Devam** düğmesi görünür. Kota beklemesi varsa sıfırlanma saati de gösterilir.
- `jev ofis` ile açılan sayfada da aynı düğmeler çalışır. Basılan düğme ilgili akışı o süreçte başlatır.

---

## 8. Rol promptu şablonları

Şablonlar `jev/prompts/*.md` dosyalarındadır; yetkili metin onlardır ve kullanıcı düzenleyebilir. `{{değişken}}` yer tutucularını orkestratör doldurur. `{{#ad}} … {{/ad}}` arasındaki bölümler yalnızca gerektiğinde eklenir, örneğin önceki denemeler yalnızca tekrar denemesinde. Promptun sonuna her zaman "ÇIKTI ŞEMASI:" başlığıyla şema eklenir. Aşağıda her şablonun işi ve uyması gereken kurallar özetlenir; şablonları iyileştirebilirsin ama bu anlamları koru.

### 8.1 `plan.md` (Opus: plan ve görev kartları, tek çağrı)
Girdi: istek, ortam, Jev'in ölçtüğü ölçek, proje klasörü (var olan projede dosya ağacı), en çok kart sayısı. Onarım turunda önceki hatalar ve önceki çıktı da eklenir; Opus doğru kısımları değiştirmeden çıktının tamamını yeniden verir.

Opus'a işçilerin yalnızca kendi kartını ve kartta adı geçen dosyaları göreceği, planı ve diğer kartları görmeyeceği söylenir. Opus dört şey yazar:
1. **İhtiyaçlar** (`needs`): internette araştırma, veri toplama, web sitesi inceleme, araç kurulumu, hazır kaynak. Ezberden doğru üretilemeyecek her bilgi için bir araştırma kartı açılır ve ihtiyaç o karta bağlanır. Kurulu bir araçla ya da hazır bir kaynakla karşılanan ihtiyacın kartı olmayabilir. Gerek yoksa liste boş kalır; gereksiz araştırma açılmaz.
2. **Plan:**
   - Belirsizlikler sorulmaz; makul varsayımlar `assumptions` alanına yazılır. Kapsam isteğe sadıktır.
   - Mimari basit ve test edilebilirdir; modül ve kart sayısı işin boyuyla orantılıdır.
   - Başarı ölçütleri ölçülebilirdir; mümkünse PowerShell'de çalışan ve kendiliğinden biten bir komutla doğrulanır. `commands.test` tüm testleri çalıştıran tek komuttur.
   - `plan_markdown` orta işte kısadır: "# <Proje adı>", "## Özet", "## Mimari ve dosyalar". Büyük işte tamdır: "## Amaç ve kapsam", "## Mimari", "## Kalite standartları" de eklenir. Modüller, ölçütler, sözleşmeler ve kartlar markdown'da tekrar yazılmaz; Jev onları JSON alanlarından `plan.md`'ye ekler.
   - İş, Jev'in ölçtüğünden belirgin biçimde büyükse `scale` alanına daha büyük seviye yazılır.
3. **Sözleşmeler** (`contracts`): bir kartın üretip başka bir kartın kullandığı her arayüz. Örnekler: fonksiyon imzaları, dışa aktarımlar, veri ve dosya biçimleri, komut satırı, HTML öğe kimlikleri, CSS sınıfları. Tanım, iki işçinin birbirini görmeden uyumlu iş çıkarabileceği kadar kesindir. Sözleşme her iki tarafın kartına bağlanır.
4. **Görev kartları** (`tasks`):
   - Her kart tek işçinin tek oturumluk işidir; birbirine sıkı bağlı küçük işler tek kartta toplanır.
   - `description` işçinin bilmesi gereken her şeyi içerir; "plana bak" denmez.
   - `reads` yalnızca gereken dosyaları, `outputs` yazılacak dosyaları gösterir.
   - `code` ve `test` kartlarında en az bir `verify` vardır. Testler ilgili kartın içinde yazılır; yalnızca doğrulama yapan kart açılmaz, çünkü doğrulamayı Jev yapar.
   - Araştırma kartı (`research`) kod yazmaz; bulguları kaynaklarıyla tek bir `.md` dosyasına yazar. Bu bilgiyi kullanan kartlar ona bağlanır ve dosyayı `reads` listesine koyar.
   - `suggested_agent` değerleri:
     - `sol`: ana kodlama ve karmaşık işler,
     - `sonnet`: testler, araştırma, orta kodlama,
     - `luna`: basit işler, belge, yapılandırma,
     - `null`: emin değilse.

Yeni projede Opus araçsız çalışır. Var olan projede klasörü salt okuma ile inceler ve yalnızca gereken dosyalara bakar.

### 8.2 `isci_gorev.md` (görev kartı) ve `isci_mini.md` (tek görevli iş)
Kart kendi kendine yeter. İşçi klasörü taramaz ve planı aramaz; kartta adı geçen dosyaları okuyup hemen işe koyulur.

Kartta şunlar bulunur:
- proje adı ve özeti, ortam, yığın ve test komutu, modül,
- görev tanımı ve notlar,
- okunacak ve yazılacak dosyalar,
- sözleşmelerin tanımı,
- bağlı olduğu kartlarda yapılanlar,
- karşıladığı başarı ölçütleri,
- kabul ölçütleri ve doğrulama komutları.

Gerektiğinde şunlar eklenir:
- **Araştırma kartı talimatı:** işçi internette arar ve her bilgiyi kaynağıyla yazar. Doğrulayamadığı bilgiyi işaretler. Kod yazmaz.
- önceki denemeler,
- Jev'in notu,
- paralel çalışma notu.

Kurallar:
- Yalnızca bu kartı yap. Var olan bir dosyayı değiştirmeden önce oku. Mevcut testleri bozma.
- Git durumunu değiştirme: commit, reset, checkout, switch, rebase, push, clean ve stash yasak; status, diff ve log serbest.
- Proje dışına, `.jev/` ve `.git/` içine yazma. Kimlik bilgisi dosyalarına dokunma.
- Kartın açık bıraktığı küçük kararları sen ver ve her birini "SEÇİM: <karar> — <neden>" satırıyla yaz.
- Kart yetmiyorsa uydurma: `status=blocked` de ve blocker alanına "takıldım: <eksik olan>" yaz.
- Doğrulama komutları geçince ek kontrol ya da iyileştirme yapmadan bitir.
- `summary` sonraki kartların bilmesi gerekeni içersin. Bir şey kurduysan "KURULUM: <araç> — <nasıl>" satırını ekle.

`isci_mini.md` mini ve küçük işin tek işçisi içindir. Bu işte plan ve kart yoktur: işçi isteği baştan sona yapar. Gerekirse internette kendisi araştırır; kaynaklarını ve "SEÇİM:" satırlarını yazar. Düzeltme turunda kullanıcının notu da bu prompta girer.

### 8.3 `jev_karar.md` (Jev'in beyni)
Girdi:
- plan özeti, ilgili modül ve başarı ölçütleri,
- görev ve sorun türü,
- deneme geçmişi,
- görev grafiğinin durumu ve bu göreve bağlı görevler,
- müsait ve soğumadaki ajanlar,
- kalan haklar,
- kayıt dosyalarının yolları.

Seçenekler §5.7'dekilerdir: retry, reassign, split, revise, skip, pause, abort ve rollback. Plan ve başarı ölçütleri değiştirilemez.

Kurallar:
- **reassign:** Opus yalnızca takılan işin ustasıdır (eskalasyon). Görev başka ajanlarda takıldıysa ya da gerçekten zor ve kritikse seçilir. Kotası plan ve beyin kararlarıyla ortak olduğu için daha ucuz bir ajan yetecekse o seçilir.
- **"takıldım: …":** İşçi böyle dediyse kart eksiktir. Aynı kartla retry edilmez; eksik bilgi ya da dosya karta eklenerek revise edilir.
- **Deneme hakkı:** Hak bittiyse yalnızca skip, pause ya da abort seçilebilir.

İlke: önce kayıtlardan kök nedeni anla, aynı hatayı tekrarlatma, guidance somut olsun.

### 8.4 `denetim.md` (Sol: son kontrol)
Sol kanıt paketine dayanır (§6). Paketteki doğrulama sonuçlarını Jev çalıştırmıştır ve bunlar esastır.
- Sol testleri ve uygulamayı kendisi çalıştırmaz, klasörü taramaz.
- Paket bir ölçütü değerlendirmeye yetmezse yalnızca o ölçüt için gereken birkaç dosyayı okur.
- Doğrulayamadığını `unverified` olarak işaretler.
- Görsel kaliteyi karelerden değerlendirir. Gerçek kaynak yerine elle, kaba ve yaklaşık üretilmiş içerik ilgili ölçütü en fazla `partial` yapar.
- Raporun uzunluğu işin boyuyla orantılıdır.

`report_markdown` başlıkları (Türkçe):
```text
# Son Kontrol Raporu — <Proje adı>
## Genel sonuç (başarılı / kısmen / başarısız + 3-5 cümle)
## Başarı ölçütleri (tablo: SC · durum · kanıt · eksik)
## Plandan sapmalar (Jev'in ve işçilerin kararları dahil)
## Açık sorunlar ve riskler
## Nasıl çalıştırılır (komutlar)
## Düzeltme önerileri (G1…; kullanıcı "düzelt" derse bunlar ele alınacak)
```
Düzeltme turunda önceki rapor da verilir ve neyin değiştiği belirtilir.

### 8.5 `duzelt.md` (Opus: düzeltme kartları)
Girdi:
- plan ve son rapor,
- eksikler ve karşılanmayan ölçütler,
- kullanıcının notu,
- mevcut kartlar ve durumları,
- git geçmişi.

Opus planın dışına çıkmadan yalnızca eksikleri kapatan kartlar yazar.
- Kartlar planlamadaki gibi kendi kendine yeter; işçi planı ve raporu görmez.
- Kimlikler `D<tur>-01`, `D<tur>-02` … biçimindedir.
- `notes` alanına kapatılan gap kimliği yazılır. Kullanıcının notundan çıkan kartta "not" yazılır.
- `depends_on` mevcut kartlara da bağlanabilir.
- `contracts` yalnızca plandaki sözleşmeleri gösterebilir.

---

## 9. Kilometre taşları

**M0: Keşif ve doğrulama.** Gerçek model çağrıları burada en az düzeyde tutulur.
- CLI yollarını bul. Sürümlerini ve `--help` çıktılarını kaydet.
- Ekipteki bütün modellere en düşük eforla "Yalnızca OK yaz" mesajı gönder. Böylece oturumların açık olduğunu ve model kimliklerinin doğru olduğunu doğrula. `claude-opus-5-5` başarısız olursa `opus` takma adını dene.
- Yapısal çıktıyı dene: küçük bir şemayla hem Codex'i (`--output-schema` + `-o`) hem Claude'u (`--json-schema`) çalıştır ve sonucun nerede geldiğini doğrula.
- Olay akışlarını yakala: Geçici bir klasörde küçük bir "dosya yaz" göreviyle Codex `--json` ve Claude `stream-json` olaylarını kaydet.
- Codex'te iki şeyi dene:
  - kullanıcı ayarlarıyla ve `--ignore-user-config` ile başlangıç sürelerini karşılaştır,
  - read-only sandbox'ın Windows'ta çalıştığını doğrula.
- Claude kancasının bypass modunda da çalıştığını zararsız bir testle doğrula.
- Codex'in `.rules` ve kanca desteğini araştır.
- Bulguları `docs/cli-notlari.md`'ye yaz; olay örnekleri ve görülen hata metinleri de buraya girsin. Tasarımı değiştirmen gerekirse `docs/tasarim.md`'yi güncelle.

**M1: İskelet.** Paket ve `pyproject.toml`, ayar yükleme ve doğrulama, CLI iskeleti, koşu klasörleri, `events.py`, `state.py` (atomik yazma, kilit), UTF-8 ayarları.

**M2: Adaptörler.** Codex, Claude ve sahte adaptör. Zaman aşımında süreç ağacını kapatma, şema doğrulama ve onarım, kota tespiti, etkinlik ayrıştırma (M0'daki örneklerle test edilir), kullanım istatistikleri.

**M3: Plan ve görev kartları.** Opus → `plan.md` / `plan.json` / `tasks.json` tek çağrıda. Kart doğrulaması ve onarım döngüsü, aynı dosyaya dokunan kartların sırası, `--onay`, `AGENTS.md` / `CLAUDE.md`, git hazırlığı.

**M4: Jev'in dağıtım katmanı.** Hazır görev seçimi, yönlendirme, görev paketi, doğrulama, commit, `progress.md`.

**M5: Jev'in beyni.** Sorunları sınıflandırma, kural katmanı, beyin kararlarının alınması ve uygulanması, sınırlar, `decisions.jsonl`. Rol denetimi testi de burada yazılır: bir ajan yalnızca rolünün izin verdiği aşamada çağrılır.

**M6: Son kontrol ve düzeltme turu.** Test sonuçlarını toplama, kanıt paketi ve görsel kareler, Sol'un denetimi, rapor, `jev duzelt` akışı.

**M7: Dayanıklılık.** Ctrl+C, `jev devam`, kota beklemesi ve duraklatma, artık çalışmayan sürece ait kilidi temizleme.

**M8: Terminal ve diğer komutlar.** `durum`, `rapor`, `ajanlar`, `gecmis` ve etkileşimli mod.

**M9: Güvenlik.** Guard modülü, Claude kancası, Codex kuralları (destek varsa), görev sonrası denetim ve bunların testleri.

**M10: Ofis arayüzü.** Sunucu, SSE, sahne, karakterler, animasyonlar, kanban, paneller, düğmeler. `--kuru` modunda canlı görünüm.

**M11: Uçtan uca doğrulama ve belgeler.** `--kuru` senaryoları, küçük bir gerçek koşu, Türkçe README. README bölümleri: kurulum, komutlar, ayarlar, güvenlik uyarısı (tam yetkinin riskleri ve koruma), kota ipuçları, sorun giderme.

**M12 (opsiyonel): Paralel mod.** Her görev ayrı bir git worktree'de çalışır. Aynı anda sağlayıcı başına en fazla 1 ajan çalışır. Birleştirmede çakışma olursa Jev'in beyni karar verir.

---

## 10. Kabul ölçütleri (bitti tanımı)

1. `pip install -e .` sonrasında `jev` komutu çalışıyor. PATH sorunu varsa README'de `py -m jev` alternatifi ve çözümü anlatılıyor.
2. `pytest` tamamen geçiyor. En az şunlar test edilmiş:
   - DAG doğrulaması,
   - yönlendirme,
   - durum geçişleri,
   - kota kalıpları ve sıfırlanma zamanının ayrıştırılması,
   - etkinlik ayrıştırma (M0 örnekleriyle),
   - git checkpoint ve geri alma,
   - kaldığı yerden devam,
   - guard kararları,
   - rol denetimi (bir ajan yalnızca rolünün izin verdiği aşamada çağrılır; eskalasyon yalnızca Jev'in kararıyla),
   - ayar doğrulaması (planlayıcı, denetçi ve beyin rolleri),
   - SSE'nin `from=seq` ile yeniden bağlanması,
   - rapor HTML'inin kaçışlanması.
3. `jev ajanlar --test` ekipteki dört ajanın hepsinden OK yanıtı alıyor ve CLI yollarını ve sürümlerini gösteriyor.
4. `jev --kuru "Basit bir yapılacaklar listesi CLI'ı tasarla"` gerçek model çağırmadan uçtan uca bitiyor: plan ve kartlar → uygulama → rapor. Uygulama sırasında:
   - en az bir yapay başarısızlık Jev'in beyninin kararıyla çözülüyor,
   - bir kota olayı başka ajana yönlendirilerek atlatılıyor,
   - bir bozuk JSON onarılıyor.

   Git dalında her görev için bir commit bulunuyor.
5. Aynı kuru koşu sırasında ofis sayfası kendiliğinden açılıyor ve:
   - karakterlerin durumu değişiyor, konuşma balonları eylemleri gösteriyor,
   - Jev kartları masalara taşıyor, Opus'a danışmaya gidiyor,
   - kanban panosu güncelleniyor,
   - sayfa yenilenince durum korunuyor,
   - rapor görünümündeki **Düzelt** düğmesi bir düzeltme turu başlatıyor ve yeni rapor üretiliyor.
6. Kuru koşunun ortasında Ctrl+C'ye basıldıktan sonra `jev devam`, tamamlanmış görevleri tekrar yapmadan sürdürüyor.
7. Guard, test amaçlı "tehlikeli" bir komutu (örneğin proje dışına yazma) Claude işçisinde engelliyor ve bu olay arayüzde görünüyor.
8. Küçük bir gerçek koşu, örneğin "Python için sıcaklık ve uzunluk birim dönüştürücü CLI tasarla", gerçek ajanlarla rapora kadar gidiyor. Kullanıcının kotasını korumak için bunu en sona bırak ve başlamadan önce kullanıcıya haber ver.
9. Türkçe karakterler (ı, ğ, ş, ç, ö, ü, İ) terminalde, dosyalarda, loglarda ve arayüzde bozulmuyor.
10. README Türkçe ve §9/M11'deki bölümleri içeriyor.

---

## 11. Çalışma kuralları

- **Kotayı koru.** Gerçek model çağrısını yalnızca şu durumlarda yap:
  - M0 denemeleri,
  - gerçekten gerekli küçük doğrulamalar,
  - kabul ölçütü 8.

  Geliştirme ve testlerin geri kalanında sahte adaptörü kullan.
- Bu belgede "doğrulandı" olarak geçmeyen hiçbir CLI bayrağını ya da olay adını, doğrulamadan koda yazma. M0'da doğrula ve sonucu `docs/cli-notlari.md`'ye yaz.
- Oturum açma işini kullanıcıya bırak. Giriş bilgisi dosyalarını asla okuma ya da yazdırma. Kullanıcının `~/.codex` ve `~/.claude` ayarlarını değiştirme; zorunlu kalırsan önce sor.
- Bu klasörün dışında yalnızca şunlara dokun:
  - M0 denemeleri için sistemin geçici klasörü,
  - kuru koşu testleri için geçici bir proje klasörü,
  - `%USERPROFILE%\.jev\`,
  - kabul ölçütü 8 için `proje_koku`.
- Basit tut. Bağımlılıklar yalnızca `rich`, `pyyaml`, `jsonschema`; testler için `pytest`. Arayüzde Node, npm ya da CDN kullanma.
- Kullanıcının gördüğü bütün metinler Türkçe olsun. Kod tanımlayıcıları, dosya adları ve durum anahtarları İngilizce olsun. Dosyaları BOM'suz UTF-8 olarak yaz.
- Her kilometre taşından sonra testleri çalıştır ve kısa bir ilerleme notu ver. Belgeden saptığın her yeri gerekçesiyle `docs/tasarim.md`'ye yaz.
- İş bitince kullanıcıya kısa bir özet ver. Özette şunlar olsun:
  - nasıl kurulacağı,
  - ilk kuru koşunun nasıl yapılacağı (arayüzle birlikte),
  - ilk gerçek koşu için bir öneri.
