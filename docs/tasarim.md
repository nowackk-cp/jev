# Jev: uygulama planı ve sapmalar

Kaynak belirtim: [`jev-sistem-promptu.md`](jev-sistem-promptu.md). Bu belge, uygulamanın o belgeden nerede ve neden ayrıldığını anlatır.

## Sapmalar

| Konu | Belirtim | Uygulama | Gerekçe |
|---|---|---|---|
| Bağımlılıklar | `rich`, `pyyaml`, `jsonschema`, `pytest` | **Hiçbiri.** Yalnızca Python standart kütüphanesi | Makinede dördü de kurulu değildi. Sıfır bağımlılık: kurulum adımı yok, internetsiz çalışır, Python güncellemelerinde kırılmaz. |
| Ayar dosyası | `jev.yaml` | `jev.toml` (`tomllib` ile okunur) | YAML okuyucu stdlib'de yok. TOML'da `null` olmadığı için "boş" değerler `""` ile yazılır. |
| Şema doğrulama | `jsonschema` | `jev/sema.py`: kullandığımız alt kümeyi (type, birleşik tipler, properties, required, additionalProperties, items, enum) doğrulayan küçük doğrulayıcı | Bağımlılık yok. Şemalar zaten katı modun küçük bir alt kümesi. |
| Terminal | `rich` | ANSI renkli satır tabanlı çıktı (`jev/terminal.py`) | Asıl görsel ofis arayüzünde. Windows'ta VT modu `SetConsoleMode` ile açılır. |
| Testler | `pytest` | `unittest` (stdlib). `pytest` kurulursa aynı testleri o da çalıştırır | Bağımlılık yok. Komut: `python -m unittest discover -s tests` |
| Sahte senaryo | `mock/senaryo_varsayilan.yaml` | `jev/mock/senaryo_varsayilan.json` | YAML yok. |
| `jev` komutu | `pip install -e .` | `pip install -e .` çalışır. Derleme arka ucu depodadır: `tools/paketle.py` (PEP 517 ve PEP 660). Hiçbir şey indirilmez. Ayrıca `kur.ps1` var: Python 3.11+'yı bulur, `pip install --no-index -e .` çalıştırır, `jev.exe`'yi bulup PATH'i denetler. | setuptools makinede yok; `pip install -e .` normalde onu indirir. Tek saf Python paketi için küçük bir arka uç yetiyor. Planlanan `jev.cmd` başlatıcısı gereksizleşti: pip gerçek bir `jev.exe` üretiyor. |
| Doğrulama komutları | `powershell -NoProfile -NonInteractive -Command "…; <komut>"` | Komut BOM'lu UTF-8 bir `.ps1` dosyasına yazılır ve `-File` ile çalıştırılır. Başarı ölçüsü betiğin sözdizim ağacından çıkarılır (bkz. `verify.py`). | `-Command` ile verilen Türkçe karakterler bozulabiliyor. PowerShell `if`/`foreach` gibi deyimlerden sonra `$?`'yi güncellemiyor; sonucu doğru okumak için son deyimin türüne bakmak gerekiyor. |
| Paralel mod | `paralel: 1` varsayılan; paralel mod isteğe bağlı (§9, M12) | Paralel mod var ve varsayılan: `paralel = 3`. Ölçek profili düşürür (mini ve küçük 1, orta 2). Her görev kendi git çalışma ağacında çalışır (`lanes.py`, `phases/parallel.py`); doğrulama ve birleştirme sırayla yapılır. | Birbirini beklemeyen görevlerin aynı anda çalışması orta ve büyük işi belirgin biçimde kısaltır; tek görevli işte bu tören gereksizdir. |
| Codex koruması | Kanca varsa kanca | Canlı akış izleme + süreç ağacını durdurma + görev sonrası denetim | M0'da Codex kancaları `~/.codex`'e dokunmadan tetiklenemedi (bkz. `cli-notlari.md`). |
| Kota ayrımı | Ajan başına soğuma | Ajan başına soğuma. Claude'un hesap genelindeki penceresi (`five_hour`, `seven_day`) dolunca Opus ve Sonnet birlikte soğur. `--kuru` koşuları ayrı bir kota defteri kullanır | Opus ile Sonnet aynı pencereyi paylaşır (1 Ekim 2026 ölçüm koşusunda görüldü); pencere dolunca diğerini denemek boşa çağrıdır. Sahte kota olayları gerçek soğumaları kirletmesin. |

