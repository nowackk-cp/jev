# Jev — çok ajanlı yazılım ofisi

Jev, "X için Y tasarla" diye verdiğin bir yazılım isteğini dört yapay zekâ ajanından oluşan bir ekibe yaptırır. Ajanlar bilgisayarındaki **Codex CLI** (ChatGPT Plus aboneliği) ve **Claude Code** (Claude Pro aboneliği) üzerinden çalışır; ajanlar için API anahtarı gerekmez. Jev'in kendi kararlarını ise TypeSafe'in **Jev modeli** verir; bunun için bir TypeSafe anahtarı gerekir. Anahtar yoksa kararları Opus verir (bkz. [Jev modeli](#3-jev-modeli-typesafe)). Olan biteni tarayıcıda canlı bir ofis olarak izlersin: her ajan bir işçi kılığında masasında çalışır, görevler panoda ilerler, Jev'in kararları akışta görünür.

```powershell
jev "Python için sıcaklık ve uzunluk birim dönüştürücü CLI tasarla"
```

Jev işin boyunu kendisi ölçer ve töreni ona göre kurar. "Çok basit bir hesap makinesi" isteğinde plan ve denetçi yoktur: tek bir işçi düşük eforla bir iki dakikada bitirir. Kurumsal bir işte ise derin plan, senin onayın ve yüksek efor devreye girer (bkz. [Ölçekler](#ölçekler)).

> **Uyarı:** Ajanlar **tam yetkiyle** çalışır: onay sormadan komut çalıştırır, dosya yazar ve siler. Jev'in felaket koruması en tehlikeli işleri engeller ama bir güvenlik duvarı değildir. İlk kullanımdan önce [Güvenlik uyarısı](#güvenlik-uyarısı) bölümünü oku.

Sürüm 0.1.0 · Windows 10/11 · Python 3.11 ve üstü (3.13 ile test edildi) · Jev'in kendi bağımlılığı yok

**İçindekiler:** [Ekip](#ekip) · [Nasıl çalışır](#nasıl-çalışır) · [Ölçekler](#ölçekler) · [Araçlar ve kaynaklar](#araçlar-ve-kaynaklar) · [Kurulum](#kurulum) · [Komutlar](#komutlar) · [Ofis arayüzü](#ofis-arayüzü) · [Proje klasörü ve git](#proje-klasörü-ve-git) · [Ayarlar](#ayarlar) · [Güvenlik uyarısı](#güvenlik-uyarısı) · [Kota ipuçları](#kota-ipuçları) · [Sorun giderme](#sorun-giderme) · [Koşu dosyaları](#koşu-dosyaları) · [Geliştirme](#geliştirme)

## Ekip

| Ajan | Model | Çalıştığı yer | Ofisteki kılığı | Ne yapar |
|---|---|---|---|---|
| **Opus** | Claude Opus 5.5 | Claude Code | Proje şefi | Orta ve büyük işte isteği inceler ve tek çağrıda ihtiyaçları, kısa planı, sözleşmeleri ve görev kartlarını yazar. Düzeltme turunda eksikleri düzeltme kartlarına çevirir. Jev'in beynidir: Jev bölme ya da düzeltme kararı verince yeni görev metnini yazar, Jev emin olmadığında son kararı verir. İşçi olarak yalnızca zor mantık gerektiren büyük (L) kartları alır; kolay kartları almaz. Başka bir görev takılınca Jev'in kararıyla onu da yazabilir (eskalasyon); o zaman ofiste "Şefin masası"na geçer. Büyük işte kodun çoğunu Sol yazdıysa son kontrolü o yapar: kimse kendi işini denetlemez. |
| **Jev** | [TypeSafe Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (`jev-latest`) | Bu program (Python) + TypeSafe API | Ustabaşı | İşin boyunu ve zorluğunu ölçer. Görev kartlarını kodla doğrular, aynı dosyaya dokunan kartları sıraya koyar. Kartları ajanlara dağıtır, her görevi doğrular ve commit'ler. Sorunlarda kendisi karar verir. Mini ve küçük işte son kontrolü ve raporu kendisi yapar. |
| **Sol** | GPT-6.1-Sol | Codex CLI | Kıdemli usta | Ana kodlama: orta ve büyük kod kartlarının ilk adayı. Orta ve büyük işte son kontrolü yapar ve raporu yazar; büyük işte kodun çoğunu kendisi yazdıysa bu işi Opus'a bırakır. Opus'a ulaşılamazsa Jev'in yedek beyni olur (efor xhigh). |
| **Sonnet** | Claude Sonnet 5.5 | Claude Code | Usta | Her boydan görev alır; test ve araştırma kartlarının ilk adayı. |
| **Luna** | GPT-6-Luna | Codex CLI | Çırak | Basit işler: küçük görevler, belge ve ayar işleri. |

## Nasıl çalışır

1. **Hazırlık.** Jev proje klasörünü hazırlar ve işi ayrı bir dalda (`jev/<koşu-kimliği>`) yapar. Senin dalına dokunmaz.
2. **Ölçek — Jev.** Jev işin boyunu (mini, küçük, orta, büyük) ve zorluğunu (kolay, orta, zor) ölçer ve kararını duyurur. Boy, işin törenini belirler: plan ve görev kartı yazılıp yazılmayacağını, en çok kaç kart yazılacağını, son kontrolü kimin yapacağını. Zorluk, ajanı ve eforu belirler. Belirsizlikler ayrıca sorulmaz: plan makul varsayımlar yapar ve onları yazar. **Mini ve küçük işte 3. ve 4. adımlar atlanır:** işi tek bir işçi yapar, gerekirse internette kendisi araştırır (bkz. [Ölçekler](#ölçekler)).
3. **Plan ve görev kartları — Opus.** Tek çağrıda isteği inceler ve şunları yazar: işin ihtiyaçları (internette araştırma, veri toplama, web sitesi inceleme, araç ya da hazır kaynak), kısa plan (başarı ölçütleri ve test komutuyla), kartlar arasındaki sözleşmeler (fonksiyon imzaları, veri biçimleri) ve görev kartları. Yeni projede klasör taranmaz. Büyük işte plan senin onayını bekler (ofisteki **Planı onayla** düğmesi ya da terminalde `onayla`); orta işte yalnızca `--onay` verdiysen bekler.
4. **Kartların denetimi — Jev.** Jev kartları kodla doğrular: kimlikler, bağımlılıklar ve döngü, sözleşmeler, doğrulama komutları, her başarı ölçütünün ve modülün bir kartla karşılanması. Hata varsa Opus'a hata listesini ve kendi önceki çıktısını geri gönderir (en çok 2 onarım). Aynı dosyayı yazan ya da başka kartın yazdığı dosyayı okuyan kartları kendisi sıraya koyar. Her kart işçinin işe hemen koyulacağı kadar eksiksizdir: hedef, okunacak ve yazılacak dosyalar, ilgili sözleşmelerin tanımı, bağlı olduğu kartların çıktıları, kabul ölçütleri ve doğrulama komutları. İşçi planı ve klasörün geri kalanını görmez, aramaz; kart yetmezse "takıldım" diyerek durur. Araştırma kartları önce çalışır: işçi internette arar ve bulguları kaynaklarıyla bir dosyaya yazar, o bilgiyi kullanan kartlar bu dosyayı okur.
5. **Uygulama — Jev ve ajanlar.** Jev her görevi uygun ajana verir. Görev bitince görevin kendi kontrollerini ve planın test komutunu çalıştırır; tek görevli işte işçinin kendi kontrollerini yeniden çalıştırır. Geçen görev, koşu dalında tek bir commit olur: `jev(T03): <başlık> [sonnet]`. Birbirini beklemeyen görevler aynı anda, her biri kendi çalışma kopyasında çalışır: her ajan aynı anda 2 görev alır, orta ve büyük işte toplam en çok 8 görev (bkz. [Proje klasörü ve git](#proje-klasörü-ve-git)).
6. **Sorun çıkarsa Jev karar verir.** Test geçmezse, ajan hata verirse ya da koruma bir şeyi engellerse Jev modeli şu kararlardan birini seçer: *tekrar dene, başka ajana ver, böl, görevi düzelt, atla, duraklat, iptal et*. Kök nedeni, ajanı, eforu ve değişikliklerin geri alınıp alınmayacağını da o belirler. Takılan bir görevi Opus'a da verebilir (eskalasyon); Opus kendiliğinden yalnızca L kartları alır. Bölme ve düzeltme yeni görev metni gerektirir; Jev metin yazmadığı için bu metni Jev'in beyni (Opus; ulaşılamazsa Sol) yazar. Jev emin değilse (güven eşiğin altındaysa) ya da modele ulaşılamıyorsa son kararı beyin verir. Her karar gerekçesiyle ofiste görünür.
7. **Son kontrol ve rapor.** Görevler bitince Jev bütün doğrulama komutlarını çalıştırır; kodu o zamandan beri değişmeyen bir komutun görevdeki sonucu yeniden kullanılır. Orta ve büyük işte Sol projeyi başarı ölçütlerine göre denetler ve raporu yazar. Büyük işte kodun yarısından fazlasını Sol yazdıysa bu işi Opus yapar; Opus soğumadaysa beklenmez, Sol yapar. Klasörü taramaz: Jev ona bir kanıt paketi verir (doğrulama sonuçları, değişikliklerin metni) ve sonuç görselse karelerini ekler (sayfanın ekran görüntüsü, videodan anlar, PDF sayfaları). Mini ve küçük işte denetçi çağrılmaz: raporu Jev kendisi yazar. Sonuç bir HTML sayfası, video, görsel ya da PDF ise kendiliğinden açılır; programlar ve betikler başlatılmaz, nasıl çalıştırılacakları raporda yazar. **Akış burada durur.**
8. **Düzeltme — yalnızca sen istersen.** Raporu okuduktan sonra `jev duzelt`, terminalde `düzelt [not]` ya da ofisteki **Düzelt** düğmesiyle yeni bir tur açarsın. Opus rapordaki eksikleri ve notunu düzeltme kartlarına çevirir (`D1-01`, `D1-02`…), ajanlar düzeltir, son kontrol yeniden yapılır ve ikinci rapor yazılır. Mini ve küçük işte Opus çağrılmaz: notun tek bir düzeltme görevine dönüşür. Akış yine durur.

Jev hiçbir zaman push ya da rebase yapmaz ve senin dalına hiçbir şey birleştirmez. Tek birleştirdiği, aynı anda çalışan görevlerin kendi dallarıdır; onları kendi koşu dalına ekler. Koşu dalını birleştirmek sana kalır; gereken komutlar raporun sonunda yazar.

## Ölçekler

İlke: en ucuz ve en hızlı yoldan kusursuz sonuç. Tören işin boyuyla orantılıdır. Basit bir iş saniyeler ya da bir iki dakika içinde, düşük eforla biter; kurumsal bir iş derin planla ve yüksek eforla yapılır.

Jev her koşunun başında iki şeyi ölçer. **Boy** işin törenini belirler: plan ve görev kartları, son kontrol ve deneme hakkı. **Zorluk** (kolay, orta, zor) işi hangi ajanın hangi eforla yapacağını belirler.

| | Mini | Küçük | Orta | Büyük |
|---|---|---|---|---|
| Örnek | çok basit bir hesap makinesi, tek bir betik | yapılacaklar uygulaması, küçük bir CLI | veritabanlı küçük bir web uygulaması | kurumsal ya da üretim düzeyinde yazılım |
| Plan ve görev kartları (Opus) | yok, tek görev | yok, tek görev | kısa plan, en çok 12 kart (high) | tam plan, en çok 40 kart (xhigh), **onayına sunulur** |
| İşçi eforu S / M / L | low / medium / high | low / medium / high | low / medium / high | ajan tablosu |
| Son kontrol | Jev | Jev | Sol (medium) | Sol (high)\* |
| Aynı anda görev | 1 | 1 | en çok 8 | en çok 8 |
| Görev başına deneme | 2, sonra küçüğe yükselir | 4 | 4 | 4 |
| Doğrulama komutu süre sınırı | 2 dk | 5 dk | 10 dk | 10 dk |
| Hedef süre | 30 sn – 2 dk | 3–6 dk | 10–20 dk | gerektiği kadar |

\* Yedek denetçi: kodun yarısından fazlasını Sol yazdıysa son kontrolü Opus (high) yapar, kimse kendi işini denetlemez. Opus soğumadaysa Sol yapar. Kod payı görev commit'lerindeki değişen satırlardan sayılır; kilit, veri ve ikili dosyalar sayılmaz.

**İşçi seçimi.** Tek görevli işte (mini ve küçük) aday işçiler zorluğa göre belirlenir; aralarından Jev seçer, soğumadaki ajan atlanır:

- **Kolay:** Sonnet ya da Luna
- **Orta:** Sonnet ya da Sol
- **Zor:** Sol ya da Sonnet

Orta ve büyük işte kartlar türlerine ve boylarına göre `[yonlendirme]` listelerinden dağıtılır; planın önerdiği ajan öne alınır. Varsayılanda büyük ve zor (L) kod kartlarında önce Opus, orta (M) kod kartlarında önce Sol, basit (S) kartlarda, belge ve ayar kartlarında önce Luna, test ve araştırma kartlarında önce Sonnet gelir. Opus yalnızca L kartları alır (`ajanlar.opus.gorev_boyu`); daha küçük bir kartı yalnızca Jev'in kararıyla alır.

**Efor tavanı.** Ajanların efor tabloları (`[ajanlar.<ad>] efor`) büyük ölçeğin eforlarıdır. Küçük ölçekler bir tavan koyar ve gerçek efor ikisinden düşük olanıdır. Tavan yalnızca ilk denemeyi bağlar: sorun çıkınca Jev'in kararıyla yükseltilen efor tavanı aşabilir.

**Karar sırası.** İlk sonuç veren adım kazanır:

1. `--olcek mini|küçük|orta|büyük` bayrağı.
2. İstekteki açık ifade: "çok basit", "en basit", "sadece test için", "test amaçlı", "deneme amaçlı" ya da "tek dosyalık" mini seçtirir; "kurumsal", "şirket için", "production", "canlı ortam" ya da "enterprise" büyük seçtirir. Listeler `[olcek] ifade_mini` ve `ifade_buyuk` ayarlarındadır. İstekte iki yönden de ifade varsa karar modele kalır.
3. Jev modeli. Her seviye için bir olasılık verir. Jev, işin ondan büyük çıkma olasılığı düşük olan en küçük seviyeyi seçer (`emin_olma = 0.75`), yani en ucuzdan başlar.
4. Jev modeline ulaşılamazsa kelime kuralı. Hiçbir ipucu yoksa yeni projede küçük, mevcut projede orta seçilir.

Karar sorulmaz, duyurulur. Örnek: `Jev: iş boyu MİNİ (istekteki ifade) · zorluk Kolay → plan yok; işi Sonnet ya da Luna tek başına yapar, denetimi Jev yapar`. Başka bir ölçek istiyorsan koşuyu `--olcek` ile başlat.

**Mini ve küçük akış.** Plan, görev kartları ve denetçi yoktur.

1. Tek bir işçi işi yapar, en az bir davranış kontrolü yazar ve çalıştırır. Ezberden doğru üretilemeyecek bilgi gerekiyorsa internette kendisi araştırır; kaynaklarını ve verdiği kararları notuna yazar.
2. Jev bu kontrolleri proje kökünde yeniden çalıştırır. Pencere ya da sunucu açan ve indirileni çalıştıran komutlar alınmaz.
3. Jev modeline sorar: "İş, isteği herkesin beklediği alışılmış biçimde, eksiksiz ve özenle karşılıyor mu?" Model güvenle "hayır" derse aynı ajan değişiklikleri koruyarak bir kez daha dener. Örneğin hesap makinesi tuş takımı yerine bir form olarak çıktıysa ya da harita gerçek veri yerine elle çizildiyse bu adımda yakalanır (bkz. [Araçlar ve kaynaklar](#araçlar-ve-kaynaklar)).
4. Raporu Jev yazar.

**Kendiliğinden yükseltme.** Mini iş iki denemede olmazsa yarım iş `jev/<koşu-kimliği>-mini-deneme` dalına kaydedilip geri alınır. İş küçük ölçekte, bir üst zorlukla (daha güçlü işçi havuzuyla) baştan başlar. Orta bir işte Opus planda daha büyük bir ölçek önerirse kalan adımlar (eforlar, son kontrol, onay) o ölçekle çalışır. Ölçek hiçbir zaman küçültülmez.

**Alışılmış biçim.** Jev kodla üretilebilen her şeyi yapar. İstekte biçim belirtilmemişse işin herkesin beklediği biçimi seçilir:

| İş | Biçim |
|---|---|
| Arayüzlü araç | Tek bir HTML sayfası. Mantık ayrı bir `.js` dosyasında durur ve Node ile test edilir. |
| Video | Videoyu üreten betik ve `.mp4` dosyası |
| Görsel | `.png` ya da `.svg` |
| Belge | `.md` |
| Arayüzsüz iş | Python betiği |

**Sonucu açma.** İş bitince sonuç bir HTML sayfası, video, görsel ya da PDF ise kendiliğinden açılır; `[arayuz] sonucu_ac = false` bunu kapatır. Programlar ve betikler asla başlatılmaz; nasıl çalıştırılacakları raporda yazar. Kuru koşuda hiçbir şey açılmaz.

**Öğrenme.** Jev ölçek kararlarını ve sonuçlarını (başarılı mı, yükseltildi mi) `%USERPROFILE%\.jev\olcek_gecmisi.jsonl` dosyasına yazar. Sonraki ölçümlerde benzer geçmiş işleri örnek olarak kullanır. `[olcek] ogrenme = false` bunu kapatır.

`jev ajanlar` profil tablosunu gösterir. `jev durum` ve ofis koşunun ölçeğini gösterir. Profiller `[olcek.mini]`, `[olcek.kucuk]`, `[olcek.orta]` ve `[olcek.buyuk]` tablolarındadır; `jev.toml` dosyasından değiştirilebilir (bkz. [Ayarlar](#ayarlar)).

## Araçlar ve kaynaklar

Jev yalnızca uygulama yazmaz; video, görsel, harita ve belge de üretir. İşçilerin bunları iyi yapabilmesi için bilgisayarda neyin kurulu olduğunu ve hazır kaynakların nerede durduğunu bilmesi gerekir. Jev bu bilgiyi plan ve işçi promptlarına kendisi ekler; senin bir şey yazman gerekmez.

**Ortam taraması.** Jev makineyi tarar ve şunları listeler:

- Windows sürümü, kabuk, Python, Node, Git, ffmpeg ve winget
- Kurulu araçlar ve sürümleri: ImageMagick, pandoc, typst, Graphviz, LaTeX (MiKTeX ya da TeX Live), Inkscape, Blender, LibreOffice ve Google Chrome. PATH'te olmayan araç tam yoluyla verilir.
- Kurulu olmayan araçlar
- Kurulu Python paketleri: numpy, matplotlib, moviepy, manim, geopandas, python-docx, playwright ve benzerleri

Tarama sırasında pencereli programlar (Inkscape, Blender, LibreOffice, Chrome) çalıştırılmaz; sürümleri dosyanın sürüm bilgisinden okunur. Graphviz'in klasörü PATH'te değilse yalnızca Jev'in kendi süreci için PATH'e eklenir; kalıcı PATH'e dokunulmaz. Tarama bir saniyeden kısa sürer ve sonucu saklanır. Jev taramayı iki durumda yineler: PATH değişince (ör. bir işçi winget ile araç kurunca) ve PATH dışına kurulan bir program için en geç bir dakika sonra.

**Ortak kaynaklar.** `%USERPROFILE%\.jev\kaynaklar\` klasörü bütün projelerin kullandığı hazır kaynakları tutar:

| Klasör | İçerik |
|---|---|
| `harita\` | Natural Earth ve tarihî sınır verisi (Cliopatria) |
| `fontlar\` | Türkçe harfleri destekleyen açık lisanslı yazı tipleri |
| `npm\` | Remotion ve hazır tarayıcısı, harita, grafik ve belge paketleri, ikon ve bayrak setleri |

Neyin nerede durduğu, lisansları, araçların kullanım tarifleri ve bilinen sorunları `KATALOG.md` dosyasında yazar. Katalog varsa Jev onun yolunu ve başlıklarını promptlara ekler. İşçiler önce kataloğa bakar; internetten yalnızca orada olmayanı indirir. Klasör işçiler için salt okunurdur: işçi kullandığı dosyayı projeye kopyalar, böylece sonuç ne ortak klasöre ne internete bağlı kalır.

**KALİTE kuralı.** Plan ve işçi promptları bir KALİTE kuralı içerir: sonuç, alanının iyi bir uzmanının elinden çıkmış gibi olmalı. Kural işin boyuyla orantılı uygulanır; basit bir hesap makinesi dış kaynak istemez. Kural şunları ister:

- Ezberden doğru üretilemeyecek şey elle uydurulmaz. Harita gerçek coğrafi veriden ve tutarlı bir izdüşümle çizilir; elle çizilmiş kıta ya da sınır kabul edilmez. İkon ve yazı tipi için açık lisanslı setler, grafik, animasyon ve PDF için olgun kütüphaneler kullanılır.
- Kullanılan veri ve varlıklar projeye kopyalanır. Kaynakları ve lisansları `KAYNAKLAR.md` dosyasına yazılır.
- İşçi görsel çıktıyı kendisi gözden geçirir: videodan birkaç kareyi, sayfanın ekran görüntüsünü ya da PDF sayfalarını PNG'ye çıkarıp bakar, kusurları düzeltir.
- Kaynağa erişilemiyorsa sessizce kaba bir taklit üretilmez; durum işçinin notuna yazılır.

Opus planda işin ihtiyaçlarını yazar: ezberden doğru üretilemeyecek her bilgi için bir araştırma kartı açar, hazır kaynakla karşılanacak ihtiyacı ilgili karta yazar. Veri hazırlığını ve görsel öz kontrolü ilgili kartın açıklamasına ve kabul ölçütlerine koyar.

**Kalite denetimi.** Tek görevli işte (mini ve küçük) Jev'in kabul sorusu kaliteyi de sorar. Gerçek veri yerine kaba bir taklit (ör. elle çizilmiş harita) bulunursa aynı ajan işi bir kez daha, bu kez gerçek kaynakla yapar. Orta ve büyük işte son kontrolde denetçi (Sol; kodun çoğunu Sol yazdığı büyük işte Opus) da kaliteyi denetler; görsel sonuca Jev'in hazırladığı karelerden bakar. Kaba taklitle karşılanan bir ölçüt en fazla "kısmen" sayılır ve rapordaki eksiklere (G1, G2…) yazılır.

## Kurulum

### Gerekenler

- Windows 10 ya da 11 (PowerShell 5.1 yeterli)
- Python 3.11 ya da üstü (`py` başlatıcısı ya da PATH'te `python`)
- Git (PATH'te)
- [Codex CLI](https://github.com/openai/codex), ChatGPT Plus hesabınla giriş yapılmış
- [Claude Code](https://docs.claude.com/claude-code), Claude Pro hesabınla giriş yapılmış

Codex ve Claude Code'un PATH'te olması gerekmez. Jev önce PATH'e, sonra bilinen kurulum klasörlerine (`%LOCALAPPDATA%\OpenAI\Codex\bin\…`, `%APPDATA%\Claude\claude-code\<sürüm>\`) bakar. Claude masaüstü uygulaması paketli (Microsoft Store/MSIX) kuruluysa ikinci klasör aslında `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\<sürüm>\` altındadır; Jev orayı da arar. Bulamazsa [`[cli]` ayarıyla](#ayarlar) tam yolu verirsin.

### 1. Jev'i kur

Bu README'nin bulunduğu klasörde PowerShell aç:

```powershell
powershell -ExecutionPolicy Bypass -File .\kur.ps1
```

`kur.ps1` şunları yapar:

- Python 3.11 ya da üstünü bulur.
- Jev'i düzenlenebilir kipte kurar (`pip install --no-index -e .`). İnternetten hiçbir şey indirilmez: kurulum aracı depoda (`tools/paketle.py`), Jev'in bağımlılığı yok.
- `jev` komutunun PATH'te olup olmadığını denetler. Değilse ne yapman gerektiğini yazar. `-YolaEkle` ile çalıştırırsan klasörü kullanıcı PATH'ine kendisi ekler:

```powershell
powershell -ExecutionPolicy Bypass -File .\kur.ps1 -YolaEkle
```

Birden fazla Python kuruluysa hangisinin kullanılacağını `-Python` ile seçebilirsin:

```powershell
powershell -ExecutionPolicy Bypass -File .\kur.ps1 -Python "C:\Python313\python.exe"
```

Betik kullanmadan kurmak istersen:

```powershell
py -m pip install -e .
```

Düzenlenebilir kurulumda depodaki kod doğrudan kullanılır: kodu güncellersen yeniden kurman gerekmez. Depo klasörünü taşır ya da silersen yeniden kur. Kaldırmak için: `py -m pip uninstall jev`.

`jev` komutu bulunamazsa her yerde `py -m jev …` da çalışır.

### 2. Giriş yap

Jev senin yerine giriş yapmaz. Her CLI'a bir kez giriş yap:

```powershell
codex login
```

```powershell
claude auth login
```

`claude` PATH'te değilse tam yolla çalıştır. Claude masaüstü uygulamasıyla gelen `claude.exe` genellikle şuradadır (sürüm klasörü farklı olabilir):

```powershell
& "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.281\claude.exe" auth login
```

- Doğru yolu `jev ajanlar` gösterir ("CLI'lar" bölümü). `jev` komutu henüz yoksa `py -m jev ajanlar` yaz.
- `%APPDATA%\Claude\claude-code\…` yolu terminalinde "is not recognized" hatası verirse uygulama paketli kuruludur: yukarıdaki `Packages` yolunu kullan. `%APPDATA%` altındaki yolu yalnızca uygulamanın kendi içinden başlayan programlar görür.
- Varsayılan giriş abonelik girişidir (Claude Pro). `--console` seçeneğini kullanma: API faturalandırmasına geçer.
- Claude masaüstü uygulamasında oturumun açık olsa bile bağımsız `claude.exe` için ayrıca giriş gerekir.

### 3. Jev modeli (TypeSafe)

Jev'in kararlarını [TypeSafe Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) modeli verir. Bu bir System One modelidir: metin üretmez, sorulan soruları olasılık ve güven değeriyle yapılandırılmış biçimde yanıtlar. Jev modeli dört yerde kullanır:

- **Ölçek.** İşin boyu ve zorluğu (bkz. [Ölçekler](#ölçekler)).
- **Dağıtım.** Birden çok müsait ajan varsa görevin hangisine verileceği. Kuralın seçimi modele öneri olarak gider. Jev emin değilse kural uygulanır.
- **Kabul.** Tek görevli işte sonucun isteği alışılmış biçimde ve özenle karşılayıp karşılamadığı.
- **Sorunlu görevde karar.** Hangi karar, hangi ajan, hangi efor, geri alma gerekip gerekmediği ve sorunun kök nedeni.

Anahtarı [console.typesafe.ai](https://console.typesafe.ai) adresinden al ve bir kez kaydet. Anahtar yalnızca Jev sürecinde kullanılır, hiçbir ajana geçmez:

```powershell
setx TYPESAFE_API_KEY "anahtarın"
```

`setx` sonrasında yeni bir terminal aç. Maliyet milyon girdi token'ı başına yaklaşık $0.042'dir, çıktı ücretsizdir. Bir karar genellikle birkaç bin token tutar. Kullanım koşunun `usage.json` dosyasında (`jev` anahtarı) ve `jev ajanlar` çıktısında görünür. Her çağrı `jev-model.jsonl` dosyasına kaydedilir.

Anahtar yoksa ya da `[jev.model] acik = false` ise eski yol çalışır: kararı Opus (yedek Sol) verir, dağıtımı kural yapar. Kuru koşu ağa çıkmaz; sahte bir Jev modeli senaryodaki kararları oynar. Ayarlar `[jev.model]` altındadır: `guven_esigi` (varsayılan 0.6), `dagitim`, `zaman_asimi_sn`, `deneme`.

### 4. Denetle

```powershell
jev ajanlar
```

Ajanları, rollerini ve efor düzeylerini, Jev modelinin durumunu (anahtar var mı), bulunan CLI'ları (sürüm ve yol), kota durumunu ve gerçek koşulardaki kullanımı gösterir. Model çağırmaz.

```powershell
jev ajanlar --test
```

Her ajana kısa bir istek gönderip "OK" yazdırır: girişlerin ve model adlarının çalıştığını doğrular. Az da olsa kota harcar. Tek bir ajanı denemek için: `jev ajanlar --test luna`.

### 5. İlk koşu: kuru koşu

```powershell
jev --kuru "Yapılacaklar listesi CLI tasarla"
```

Kuru koşuda gerçek model çağrılmaz, kota harcanmaz. Sahte ajanlar, isteğe bakmadan hazır bir senaryoyu oynar: küçük bir yapılacaklar listesi programı yazılır, yolda birkaç sorun çıkar, Jev karar verir ve rapor gelir. Ofis tarayıcıda açılır; akışı ve düğmeleri böyle tanırsın. Proje `%USERPROFILE%\jev-projeler\kuru-…` altında açılır. Daha hızlı izlemek için `--kuru-hiz 5` ekle. Bu senaryo büyük ölçeğin tam hattını oynar; kuru koşuda plan onayı yalnızca `--onay` verilirse sorulur.

Küçük bir işin ne kadar kısa sürdüğünü görmek için mini senaryosunu dene. `jev.toml` dosyasına şunu yaz (yol, Jev'i kurduğun klasöre göre):

```toml
[kuru]
senaryo = 'C:\Users\<kullanıcı>\jev\jev\mock\senaryo_mini.json'
```

```powershell
jev --kuru "sadece test için çok basit bir hesap makinesi tasarla"
```

Plan, görev kartları ve denetçi olmadan tek bir işçi tuş takımlı bir hesap makinesi yazar (`index.html`, `hesap.js`, `hesap.test.js`); Jev testleri yeniden çalıştırıp raporu kendisi yazar. Testlerin çalışması için Node gerekir. Denedikten sonra `senaryo` satırını sil.

Görevlerin aynı anda çalışmasını görmek için aynı yolla `senaryo_paralel.json` dosyasını ver. Orta ölçekli bir birim dönüştürücüde iki görev aynı anda, her biri kendi çalışma kopyasında çalışır; üçüncü görev ikisini bekler (bkz. [Proje klasörü ve git](#proje-klasörü-ve-git)). Kuru koşuda görevler yalnızca senaryoda `paralel` anahtarı varsa aynı anda çalışır; varsayılan senaryo onları sırayla oynar.

### 6. İlk gerçek koşu

Küçük bir istekle başla:

```powershell
jev "Python için sıcaklık ve uzunluk birim dönüştürücü CLI tasarla"
```

Proje `%USERPROFILE%\jev-projeler\python-icin-sicaklik-ve-uzunluk-birim` klasöründe açılır. Mevcut bir projede çalışmak için `--proje` kullan (bkz. [Proje klasörü ve git](#proje-klasörü-ve-git)).

## Komutlar

```text
jev "X için Y tasarla" [seçenekler]   Yeni koşu başlatır
jev                                   Etkileşimli mod ("Ne tasarlayalım?")
jev devam [koşu]                      Duraklatılmış ya da yarıda kalmış koşuyu sürdürür
jev durum [koşu]                      Aşama, görevler, kota ve beyin çağrıları
jev rapor [koşu]                      Son raporu gösterir
jev duzelt [koşu] ["not"]             Rapordaki eksikler için düzeltme turu açar
jev ofis [koşu]                       Ofis arayüzünü açar (bitmiş ya da durmuş koşular için de)
jev ajanlar [--test] [ajan…]          Ajanlar, CLI'lar, kota ve kullanım; --test her ajana "OK" yazdırır
jev gecmis [N]                        Son N koşu (varsayılan 20)
```

| Seçenek | Ne yapar |
|---|---|
| `--proje DİZİN`, `-p DİZİN` | Mevcut bir projede çalış (verilmezse `proje_koku` altında yeni klasör açılır) |
| `--olcek SEVİYE` | İşin boyunu sen belirle: `mini`, `küçük`, `orta` ya da `büyük`. İstekteki ifadenin ve Jev'in ölçümünün önüne geçer (bkz. [Ölçekler](#ölçekler)) |
| `--onay` | Plan yazılınca onayını bekle (terminalde `onayla`/`reddet` ya da ofisteki düğmeler). Büyük işte onay her zaman beklenir; plan yazılmayan mini ve küçük işte bu bayrağın etkisi yoktur |
| `--kuru` | Sahte ajanlarla kuru koşu; kota harcanmaz |
| `--kuru-hiz N` | Kuru koşu hız çarpanı (ör. 5); `--kuru`'yu da açar |
| `--ayrintili`, `-v` | Ajanların anlık eylemlerini terminalde de göster |
| `--arayuz-yok` | Ofis arayüzünü açma |
| `--bitince-cik` | Rapordan ya da duraklamadan sonra komut bekleme, çık (betikler için) |
| `--surum` | Sürümü yaz |
| `--yardim`, `-h` | Yardımı göster |

**Koşu kimliği** tarih, saat ve istekten türeyen kısa bir addır: `20260928-143210-python-icin-sicaklik-ve`. Kimlik verilmezse bulunulan klasördeki projenin son koşusu, o da yoksa en son koşu kullanılır. Kimliğin başını yazmak yeter: `jev durum 20260928-1432`.

### Terminalden komutlar

Jev çalışırken terminalden de komut alır:

| Ne zaman | Komutlar |
|---|---|
| Koşu sırasında | `durum`, `çıkış` |
| Plan onay beklerken | `onayla`, `reddet` |
| Rapordan sonra | `düzelt [not]`, `rapor`, `durum`, `ofis`, `çıkış` |
| Duraklamada | `devam`, `durum`, `ofis`, `çıkış` |

**Ctrl+C** yarım kalan görevi geri alır (koşu dalı son commit'e döner) ve koşuyu güvenle durdurur. `jev devam` biten görevleri yeniden yapmadan kaldığı yerden sürer. Süreç çökse ya da bilgisayar kapansa da aynısı geçerlidir.

Görevler aynı anda çalışırken `çıkış` yeni görev başlatmaz, çalışan görevlerin bitmesini bekler ve sonra koşuyu duraklatır. İkinci bir `çıkış` çalışan ajanları hemen durdurur ve yarım işlerini geri alır.

**Çıkış kodları:** 0 rapor ya da başarılı · 1 hata · 2 kullanım hatası · 3 duraklatıldı · 4 iptal · 130 Ctrl+C.

## Ofis arayüzü

Koşu başlayınca Jev ofisi tarayıcıda açar ve adresini terminale `Ofis:` satırı olarak yazar. Sonradan açmak için: `jev ofis` (bitmiş ya da durmuş koşular için de çalışır).

- **Ofis:** Her ajan kılığıyla masasında: Opus proje şefi, Jev ustabaşı, Sol kıdemli usta, Sonnet usta, Luna çırak. Çalışan ajanın üstünde ne yaptığını anlatan bir konuşma balonu çıkar. Bir ajana tıklarsan şu anki görevini, etkinliğini, denemelerini, çağrılarını ve kararlarını görürsün.
- **Aşama şeridi:** İşin ölçeğine göre kurulur. Büyük işte Ölçekleme → Plan → Onay bekliyor → Uygulama → Son kontrol → Rapor, mini ve küçük işte Ölçekleme → Uygulama → Son kontrol → Rapor (düzeltme turunda Düzeltme). Yanında geçen süre, biten görev sayısı ve ölçek rozeti (ör. `Mini · Kolay`; üstüne gelince kaynağı ve akışı görünür).
- **Görev panosu:** Bekliyor, Sırada, Çalışıyor, Doğrulanıyor, Bitti ve Sorunlu sütunları. Bir karta tıklarsan görevin açıklamasını, kabul ölçütlerini, denemelerini, Jev'in kararlarını ve dosyalarını görürsün.
- **Akış ve Jev'in kararları:** Olayların akışı ve Jev'in her kararı, gerekçesiyle.
- **Düğmeler:** Plan onay beklerken **Planı onayla** ve **Reddet ve çık**; duraklamada **Devam**; rapordan sonra **Düzelt…** (istersen bir not yazarsın). **Plan** ve **Rapor** pencereleri her zaman açılabilir.

Ofis yalnızca bu bilgisayardan açılır: sunucu `127.0.0.1` adresinde dinler (varsayılan port 8765; doluysa sıradaki boş port). Adresteki `?k=…` anahtarı `jev` her başladığında rastgele üretilir; anahtarsız sayfa hiçbir şey göstermez, düğmeler de çalışmaz. Adresi paylaşma: anahtarı bilen, bu bilgisayardan koşuya komut verebilir.

`[arayuz] tarayici_ac = false` tarayıcıyı kendiliğinden açmaz (adres yine terminale yazılır). `--arayuz-yok` o koşuda arayüzü hiç başlatmaz.

## Proje klasörü ve git

**`--proje` verilmezse** `proje_koku` (varsayılan `%USERPROFILE%\jev-projeler`) altında istekten türeyen adla yeni bir klasör açılır ve `git init` yapılır. Aynı ad varsa sonuna `-2`, `-3` eklenir. Kuru koşuların klasör adı `kuru-` ile başlar.

**`--proje DİZİN` verilirse:**

- Klasör yoksa ya da boşsa oluşturulur ve `git init` yapılır.
- Git deposuysa en az bir commit'i olmalı ve çalışma ağacı temiz olmalı. Jev senin yerine stash yapmaz.
- Git deposu değilse `git init` yapılır ve mevcut dosyalar `jev: mevcut dosyalar (ilk commit)` commit'iyle kaydedilir.
- Başka bir deponun alt klasörüyse hata verir: deponun kök klasörünü ver.

Jev işi `jev/<koşu-kimliği>` dalında yapar. Her biten görev bu dalda bir commit olur. Jev push ya da rebase yapmaz ve senin dalına hiçbir şey birleştirmez. İnceledikten sonra dalı kendin birleştirirsin (raporun sonunda projenin yolu ve dal adlarıyla yazılıdır):

```powershell
cd "<proje>"
git switch main
git merge --no-ff jev/<koşu-kimliği>
```

Koşunun kayıtları projenin `.jev\` klasöründe durur. Jev bu klasörü `.git\info\exclude` dosyasına ekler, yani commit'lere girmez.

### Aynı anda çalışan görevler

Birbirini beklemeyen en az iki görev hazırsa Jev onları aynı anda çalıştırır. Sınır `paralel` ayarıdır (varsayılan 8: dört ajan × 2); ölçek bunu düşürür: mini ve küçük işte 1.

- **Çalışma kopyaları.** Her görev kendi çalışma kopyasında (git worktree) ve kendi dalında çalışır. Kopyalar projenin yanındaki `<proje>--jev-wt\` klasöründe açılır; dalların adı `jev/<koşu-kimliği>-serit-T03` biçimindedir. `node_modules`, `.venv` ve `venv` klasörleri kopyalara bağlanır, yeniden kurulmaz.
- **Ajan başına görev sınırı.** Bir ajan aynı anda en fazla `ajanlar.<ad>.es_zamanli` görev alır (varsayılan 2; `jev.toml`'da değiştirilebilir). Görevin uygun ajanlarının hepsi sınırına ulaşmışsa görev onlardan biri boşalana kadar bekler. Çıktı dosyaları çalışan bir görevinkilerle çakışan görev de o görev bitene kadar başlatılmaz.
- **Birleştirme.** Biten görevin işi önce kendi kopyasında doğrulanır. Bu arada başka görevler koşu dalına eklendiyse iş onlarla birleştirilir ve planın testleri yeniden çalıştırılır. Sonunda iş koşu dalına tek bir commit olarak eklenir, kopya ve dal silinir. Doğrulamalar sırayla çalışır, böylece aynı anda çalışan testler birbirini bozmaz.
- **Çakışma.** İki görev aynı dosyanın aynı yerini değiştirmişse sonra biten görevin işi çakışma işaretleriyle koşu dalının son hâline taşınır ve çakışmayı aynı ajan çözer. Bu deneme hakkından düşmez ve ajan başarısız sayılmaz. Aynı görev ikiden fazla kez çakışırsa kararı Jev verir.
- **Sırayla çalışma.** Yalnızca bir görev hazırsa görev proje klasöründe çalışır. Çalışma kopyası açılamazsa koşunun geri kalanı sırayla sürer.
- **Temizlik.** Koşu bitince, duraklayınca, Ctrl+C'de ya da iptalde yarım görevlerin kopyaları ve dalları silinir. Çökmeden kalanları `jev devam` toplar.

## Ayarlar

Varsayılanlar Jev'in içindeki `jev/varsayilan.toml` dosyasındadır. O dosyayı düzenleme; değiştirmek istediğin anahtarları aynı tablo adlarıyla `%USERPROFILE%\.jev\jev.toml` dosyasına yaz. Yalnızca yazdığın anahtarlar değişir, gerisi varsayılan kalır. Yanlış yazılmış bir anahtar ya da geçersiz bir değer (ör. olmayan bir rol, ya da denetçi rolü olmayan bir ajanı denetçi yapmak) Jev başlamadan önce hata olarak gösterilir.

```toml
proje_koku = 'D:\projeler\jev'       # Windows yolları için tek tırnak: ters bölü kaçış gerektirmez

[arayuz]
port = 8800
tarayici_ac = false

[ajanlar.sol]
efor = { L = "high" }                # Sol büyük görevlerde xhigh yerine high kullansın

[yonlendirme]
S = ["luna", "sol"]                  # küçük görevleri Sonnet almasın
```

| Anahtar | Varsayılan | Ne işe yarar |
|---|---|---|
| `proje_koku` | `~/jev-projeler` | `--proje` verilmeyince yeni projelerin açıldığı klasör |
| `paralel` | `8` | Aynı anda en çok kaç görev çalışır; `1` görevleri hep sırayla çalıştırır. Ölçek profili bunu düşürür: mini ve küçük 1 (bkz. [Aynı anda çalışan görevler](#aynı-anda-çalışan-görevler)). Kuru koşuda senaryonun `paralel` anahtarı geçerlidir; anahtar yoksa görevler sırayla çalışır |
| `plan_onayi` | `false` | `true`: planı olan her koşuda onayını bekle (`--onay` gibi). Büyük işte onay zaten beklenir (`[olcek.buyuk] plan_onayi`) |
| `her_gorevde_tam_test` | `true` | Her görevden sonra planın test komutunu da çalıştır |
| `[arayuz]` `acik`, `port`, `tarayici_ac` | `true`, `8765`, `true` | Ofis arayüzü |
| `[arayuz]` `sonucu_ac` | `true` | İş bitince HTML, video, görsel ya da PDF sonucunu aç. Programlar ve betikler asla başlatılmaz |
| `[zaman_asimi_dk]` | plan 30, isci_S/M/L 20/40/60, beyin 10, denetim 30, verify 10, test 2 | Çağrı başına süre sınırı (dakika) |
| `[sinirlar]` `gorev_basina_deneme` | `4` | Bir görev en çok kaç kez denenir |
| `[sinirlar]` `kosu_basina_beyin_cagrisi` | `25` | Bir koşuda Jev'in beynine en çok kaç kez danışılır |
| `[sinirlar]` `en_fazla_gorev` | `40` | Planın çıkarabileceği en çok görev kartı |
| `[sinirlar]` `onarim_denemesi` | `2` | Jev'in kart denetiminde bulduğu hatalar için planlayıcıya en çok kaç kez geri dönülür |
| `[sinirlar]` `denetim_karesi` | `6` | Son kontrolde denetçiye gösterilen en çok kare (ekran görüntüsü, video anı, PDF sayfası); `0` kapatır |
| `[guvenlik]` `tam_yetki`, `koruma` | `true`, `false` | Ajanların onaysız çalışması; isteğe bağlı felaket koruması |
| `[cli]` `codex`, `claude` | `"auto"` | CLI'ın yeri; bulunamazsa tam yolu yaz |
| `[cli]` `codex_kullanici_ayarini_yoksay` | `true` | Codex'in kendi `config.toml` ayarları (eklentiler, MCP) Jev'in çağrılarına yüklenmez: daha hızlı ve daha az token |
| `[ajanlar.<ad>]` | | Her ajanın `model`, `etiket`, `roller` ve `efor` ayarları |
| `[jev]` `planlayici`, `denetci` | `"opus"`, `"sol"` | Planı ve görev kartlarını kim yazar; orta ve büyük işte son kontrolü kim yapar. Ajanın ilgili rolü olmalı |
| `[jev]` `beyin`, `yedek_beyin` | `"opus"`, Sol (xhigh) | Sorunlarda kararı kim verir |
| `[jev]` `ret_takasi` | `"sol"` | Opus bir çağrıyı yedek modeliyle de güvenlik gerekçesiyle reddederse yalnızca o çağrıyı alan ajan. `""` kapatır |
| `[kota]` `ret_kaliplari` | Usage Policy iletileri | Güvenlik reddini tanıyan kalıplar (yeniden denenmez) |
| `[jev]` `yedek_denetci` | `{ ajan = "opus", olcekler = ["buyuk"] }` | Kodun yarısından fazlasını denetçi yazdıysa son kontrolü kim yapar, hangi ölçeklerde. `olcekler = []` kapatır, `["orta", "buyuk"]` orta işe de açar. Rol gerekmez |
| `[yonlendirme]` `S`, `M`, `L` | Luna, Sonnet, Sol · Sol, Sonnet · Sol, Sonnet | Görev boyuna göre aday ajanlar, öncelik sırasıyla. Planın görev için önerdiği ajan listenin başına eklenir |
| `[yonlendirme]` `eskalasyon` | `["opus"]` | Takılan görevi Jev'in kararıyla, boyu ne olursa olsun alabilecek ajanlar |
| `[yonlendirme]` `saglayici_dengesi` | `2` | Eşdeğer seçenek varken aynı sağlayıcıya art arda en çok kaç görev verilir |
| `[yonlendirme.tur]` | docs, test, config, research | Görev türüne göre aday ajanlar; türün listesi varsa boy listesinin yerine geçer |
| `[kota]` `bekleme_esigi_dk`, `varsayilan_soguma_dk` | `30`, `60` | Kota dolunca bekleme ya da duraklama (bkz. [Kota ipuçları](#kota-ipuçları)) |
| `[olcek]` `emin_olma` | `0.75` | Jev, işin daha büyük çıkma olasılığı bu eşiğin altında kalan en küçük seviyeyi seçer (bkz. [Ölçekler](#ölçekler)) |
| `[olcek]` `ogrenme` | `true` | Ölçek kararlarını ve sonuçlarını `olcek_gecmisi.jsonl` dosyasına yaz, sonraki ölçümlerde kullan |
| `[olcek]` `ifade_mini`, `ifade_buyuk` | "çok basit", "sadece test için"… · "kurumsal", "şirket için"… | Ölçeği doğrudan belirleyen açık ifadeler |
| `[olcek]` `kural_mini`, `kural_buyuk` | "basit", "küçük"… · "kapsamlı", "e-ticaret"… | Jev modeline ulaşılamazsa bakılan kelimeler |
| `[olcek.havuz]` `kolay`, `orta`, `zor` | Sonnet, Luna · Sonnet, Sol · Sol, Sonnet | Tek görevli işte zorluğa göre aday işçiler |
| `[olcek.<seviye>]` | bkz. [Ölçekler](#ölçekler) | Seviyenin profili: `plan`, `denetim` (`"yok"`, `"jev"`, `"tablo"` ya da tavan efor), `isci_tavan`, `beyin_tavan`, `en_fazla_gorev`, `deneme`, `verify_dk`, `paralel`, `plan_onayi` |

Efor düzeyleri: `low`, `medium`, `high`, `xhigh`, `max`.

Ayar klasörünü `JEV_HOME` ortam değişkeniyle değiştirebilirsin. Bu klasörde `jev.toml`'un yanında `kosular.json` (koşu dizini), `kota.json` (ajanların kota soğuma süreleri), `olcek_gecmisi.jsonl` (ölçek kararları ve sonuçları) ve `ajan-testi\` (`jev ajanlar --test` çalışma klasörü) de durur.

## Güvenlik uyarısı

**Ajanlar tam yetkiyle çalışır.** Codex `--dangerously-bypass-approvals-and-sandbox`, Claude Code `--dangerously-skip-permissions` ile başlatılır. Ajanlar onay sormadan komut çalıştırır, dosya yazar ve siler, internete çıkabilir, paket kurabilir. Jev'in kesintisiz çalışması için bu bilinçli bir seçimdir; riskini sen üstlenirsin. Plan ve görev kartları, son kontrol ve Jev'in beyin çağrıları salt okunur çalışır; tam yetki yalnızca kod yazan işçi çağrılarındadır.

### Felaket koruması

Guard varsayılan olarak kapalıdır (`[guvenlik] koruma = false`). Claude araç çağrılarına Jev'in PreToolUse kancası eklenmez; Codex komutları guard tarafından durdurulmaz. İşçinin doğrulama komutları da guard kurallarıyla elenmez. Pencere veya sunucu açıp bekleyen komutlar doğrulama için yine kullanılmaz.

İsteğe bağlı olarak `[guvenlik] koruma = true` yaparsan şunlar engellenir:

| Kategori | Engellenenler |
|---|---|
| Disk ve sistem | Sürücü kökünde, kullanıcı ya da sistem klasörlerinde toplu silme; sistem klasörlerine yazma; `format`, `diskpart`, `bcdedit`, `bootrec`, `Format-Volume`, `Clear-Disk`, `Initialize-Disk`, `Remove-Partition`; `shutdown`, `Stop-Computer`, `Restart-Computer`; Defender ayarı (`Set-MpPreference`, `Add-MpPreference`); `fsutil`, `takeown`, `mountvol`; kayıt defteri silme, içe aktarma ve HKLM'ye yazma; gölge kopya ve yedek silme; `cipher /w`; hizmet değiştirme (`sc delete/stop/config/create`); `net user`, `net localgroup` gibi hesap ve paylaşım komutları |
| Proje dışına yazma | Proje klasörü ve geçici klasör dışında dosya yazma ya da silme |
| Kimlik bilgisi | Codex ve Claude giriş dosyaları, `.ssh`, tarayıcı profilleri, `.git-credentials`, `.netrc`, `.aws\credentials`, Docker ve PyPI ayarları, Windows kimlik kasası; `cmdkey`, `vaultcmd`, `gh auth`, `git credential` |
| Dışarıya yayın | `git push`, `gh`, `npm`/`pnpm`/`yarn publish`, `twine upload`, `docker push`, `cargo`/`poetry`/`flit`/`hatch`/`uv publish`, `gem push`, `dotnet nuget` |
| Git | `reset`, `clean`, dal değiştiren `checkout` ve `switch`, `rebase`, dal silme ve taşıma, `stash`, etiket silme, `filter-branch`, `update-ref`, `gc`, `prune`, genel ya da sistem git ayarı. Commit'i Jev attığı için `commit`, `merge`, `cherry-pick`, `revert`, `am`, `init` de engellenir |
| Jev ve git iç dosyaları | `.git\` ve `.jev\` klasörlerine dokunma, proje klasörünü toptan silme |
| İndirip çalıştırma | İnternetten indirileni doğrudan çalıştırmak: `irm … \| iex`, `iwr … \| iex`, `curl … \| sh`, `bash <(curl …)`, `sh -c "$(curl …)"`, `[scriptblock]::Create((irm …))` gibi. İndirip dosyaya yazmak (`iwr -OutFile`) ve indirileni okumak (`\| ConvertFrom-Json`) serbesttir |

Engellenen her girişim `guard.jsonl` dosyasına yazılır ve raporun ekinde listelenir. Disk ve sistem, kimlik bilgisi, dışarıya yayın ve indirip çalıştırma kategorileri **ciddi** sayılır: görev sorunlu olur ve Jev karar verir.

### Sağlayıcıya göre nasıl uygulanır

- **Claude (Opus, Sonnet): önleyici.** Koruma bir PreToolUse kancasıdır. Her araç çağrısı çalışmadan önce denetlenir; yasak olan hiç çalışmaz.
- **Codex (Sol, Luna): önleyici değil, durdurucu.** Codex'in bu sürümünde kullanıcının `~/.codex` ayarlarına dokunmadan ön kanca eklenemiyor. Bu yüzden Jev ajanın canlı olay akışını izler: yasak bir komut başladığı anda ajanın süreç ağacını durdurur (`taskkill /T /F`). Görev bitince ayrıca denetim yapar: silinen dosyalar, dal ya da HEAD değişimi, `.git` ve `.jev` iç dosyaları. **Komut durdurulmadan önce başlamış ve kısmen iş yapmış olabilir.**

### Koruma bir güvenlik duvarı değildir

Komut çözümlemesi kasıtlı olarak basittir: amaç felaket sınıfındaki komutları yakalamaktır. Bir betik dosyasının içine yazılmış komutları, ağ erişimini ve programların yan etkilerini engellemez. Yaratıcı bir komut kuralları aşabilir.

**Öneriler:**

- Jev'i önemli verilerin olmadığı ayrı bir Windows kullanıcı hesabında ya da sanal makinede çalıştır.
- Mevcut bir projede çalışmadan önce her şeyi commit'le ve bir yedeğin olsun.
- Proje klasöründe parola, anahtar ya da `.env` gibi gizli bilgiler bulundurma: ajanlar proje dosyalarını okur ve modellerle paylaşır.
- Dalı birleştirmeden önce değişiklikleri incele (`git log`, `git diff main...jev/<koşu-kimliği>`).

## Kota ipuçları

- **Kuru koşu bedavadır.** `--kuru` hiç model çağırmaz; akışı ve arayüzü denemek için onu kullan.
- **Ölçek kotayı korur.** Mini ve küçük işte planlayıcı ve denetçi hiç çağrılmaz; tek bir işçi çalışır, son kontrolü Jev yapar. Jev'in ölçümü yanlış bir büyük ölçek seçerse koşuyu `--olcek küçük` ile başlat.
- **`jev ajanlar`** hangi ajanın soğumada olduğunu (ne zamana kadar) ve gerçek koşulardaki token kullanımını gösterir. `jev durum` o koşunun kota durumunu ve beyin çağrılarını gösterir.
- **Bir işçinin kotası dolarsa** görev, deneme hakkı harcanmadan sıradaki uygun ajana geçer. Uygun ajan kalmazsa sıfırlanma zamanına bakılır. Sıfırlanma 30 dakika içindeyse (`bekleme_esigi_dk`) Jev geri sayımla bekler. Daha uzaksa koşuyu duraklatır ve ne zaman devam edebileceğini yazar: `Kota doldu (…); ~HH:MM'den sonra jev devam`. Sıfırlanma zamanı bilinmiyorsa 60 dakika (`varsayilan_soguma_dk`) varsayılır.
- **Opus ve Sonnet aynı Claude kotasını paylaşır.** Claude'un 5 saatlik ve haftalık pencereleri hesabın tamamına aittir. Biri dolunca Jev ikisini de sıfırlanma saatine kadar soğumaya alır: kod kartları Sol'a ve Luna'ya geçer, Opus'un işleri (plan, düzeltme turu) bekler. Opus'a geçmek bu durumda işe yaramaz.
- **Sabit işler başka modele verilmez.** Plan, görev kartları ve düzeltme turu her zaman Opus'un, orta ve büyük işte son kontrol Sol'undur. Onların kotası dolarsa Jev bekler ya da duraklatır. Güvenlik reddi ayrıdır (aşağıda). İki istisna var: Jev'in beyni Opus'a ulaşılamazsa yedek beyne (Sol) geçer; büyük işte kodun çoğunu Sol yazdıysa son kontrolü Opus yapar, Opus'a ulaşılamazsa beklenmez, Sol yapar.
- **Güvenlik reddi: Opus 5.5 → Opus 4.8 → Sol.** Opus bir çağrıyı (plan, karar, düzeltme, görev ya da son kontrol) güvenlik gerekçesiyle yanıtlamazsa (ör. sızma testi, zararlı yazılım analizi) aynı çağrı bir kez Opus 4.8 ile denenir (`ajanlar.opus.ret_yedek_model`). O da yanıtlamazsa yalnızca o çağrı Sol'a geçer (`[jev] ret_takasi`); Opus koşudan çıkarılmaz, sonraki planı, kararları ve L kartlarını yine o yapar. Bir işçi (ör. Sonnet) bir kartı reddederse kart deneme hakkı harcanmadan Sol'a geçer; reddeden yalnızca o kartı bir daha almaz. Her ret rapor ekinde görünür. Ret, CLI'ın `stop_reason: refusal` bildiriminden ya da `[kota] ret_kaliplari` kalıplarından tanınır.
- **Opus'un kotası plana, kararlara, zor işe ve büyük işin son kontrolüne gider:** plan ve görev kartları, Jev'in beyni, zor mantık gerektiren L kartlar, Jev'in kararıyla takılan görevler (eskalasyon) ve kodun çoğunu Sol yazdığı büyük işin son kontrolü. Diğer kodu işçiler yazar: ana kodu Sol, test ve araştırmayı Sonnet, basit işleri Luna. Opus ve Sonnet aynı Claude kotasını kullanır; aynı anda dört Claude görevi kotayı daha hızlı bitirir.
- **Sol'un kotası iki işe gider:** ana kodlama ve orta ve büyük işte son kontrol. Sol soğumadaysa kod kartları sıradaki işçiye geçer ama son kontrol Sol'u bekler; yalnızca kodun çoğunu Sol yazdığı büyük işte son kontrolü Opus yapar. Kotan çabuk bitiyorsa kod kartlarını Sonnet'e öne alabilirsin:

  ```toml
  [yonlendirme]
  M = ["sonnet", "sol"]
  L = ["sonnet", "sol"]
  ```

- **Efor kota tüketimini doğrudan etkiler.** `xhigh` yerine `high`, `high` yerine `medium` daha az harcar (bkz. [Ayarlar](#ayarlar)).
- **Kota iki aboneliğe yayılır.** `saglayici_dengesi = 2` eşdeğer seçenek varken aynı sağlayıcıya art arda en çok iki görev verir; böylece ChatGPT ve Claude kotaları dengeli tükenir.
- **Abonelik kullanılır, API değil.** Ortamda `ANTHROPIC_API_KEY` tanımlı olsa bile Jev bunu Claude Code'a geçirmez (`[cli] claude_ortam_temizle`).
- **Oturum kapanırsa** (giriş süresi dolduysa) koşu duraklar ve hangi komutla giriş yapacağın yazılır. Giriş yapıp `jev devam` yaz.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `jev` komutu bulunamadı | `py -m jev …` her zaman çalışır. Kalıcı çözüm: `.\kur.ps1 -YolaEkle`, sonra yeni bir terminal aç. |
| Türkçe karakterler bozuk | Windows Terminal kullan. Jev'in çıktısını bir PowerShell komutuna aktarıyorsan önce `[Console]::OutputEncoding = [Text.Encoding]::UTF8` çalıştır. Jev'in dosyaları BOM'suz UTF-8'dir: PowerShell 5.1'de `Get-Content -Encoding UTF8 …` ile oku. |
| "Giriş gerekli" ya da koşu oturum yüzünden duraklatıldı | Terminalde yazan komutla giriş yap (`codex login` ya da `claude auth login`; `claude` PATH'te değilse [Giriş yap](#2-giriş-yap) bölümündeki tam yolla), sonra `jev devam`. |
| CLI bulunamadı | `jev ajanlar` hangi CLI'ın bulunamadığını gösterir. `jev.toml`'da tam yolu ver: `[cli]` altında `codex = 'C:\…\codex.exe'` |
| `jev ajanlar --test` bir ajanda başarısız | Hata metnine bak. Model aboneliğinde yoksa `jev.toml`'da `[ajanlar.<ad>] model` değerini değiştir. |
| "Çalışma ağacı temiz değil" | Değişikliklerini commit'le ya da kendin stash yap, sonra yeniden dene. |
| "Bu projede başka bir jev süreci çalışıyor" | O süreç bitene kadar bekle ya da onu kapat. Süreç artık çalışmıyorsa kilit kendiliğinden kaldırılır. |
| Ofis açılmıyor ya da port dolu | Jev sıradaki 20 porta kadar dener. Adres terminalde yazar; `jev ofis` ile yeniden açabilirsin. İstersen `[arayuz] port` ayarını değiştir. |
| Ofis "Erişim anahtarı yok" diyor | Terminaldeki `Ofis:` satırındaki adresi `?k=…` kısmıyla birlikte aç ya da `jev ofis` çalıştır. Anahtar `jev` her başladığında değişir. |
| Ctrl+C, çökme, bilgisayar kapandı | `jev devam`: yarım görev geri alınır, biten görevler yeniden yapılmaz. |
| Projenin yanında `<proje>--jev-wt` klasörü | Aynı anda çalışan görevlerin çalışma kopyalarıdır. Koşu bitince, duraklayınca ya da iptal edilince silinir; çökmeden kalanları `jev devam` toplar. Koşuyu sürdürmeyeceksen klasörü silip projede `git worktree prune` çalıştırabilirsin. |
| Kota doldu, koşu duraklatıldı | Yazan saatten sonra `jev devam`. Ayrıntı: [Kota ipuçları](#kota-ipuçları). |
| Koruma bir şeyi engelledi | Guard varsayılan olarak kapalıdır. Kullanıcı ayarlarında `[guvenlik] koruma = true` varsa `false` yapıp Jev'i yeniden başlat. Ayrıntı raporun ekinde ve `guard.jsonl` dosyasında. |
| "Bilinmeyen ayar" ya da "Ayar hataları" | `jev.toml`'daki anahtar adlarını `jev/varsayilan.toml` ile karşılaştır. |

## Koşu dosyaları

Her koşu `<proje>\.jev\runs\<koşu-kimliği>\` klasörüne kaydedilir:

| Dosya | İçerik |
|---|---|
| `plan.md`, `plan.json` | Opus'un planı: ihtiyaçlar, başarı ölçütleri, sözleşmeler ve kartların listesi (mini ve küçük işte Jev'in tek ölçütlü planı) |
| `tasks.json` | Görev kartları ve durumları |
| `progress.md` | İlerleme özeti |
| `report.md`, `report.json` | Son kontrolün raporu (orta ve büyük işte Sol'un, kodun çoğunu Sol yazdığı büyük işte Opus'un; mini ve küçük işte Jev'in); düzeltme turlarında `report-2.md`, `report-3.md`… |
| `state.json` | Koşunun durumu (`jev devam` buradan sürer) |
| `events.jsonl` | Tüm olaylar (ofis bunları gösterir) |
| `decisions.jsonl` | Jev'in kararları ve gerekçeleri |
| `guard.jsonl` | Korumanın engelledikleri |
| `usage.json` | Ajan başına token kullanımı |
| `calls\` | Her model çağrısının istemi ve çıktısı |
| `dogrulama\` | Doğrulama komutlarının çıktıları |

## Geliştirme

```powershell
py -m unittest discover -s tests
```

Tüm testler yaklaşık 6 dakika sürer. `$env:JEV_HIZLI = "1"` uzun uçtan uca testleri atlar. Testler gerçek model çağırmaz, tarayıcı açmaz; senin `%USERPROFILE%\.jev` klasörüne ve genel git ayarlarına dokunmaz.

Belgeler:

- [AI_PROJECT_MAP.md](../AI_PROJECT_MAP.md): yapay zekâlar için ayrıntılı mimari, işlem akışları, değişiklik/hata yönlendirmesi, dosya ve sembol bağımlılık dizinleri
- [docs/jev-sistem-promptu.md](jev-sistem-promptu.md): sistemin belirtimi (kurallar, akış, kabul ölçütleri)
- [docs/tasarim.md](tasarim.md): tasarım kararları, belirtimden sapmalar ve modül haritası
- [docs/cli-notlari.md](cli-notlari.md): Codex ve Claude Code CLI'larıyla ilgili bulgular
- [docs/ilerleme.md](ilerleme.md): yapılanlar ve kalanlar
