# İlerleme

Son güncelleme: 2026-09-28.

> Bu belge 28 Eylül 2026'daki durumun kaydıdır ve o tarihten sonra güncellenmedi. Sonraki değişiklikler için güncel kaynak `README.md` ve `docs/tasarim.md`'dir: paralel mod, ölçekler, Jev modeli ve 1 Ekim 2026'da Astra'nın ekipten çıkarılması. Aşağıdaki Astra ve "parçalama" satırları o günün düzenini anlatır.

## Kilometre taşları (§9)

- [x] M0 Codex: yollar, olay biçimi, yapısal çıktı, açılış süresi, kanca araştırması → `cli-notlari.md`
- [ ] M0 Claude: giriş yapıldı (abonelik, 2026-09-28), `jev ajanlar --test`'te Opus ve Sonnet OK. Kalan: stream-json biçimi, `--json-schema`, bypass modunda kanca → `cli-notlari.md`
- [x] M1 İskelet: config, util, sema, events, state, terminal, cli
- [x] M2 Adaptörler: base, codex, claude, mock + discovery, quota, guard
- [x] M3 Plan + parçalama (+ dag, context, gitops)
- [x] M4 Dağıtım + doğrulama + commit
- [x] M5 Beyin + kararlar (+ Astra yasağı testi)
- [x] M6 Son kontrol + düzeltme turu
- [x] M7 Ctrl+C, devam, kota beklemesi, kilit
- [x] M8 durum, rapor, ajanlar, gecmis, etkileşimli mod
- [x] M9 Güvenlik testleri
- [x] M10 Ofis arayüzü
- [x] M11 Kuru uçtan uca koşu, Türkçe README, kurulum (`tools/paketle.py`, `kur.ps1`). Küçük gerçek koşu hariç: o, kullanıcıya sorularak en sona kaldı.
- [ ] M12 (isteğe bağlı) Paralel mod: yapılmadı. `paralel = 1` dışındaki değer ayar hatası verir.

## Kabul ölçütleri (§10)

| # | Ölçüt | Durum |
|---|---|---|
| 1 | `pip install -e .` sonrasında `jev` çalışıyor | ✓ Geçici bir venv'de doğrulandı: `kur.ps1`, tekerlek derleme, tekerlekten kurulum ve depo dışında kuru koşu, sdist, kaldırma. Kalıcı test: `tests/test_paketle.py` |
| 2 | Testler geçiyor | ✓ 367 test, hepsi geçiyor (~165 sn; `JEV_HIZLI=1` uzun testleri atlar) (`py -m unittest discover -s tests`). §10'daki konular: DAG `test_dag`, yönlendirme `test_routing`, durum geçişleri `test_kosucu` ve `test_state`, kota `test_quota`, etkinlik ayrıştırma (M0 örnekleriyle) `test_etkinlik`, git checkpoint ve geri alma `test_gitops`, devam `test_kosucu`, koruma `test_guard`, Astra yasağı `test_astra_yasak`, ayar doğrulaması `test_ayar`, SSE `from=seq` `test_olaylar_ofis`, rapor HTML kaçışlaması `test_md`. Ayrıca CLI bulma (paketli uygulamalar dahil) `test_discovery`. |
| 3 | `jev ajanlar --test` beş ajandan OK alıyor | 2026-09-28: Opus, Sonnet ve Luna ✓. Astra ve Sol API'den 400 aldı: kullanıcının ChatGPT Plus aboneliği bitmiş (M0'da ikisi de çalışıyordu). Abonelik yenilenince `jev ajanlar --test astra sol` tekrarlanacak. |
| 4 | `jev --kuru` uçtan uca bitiyor | ✓ Yapay başarısızlık beyin kararıyla, kota olayı başka ajana yönlendirilerek, bozuk JSON onarılarak; her görev için bir commit. |
| 5 | Ofis arayüzü kuru koşuyu canlı gösteriyor | ✓ Düzelt düğmesi dahil. |
| 6 | Ctrl+C → `jev devam` | ✓ Tamamlanan görevler tekrarlanmıyor. |
| 7 | Koruma tehlikeli komutu engelliyor, olay arayüzde görünüyor | ✓ Kurallar, Claude kancası (dosya yoluyla, gerçek alt süreçte) ve kuru koşudaki engelleme testli. Gerçek bir Claude işçisinde deneme, girişten sonra küçük gerçek koşuyla yapılacak. |
| 8 | Küçük gerçek koşu | Bekliyor. Kota harcadığı için en sona kaldı; başlamadan kullanıcıya sorulacak. |
| 9 | Türkçe karakterler bozulmuyor | ✓ Terminal, dosyalar, loglar, arayüz. |
| 10 | README Türkçe ve M11 bölümlerini içeriyor | ✓ |

## Sıradaki adımlar

1. Kullanıcı Claude Code'a bir kez giriş yapar (abonelik girişi; `--console` değil). Claude masaüstü uygulaması paketli (MSIX) kurulu olduğu için `%APPDATA%` yolu kullanıcının terminalinde çalışmıyor; paketteki gerçek yol kullanılmalı (`cli-notlari.md`):
   `& "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.281\claude.exe" auth login`
2. Kullanıcı ChatGPT Plus'ı yeniler, sonra `jev ajanlar --test astra sol`. Claude bulguları `cli-notlari.md`'ye yazılır.
3. Kullanıcıya sorulduktan sonra küçük gerçek koşu: `jev "Python için sıcaklık ve uzunluk birim dönüştürücü CLI tasarla"`.
4. İsteğe bağlı: M12 paralel mod (her görev ayrı git worktree'de; sağlayıcı başına aynı anda en çok bir ajan).