## Kullanıcı isteğiyle yapılan değişiklikler

Bunlar sapma değildir: belirtim de buna göre güncellendi.

- **Güvenlik reddinde ret takası (2 Ekim 2026).** Opus bazı istekleri güvenlik filtresi yüzünden yanıtlamıyordu; görevlerde sorun yoktu ama koşu iki boşa denemeden sonra duruyordu. Kullanıcı sunulan seçeneklerden "ret gelince", "tam takas" ve "Sonnet kendi reddinde"yi seçti.
  - **Tanıma:** yeni hata türü `refusal`. Claude'da stream-json'daki `stop_reason: "refusal"`, ya da yapılandırılmış çıktı yokken ret iletisi (`[kota] ret_kaliplari`; CLI bazen başarı diye bitirir). Codex'te aynı kalıplar. Ret yeniden denenmez, şema onarımı istenmez.
  - **Takas (`jev/takas.py`):** planlayıcı (Opus) plan, düzeltme, beyin ya da eskalasyon çağrısında reddederse `state["takas"]` yazılır ve koşunun ayar nesnesine uygulanır (`Runner` açılışta yeniden uygular; `jev devam` aynı ayarla sürer). Ortak (`[jev] ret_takasi`, varsayılan Sol) planlayıcı ve beyin olur; Opus denetçi ve yedek beyin olur, eskalasyon rolünü bırakır. Yedek denetçi Sol olur. Sol'un plan eforu `xhigh`. Reddedilen çağrı aynı turda Sol'a gider.
  - **Son kontrol:** Opus reddederse beklenmez, Sol yapar (`review.refusal_reviewer`: takasın ortağı, yedek denetçi, denetçi). Kimse kalmazsa koşu duraklar.
  - **İşçi reddi:** deneme hakkından sayılmaz, beyin çağrılmaz, kart sıradaki işçiye geçer; reddeden `state["ret_veren"]` listesine girer ve o koşuda görev almaz (`routing.choose_agent`, beyne gösterilen ajanlar). Listeden düşen işçinin yerine diğer işçiler listenin sonuna eklenir. Bütün işçiler reddederse koşu duraklar.
  - Kuru koşu senaryosunda `"ret": {"plan": ["opus"], "worker": ["sonnet"]}` ile denenir (`tests/test_ret_takasi.py`).
- **Kimse kendi işini denetlemesin: yedek denetçi (1 Ekim 2026).** Sol hem ana kodlayıcı hem denetçi olunca büyük işte çoğu zaman kendi kodunu denetliyordu. Kullanıcı sunulan seçeneklerden "yalnızca büyük işte Opus denetlesin"i seçti; küçük ve orta işler değişmedi.
  - **Ölçüm:** Jev son kontrolden önce başlangıçtan HEAD'e görev commit'lerinde (`jev(T03): <başlık> [sol]`) ajan başına değişen satırları sayar (eklenen + silinen, `git log --numstat`; `review.authorship`). Kilit, küçültülmüş, veri ve ikili dosyalar, `AGENTS.md`/`CLAUDE.md` ve görev dışı commit'ler sayılmaz. Satırların yarısından fazlası denetçinindiyse son kontrolü yedek denetçi yapar. Her turda yeniden ölçülür.
  - **Ayar:** `[jev] yedek_denetci = { ajan = "opus", olcekler = ["buyuk"] }`; Opus'un denetim eforu `high`. `olcekler = []` kapatır, `["orta", "buyuk"]` orta işe de açar. Kapı yedek denetçiye rol aramadan son kontrol izni verir; kural 11'in tek istisnası budur.
  - **Yedek beklenmez:** Opus soğumadaysa son kontrol Sol'da kalır. Opus'un çağrısı yarıda kalırsa (kota, oturum, iki başarısız deneme) koşu duraklatılmaz, son kontrol aynı turda Sol'a döner (`call_fixed(optional=True)` → `Unavailable`). Seçim ve nedeni terminalde, ofiste ve rapor ekinde görünür.
  - **Kareler:** Claude CLI resim eki almaz; Opus kareleri prompttaki yollarından `Read` ile açar.
  - Kuru koşuda kodun çoğunu Sonnet yazar; orada son kontrolü yine Sol yapar.
- **Astra çıkarıldı; Opus planı ve görev kartlarını tek çağrıda yazar (1 Ekim 2026).** Kullanıcı, fiyatı yüzünden Astra'nın ekipten çıkarılmasını ve gereksiz token harcayan adımların kaldırılmasını istedi. İlke: işi bir kez düşünen yazar, işçi yalnızca kendi kartını okur.
  - **Opus** (`planlayici`, `beyin`, `eskalasyon`) orta ve büyük işte tek çağrıda şunları yazar: ihtiyaçlar (araştırma, veri, web sitesi, araç, hazır kaynak), kısa plan (büyükte tam plan), sözleşmeler ve görev kartları. Ayrı parçalama çağrısı yoktur (`phases/decompose.py` silindi; `decomposing` aşaması yalnızca eski koşular için tanınır; devam edilince plana döner). Yeni projede Opus araçsız çalışır ve klasör ağacı gönderilmez. Düzeltme turunun kartlarını da o yazar. Kodu yalnızca Jev'in kararıyla yazar (eskalasyon).
  - **Jev kartları kodla doğrular** (`dag.validate_tasks`: kimlik, bağımlılık ve döngü, modül, başarı ölçütü ve sözleşme kapsamı, doğrulama komutu, araştırma çıktısı). Hata varsa planlayıcıya hata listesi ve önceki çıktısı gider (`[sinirlar] onarim_denemesi = 2`). Aynı dosyayı yazan ya da başka kartın yazdığı dosyayı okuyan kartları Jev kendisi sıraya koyar (`dag.link_shared_files`); planlayıcıya geri dönülmez.
  - **Görev kartı kendi kendine yeter:** hedef, okunacak ve yazılacak dosyalar, ilgili sözleşmelerin tanımı (Jev plandan karta koyar), yalnızca doğrudan bağımlılıkların çıktıları, kabul ölçütleri ve doğrulama. İşçi planı açmaz, klasörü taramaz, bir dosyayı düzenlemeden önce okur, doğrulama geçince durur. Kart yetmezse `blocked` durumuyla "takıldım: …" döner ve kararı Jev verir.
  - **Araştırma kartı** (`type = "research"`) önce çalışır: bulgular kaynaklarıyla bir `.md` dosyasına yazılır, onu kullanan kartlar ona bağlanır ve dosyayı okur. İnternet izni yalnızca araştırma kartına ve tek görevli işin işçisine verilir.
  - **Sol 6.1** (`gpt-6.1-sol`; `isci`, `denetci`) ana kodlayıcıdır ve orta ve büyük işte son kontrolü yapar (büyük işte kodun çoğunu kendisi yazdıysa son kontrolü yedek denetçi Opus yapar; bkz. yukarıdaki madde). Denetçi klasörü taramaz: Jev ona kanıt paketi (doğrulama sonuçları, değişikliklerin metni) ve görsel sonucun karelerini verir (`[sinirlar] denetim_karesi = 6`; sayfa ekran görüntüsü, videodan anlar, PDF sayfaları). Kareler başsız tarayıcıyla, geçici profille ve kullanıcıya gösterilmeden çıkarılır.
  - **Küçük işte de son kontrolü Jev yapar;** denetçi çağrılmaz. Ayrı netleştirme adımı ve `[olcek] belirsizlik_esigi` kaldırıldı: plan varsayımlarını yazar, tek görevli işte işçi kendi seçimlerini "SEÇİM:" satırlarına yazar.
  - **Yönlendirme:** S Luna, Sonnet, Sol · M ve L Sol, Sonnet · test ve araştırma Sonnet, Sol · belge Luna, Sonnet · ayar Luna, Sol · eskalasyon Opus. Tek görevli işin zor havuzu Sol, Sonnet.
  - **Bedeli:** Sol hem ana kodlayıcı hem denetçidir (büyük işte kendi kodunu yedek denetçi Opus denetler). Sol'un kotası dolarsa kod kartları sıradaki işçiye geçer ama son kontrol Sol'u bekler ya da koşu duraklar (kural 11).
  - **Ofis:** Astra'dan boşalan camlı planlama odası Opus'un yeridir; tabelası camın üstündedir. Kartlar yandaki beyaz tahtaya düşer. Opus eskalasyonda kapıdan çıkıp "Şefin masası"na geçer, Jev ona danışırken kapıya gelir.
- **Opus da kod yazar (28 Eylül 2026; 1 Ekim 2026'da yerini yukarıdaki karar aldı).** İlk belirtimde Opus parçalayıcı, beyin ve yalnızca Jev'in kararıyla iş alan bir eskalasyon işçisiydi. Kullanıcı, Opus'un görevleri dağıtırken kendine de görev ayırmasını ve diğer ustalar gibi kod yazmasını istedi.
  - Ayar: `ajanlar.opus.roller = ["parcalayici", "beyin", "isci"]`, `yonlendirme.L = ["sol", "sonnet", "opus"]`, `yonlendirme.eskalasyon = []`.
  - Opus parçalarken ve düzeltme turunda `suggested_agent: "opus"` ile kendine görev ayırır. Jev bu görevi Opus'a verir (gerekçe: "Opus görevi kendine ayırdı"). Sağlayıcı dengesi bu görevi başkasına kaydırmaz. Opus soğumadaysa ya da bu görevde başarısız olduysa görevi sıradaki uygun ajan alır.
  - İşçi paketi Opus'a görevi kendisinin ayırdığını hatırlatır. Ofiste Opus kod yazarken beyaz tahtadan "Şefin masası"na geçer.
  - Kuru koşu senaryosunda uçtan uca test görevi (T06) Opus'undur.
  - Bedeli: Opus'un kotası parçalama, beyin kararları ve kodlama arasında paylaşılıyordu.

## Belirtime eklenen ayrıntılar

Belirtimin açık bıraktığı ya da hiç değinmediği yerlerde verilen kararlar.

- **Kurulum.** Python 3.11+ gerekir (`tomllib`). `kur.ps1` UTF-8 BOM'lu ve CRLF satır sonludur: PowerShell 5.1 BOM'suz betiği ANSI kod sayfasıyla okur ve Türkçe metni bozar. `.gitattributes` bu satır sonlarını git'te de korur.
  - `-YolaEkle`, `jev.exe`'nin klasörünü kullanıcı PATH'ine ekler. PATH kayıt defterinden ham hâliyle okunup yazılır: `%USERPROFILE%` gibi değişkenler açılmaz, değer türü (`REG_EXPAND_SZ`) korunur. Ardından açık programlara ortamın değiştiği duyurulur (`WM_SETTINGCHANGE`). `[Environment]::SetEnvironmentVariable` bu yüzden kullanılmadı: değeri düz metne çevirir, değişkenleri açar.
  - Düzenlenebilir kurulum depo kökünü `sys.path`'e eklemez; yalnızca `jev` paketini gösteren bir bulucu kurar. `tests/` ve `tools/` başka programlara görünmez.
- **CLI bulma ve paketli uygulamalar.** Claude masaüstü uygulaması Windows'ta paketli (MSIX) kurulur. Onun `%APPDATA%\Claude\claude-code\<sürüm>\claude.exe` dosyası aslında `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\…` altındadır. Eski yolu yalnızca uygulamanın içinden başlayan işlemler görür; kullanıcının kendi terminali (uygulamanın Terminal paneli dahil) göremez. `discovery.py` iki yere de bakar ve aynı sürümde gerçek yolu seçer: `jev` her terminalde Claude'u bulur, `jev ajanlar` her yerde çalışan yolu gösterir. `jev ajanlar --test`'teki giriş ipucu da CLI PATH'te değilse bu tam yolu yazar (`& "…\claude.exe" auth login`).
- **Ayar denetimi.** `jev.toml`'daki bilinmeyen bir anahtar hata verir; yazım hatası sessizce yok sayılmaz. Adları kullanıcıya bırakılan tablolar (`yonlendirme.tur`, `ajanlar.*.efor`) bu denetimin dışındadır.
- **Proje klasörü adı.** Slug 40 karakteri aşarsa kelimenin ortasından değil, kelime sınırından kesilir.
- **Rol denetimi birkaç katmanda.** Çağrı kapısı (`gateway.py`) rolü ve aşamayı denetler: plan ve düzeltme planlayıcının, son kontrol denetçinin (ya da ayardaki yedek denetçinin), beyin çağrısı beynin (ya da yedek beynin) işidir; işçi çağrısı işçi rolü ya da Jev'in eskalasyon kararını ister. Beyin kararı işçi ya da eskalasyon rolü olmayan bir ajana görev veremez (`apply_decision`). Yönlendirme, durum dosyasında zorlanmış ajan işçi değilse onu yok sayar ve seçimi kurala bırakır.
- **`jev duzelt [koşu] [not]`.** İlk sözcük 8 rakamla başlıyorsa ya da bilinen bir koşunun kimliğiyse koşu kimliği sayılır. Değilse notun parçasıdır; tırnaksız yazılan not da çalışır.
- **Rapor eki.** Jev, son kontrol raporunun sonuna kendi kayıtlarını ekler: dal, commit sayısı, kararlar, koruma engellemeleri, plandan sapmalar. Dalı birleştirme komutları proje klasörünün tam yoluyla başlar (`cd "<proje>"`); rapor başka bir klasörden okunsa da komutlar çalışır.
- **Ofis sunucusu.**
  - `Referrer-Policy: no-referrer` gönderilir; adresteki anahtar başka sitelere sızmaz. Anlık görüntüde çağrı dosyası yolları, ham çıktı, prompt ve anahtar bulunmaz.
  - POST isteğinde gövde, yetki denetiminden önce okunur (en çok 64 KB, 10 sn zaman aşımı; daha büyükse 413). Windows'ta okunmamış gövdeyle kapanan bağlantıda tarayıcı, hata yanıtını göremeden "bağlantı sıfırlandı" alabiliyordu.
  - Sunucu kapanırken SSE akışı kuyrukta kalan olayları (rapor, son aşama) gönderir, sonra `kapandi` olayıyla istemciye yeniden bağlanmamasını söyler.
- **Olay günlükleri.** JSONL dosyasına yazan süreç bir satırın ortasında ölürse yarım satır sonraki açılışta kapatılır; yeni kayıt ona yapışıp kaybolmaz. Okuyucu bozuk satırı atlar.

## Varsayımlar

- Tek bilgisayar, tek kullanıcı. Ofis sunucusu yalnızca `127.0.0.1`'e bağlanır.
- Hedef projeler Windows PowerShell 5.1'de çalışan komutlar kullanır. Doğrulama komutunun başarı ölçüsü:
  - `exit N` çağrılırsa N;
  - son deyim bir komut hattıysa onun `$?` değeri (yerel programlarda çıkış kodu);
  - son deyim `if`/`foreach` gibi bir denetim deyimiyse başarılı;
  - çıktının son satırı tam olarak `False` ise başarısız (ör. tek başına `Test-Path yok.txt`).
- Git'te genel kullanıcı adı/e-posta tanımlı olmayabilir. Jev, depoda tanımlı değilse commit'lere `-c user.name=Jev -c user.email=jev@localhost` ekler; genel ayarlara dokunmaz.
- Plan `docs/` ya da proje köküne kopyalanmaz; `.jev/runs/<id>/plan.md` dosyasındadır. İşçi planı görmez: kartında ihtiyacı olan her şey (sözleşmelerin tanımı dahil) vardır.
- Kod dışı görevlerde (docs/research) değişiklik yoksa "her görev için bir commit" kuralı için boş commit (`--allow-empty`) atılır.
- `jev ofis` ile açılan eski bir koşuda Düzelt düğmesine basılırsa düzeltme turu o süreçte başlar.

## Modül haritası

```
jev/
  __main__.py      python -m jev
  cli.py           komut ayrıştırma, etkileşimli mod
  terminal.py      ANSI çıktı
  config.py        varsayılan + kullanıcı ayarı, doğrulama (roller: planlayıcı, denetçi, beyin, eskalasyon)
  varsayilan.toml  varsayılan ayarlar
  util.py          zaman, atomik yazma, slug, metin kısaltma
  sema.py          JSON şema doğrulayıcı (alt küme) + JSON ayıklama
  events.py        olay kanalı (events.jsonl + abonelere yayın)
  state.py         koşu durumu, kilit, koşu dizini
  discovery.py     CLI yürütülebilirlerini bulma (paketli uygulamaların gerçek klasörleri dahil)
  quota.py         kota kalıpları, sıfırlanma zamanı, soğuma defteri
  machine.py       makinenin ortamı: kurulu araçlar, Python paketleri, ortak kaynaklar
  systemone.py     Jev modeli (TypeSafe System One): ölçek, dağıtım, kabul ve karar soruları
  guard.py         koruma kuralları + Claude PreToolUse kancası
  kanca.py         kancanın başlatıcısı (Claude onu dosya yoluyla çalıştırır)
  gitops.py        git yardımcıları
  verify.py        doğrulama komutları
  context.py       plan, görev kartı ve denetim promptları, AGENTS.md/CLAUDE.md, progress.md
  dag.py           görev grafiği: kart doğrulaması, aynı dosyaya dokunan kartların sırası
  lanes.py         paralel görev şeritleri (git çalışma ağaçları)
  routing.py       ajan seçimi, görev sırası
  gateway.py       tek çağrı kapısı: rol/aşama denetimi (eskalasyon dahil), şema onarımı, kota ve kullanım kaydı
  adapters/        base.py (süreç), codex.py, claude.py, mock.py
  phases/          common.py, sizing.py, plan.py, execute.py, parallel.py, brain.py, review.py, fix.py
  runner.py        koşu yaşam döngüsü, devam, kontrol komutları
  office/          server.py, snapshot.py, md.py (güvenli markdown → HTML), static/
  prompts/         rol promptu şablonları
  schemas/         yapısal çıktı şemaları
  mock/            kuru koşu senaryosu ve sahte ajanların yazdığı dosya şablonları
tools/
  paketle.py       kurulum arka ucu: tekerlek, düzenlenebilir kurulum, sdist
  sema_uret.py     jev/schemas/*.schema.json dosyalarını üretir
  senaryo_uret.py  jev/mock/senaryo_varsayilan.json dosyasını üretir
tests/             unittest; _ortak.py testleri kullanıcının ~/.jev ve git ayarlarından yalıtır
kur.ps1            kurulum betiği (hiçbir şey indirmez)
```
