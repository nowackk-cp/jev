"""jev/mock/senaryo_varsayilan.json dosyasını üretir.

Senaryo, `jev --kuru` koşusunda sahte ajanların ne yapacağını anlatır. JSON'u doğrudan da
düzenleyebilirsin; bu betik yalnızca uzun Markdown metinlerini kaçış derdi olmadan yazmak için var.

Kabul ölçütü 4'ün istediği olaylar bu senaryoda:
- T02: Sol hatalı kod yazar, test gerçekten kalır → Jev'in beyni görevi Sonnet'e verir → Sonnet düzeltir.
- T05: Luna'nın kotası dolar → kural katmanı görevi Sonnet'e yönlendirir. (Kota denetçi Sol'da dolsaydı son kontrol
  kotanın açılmasını beklerdi: sabit işler başka modele verilmez.)
- T04: Sonnet bozuk JSON döndürür → onarım çağrısı. Ayrıca proje dışına yazmayı dener → koruma engeller.
- T06: uçtan uca test (planın önerisi Sonnet); T02 ve T03 kartlarına C1 sözleşmesi (TodoStore) Jev'ce konur.
- Son kontrol SC4'ü karşılanmamış bulur → kullanıcı "düzelt" derse D1-01 görevi eksik komutu ekler.

Planlayıcı planı ve görev kartlarını tek çağrıda yazar: kartlar planın `tasks` alanındadır.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "jev" / "mock" / "senaryo_varsayilan.json"

PLAN_MD = """# Yapılacaklar CLI

## 0. Özet
Komut satırından kullanılan, görevleri bir JSON dosyasında saklayan basit bir yapılacaklar listesi.
Komutlar: `ekle`, `listele`, `bitir`, `sil`, `temizle`. Yalnızca Python standart kütüphanesi kullanılır.
Depolama ve komut satırı ayrı modüllerdir; ikisi de `unittest` ile test edilir.

## 1. Amaç ve kapsam
**Hedefler**
- Görev ekleme, listeleme, tamamlandı olarak işaretleme, silme ve tamamlananları temizleme.
- Görevler yeniden başlatmada kaybolmaz; Türkçe karakterler bozulmaz.
- Hatalı girdide anlaşılır Türkçe hata mesajı ve 1 çıkış kodu.

**Kapsam dışı**
- Grafik arayüz, eşitleme, öncelik ve tarih alanları.

## 2. Varsayımlar ve kısıtlar
- Python 3.11 ya da daha yenisi kurulu; ek paket kurulmayacak.
- Veri dosyası varsayılan olarak `%USERPROFILE%\\.todo-cli.json`; `TODO_DOSYA` ortam değişkeniyle değiştirilebilir.
- Komutlar Windows PowerShell 5.1'de çalışır.

## 3. Mimari
- `todo/store.py`: `TodoStore` sınıfı; JSON dosyasını okur ve atomik olarak yazar.
- `todo/cli.py`: `argparse` ile alt komutlar; `main(argv) -> int`.
- `todo/__main__.py`: `python -m todo` giriş noktası.
- `tests/`: `unittest` testleri. Testler geçici klasörde çalışır, kullanıcı verisine dokunmaz.

Veri akışı: CLI → `TodoStore` → JSON dosyası. Görev numaraları kullanıcıya 1'den başlayarak gösterilir.

## 4. Modüller
- **M1 Depolama** (`todo/store.py`): `add(text)`, `list()`, `done(n)`, `remove(n)`, `clear_done()`. Bağımlılık yok.
- **M2 Komut satırı** (`todo/cli.py`): `ekle`, `listele`, `bitir N`, `sil N`, `temizle`. M1'e bağlı.
- **M3 Belgeler** (`README.md`): kurulum, kullanım, testler. M2'ye bağlı.

## 5. Başarı ölçütleri
- **SC1:** `ekle` ve `listele` çalışır. Komut: `python -m unittest tests.test_cli -v`
- **SC2:** Görevler kalıcıdır ve Türkçe karakterler bozulmaz. Komut: `python -m unittest tests.test_store -v`
- **SC3:** `bitir N` ve `sil N` doğru görevi etkiler; geçersiz numarada hata ve 1 çıkış kodu. Komut: `python -m unittest tests.test_cli -v`
- **SC4:** `temizle` tamamlanan görevleri siler. Komut: `python -m unittest tests.test_cli -v`
- **SC5:** README kurulum ve kullanımı anlatır. Komut: `Select-String -Path README.md -Pattern "python -m todo"`

## 6. Kalite standartları
- PEP 8, tip ipuçları, kısa fonksiyonlar.
- Her modülün `unittest` testleri; testler geçici klasör kullanır.
- Kullanıcıya dönük metinler Türkçe.

## 7. Riskler ve önlemler
- Veri dosyasının yarım yazılması → geçici dosyaya yazıp `os.replace` ile değiştirme.
- Konsolda Türkçe karakterlerin bozulması → stdout'u UTF-8'e ayarlama.

## 8. Önerilen uygulama sırası
1. İskelet ve test altyapısı
2. Depolama ve testleri
3. Komut satırı
4. Komut satırı testleri
5. README
"""

PLAN = {
    "project_name": "Yapılacaklar CLI",
    "slug": "yapilacaklar-cli",
    "summary": "Komut satırından kullanılan, görevleri JSON dosyasında saklayan basit bir yapılacaklar listesi. "
               "Komutlar: ekle, listele, bitir, sil, temizle. Yalnızca Python standart kütüphanesi.",
    "plan_markdown": PLAN_MD,
    "stack": {"language": "Python 3.11+", "frameworks": ["argparse", "unittest"],
              "runtime_notes": "Ek paket yok. Komutlar Windows PowerShell 5.1'de çalışır."},
    "commands": {"install": None, "test": "python -m unittest discover -s tests -v",
                 "run": "python -m todo listele", "lint": None},
    "modules": [
        {"id": "M1", "name": "Depolama", "responsibility": "Görevleri JSON dosyasında saklamak",
         "interfaces": "TodoStore.add/list/done/remove/clear_done", "depends_on": []},
        {"id": "M2", "name": "Komut satırı", "responsibility": "Alt komutlar ve çıktı",
         "interfaces": "todo.cli.main(argv) -> int; python -m todo", "depends_on": ["M1"]},
        {"id": "M3", "name": "Belgeler", "responsibility": "Kurulum ve kullanım belgesi",
         "interfaces": "README.md", "depends_on": ["M2"]},
    ],
    "success_criteria": [
        {"id": "SC1", "statement": "ekle ve listele çalışır",
         "verification": "CLI testleri", "command": "python -m unittest tests.test_cli -v"},
        {"id": "SC2", "statement": "Görevler kalıcıdır ve Türkçe karakterler bozulmaz",
         "verification": "Depolama testleri", "command": "python -m unittest tests.test_store -v"},
        {"id": "SC3", "statement": "bitir N ve sil N doğru görevi etkiler; geçersiz numarada hata ve 1 çıkış kodu",
         "verification": "CLI testleri", "command": "python -m unittest tests.test_cli -v"},
        {"id": "SC4", "statement": "temizle tamamlanan görevleri siler",
         "verification": "CLI testi: temizle sonrası yalnızca bitmemiş görevler kalır",
         "command": "python -m unittest tests.test_cli -v"},
        {"id": "SC5", "statement": "README kurulum ve kullanımı anlatır",
         "verification": "README.md içinde kullanım örnekleri",
         "command": "Select-String -Path README.md -Pattern \"python -m todo\""},
    ],
    "assumptions": ["Python 3.11+ kurulu", "Ek paket kurulmayacak", "Veri dosyası TODO_DOSYA ile değiştirilebilir"],
    "out_of_scope": ["Grafik arayüz", "Eşitleme", "Öncelik ve tarih alanları"],
    "risks": ["Yarım yazılmış veri dosyası", "Konsolda Türkçe karakter bozulması"],
    "conventions": "PEP 8 ve tip ipuçları. Kullanıcıya dönük metinler Türkçe. Testler unittest ile, geçici "
                   "klasörde çalışır; kullanıcının gerçek veri dosyasına dokunmaz. Görev numaraları 1'den başlar.",
    "scale": None,
    "needs": [],
    "contracts": [
        {"id": "C1", "name": "TodoStore",
         "definition": "todo/store.py: class TodoStore(path: Path | None = None); path verilmezse TODO_DOSYA ortam "
                       "değişkeni, o da yoksa ~/.todo-cli.json. Öğe: {\"text\": str, \"done\": bool}. "
                       "add(text) -> öğe (boş metinde ValueError); list() -> list[öğe]; done(n) -> öğe; "
                       "remove(n) -> öğe; clear_done() -> int (silinen sayısı). Numaralar 1'den başlar; geçersiz "
                       "numarada IndexError. Dosya UTF-8 JSON, atomik yazılır (geçici dosya + os.replace)."},
    ],
}


def task(id_, title, module, type_, desc, acceptance, verify, outputs, deps, covers, cx, agent, notes="", *,
         reads=None, contracts=None):
    return {"id": id_, "title": title, "module": module, "type": type_, "description": desc, "reads": reads or [],
            "outputs": outputs, "contracts": contracts or [], "acceptance": acceptance, "verify": verify,
            "depends_on": deps, "covers_criteria": covers, "complexity": cx, "suggested_agent": agent,
            "notes": notes}


TMP_DATA = ("$env:TODO_DOSYA = Join-Path $env:TEMP 'jev-kuru-todo.json'; "
            "Remove-Item $env:TODO_DOSYA -ErrorAction SilentlyContinue; ")

TASKS = [
        task("T01", "Proje iskeleti ve test altyapısı", "M1", "config",
             "todo paketini, python -m todo giriş noktasını ve tests klasörünü bir duman testiyle oluştur.",
             ["import todo çalışır", "tests klasöründe en az bir test geçer"],
             ["python -c \"import todo; print(todo.__version__)\"", "python -m unittest tests.test_smoke -v"],
             ["todo/__init__.py", "todo/__main__.py", "tests/__init__.py", "tests/test_smoke.py"],
             [], [], "S", "luna"),
        task("T02", "Depolama katmanı ve testleri", "M1", "code",
             "todo/store.py içinde TodoStore sınıfını yaz: add, list, done, remove, clear_done. Numara 1'den başlar. "
             "Dosyayı atomik yaz. tests/test_store.py ile test et.",
             ["Tüm depolama testleri geçer", "Türkçe karakterler bozulmaz", "Geçersiz numarada IndexError"],
             ["python -m unittest tests.test_store -v"],
             ["todo/store.py", "tests/test_store.py"],
             ["T01"], ["SC2"], "M", "sol", reads=["todo/__init__.py"], contracts=["C1"]),
        task("T03", "Komut satırı arayüzü", "M2", "code",
             "todo/cli.py içinde argparse ile ekle, listele, bitir, sil ve temizle alt komutlarını yaz. "
             "Hatalı numarada Türkçe hata ve 1 çıkış kodu döndür.",
             ["python -m todo ekle ve listele çalışır", "Hatalı numarada çıkış kodu 1", "temizle tamamlananları siler"],
             [TMP_DATA + "python -m todo ekle 'Süt al'; python -m todo listele"],
             ["todo/cli.py"],
             ["T02"], ["SC1", "SC3", "SC4"], "M", "sol", reads=["todo/store.py"], contracts=["C1"]),
        task("T04", "Komut satırı testleri", "M2", "test",
             "tests/test_cli.py içinde CLI'ı geçici veri dosyasıyla test et.",
             ["ekle/listele, bitir/sil ve hatalı numara testleri geçer"],
             ["python -m unittest tests.test_cli -v"],
             ["tests/test_cli.py"],
             ["T03"], ["SC1", "SC3"], "S", "sonnet", reads=["todo/cli.py"]),
        task("T05", "README", "M3", "docs",
             "README.md: kurulum, kullanım örnekleri, veri dosyası ve testler.",
             ["README'de python -m todo örnekleri var"],
             ["if (-not (Select-String -Path README.md -Pattern 'python -m todo' -SimpleMatch -Quiet)) { exit 1 }"],
             ["README.md"],
             ["T03"], ["SC5"], "S", "luna", reads=["todo/cli.py"]),
        task("T06", "Uçtan uca test", "M2", "test",
             "tests/test_uctan_uca.py içinde python -m todo'yu gerçek bir alt süreç olarak, geçici veri dosyasıyla "
             "çalıştır: ekle, bitir, listele ve sil zincirini, verinin kalıcılığını ve hatalı numarada 1 çıkış kodunu "
             "doğrula.",
             ["Alt süreçle çalışan uçtan uca testler geçer", "Kullanıcının gerçek veri dosyasına dokunulmaz"],
             ["python -m unittest tests.test_uctan_uca -v"],
             ["tests/test_uctan_uca.py"],
             ["T03"], ["SC1", "SC3"], "M", "sonnet", reads=["todo/cli.py", "todo/store.py"]),
]


def think(text, delay=1.2):
    return {"do": "think", "text": text, "delay": delay}


def read(path, delay=0.8):
    return {"do": "read", "path": path, "delay": delay}


def write(path, template=None, content=None, delay=1.2):
    d = {"do": "write", "path": path, "delay": delay}
    if template:
        d["template"] = template
    if content is not None:
        d["content"] = content
    return d


def test(command, delay=1.5):
    return {"do": "test", "command": command, "delay": delay}


def cmd(command, delay=0.8):
    return {"do": "command", "command": command, "delay": delay}


WORKERS = [
    {"task": "T01", "steps": [
        think("Görevi okuyor"), read("AGENTS.md"),
        write("todo/__init__.py", "todo__init__.py.sablon"),
        write("todo/__main__.py", "todo__main__.py.sablon"),
        write("tests/__init__.py", content=""),
        write("tests/test_smoke.py", "test_smoke.py.sablon"),
        test("python -m unittest tests.test_smoke -v")],
     "outcome": "done", "summary": "todo paketi, python -m todo giriş noktası ve duman testi oluşturuldu. "
                                   "Duman testi geçiyor."},
    {"task": "T02", "agent": "sol", "steps": [
        think("Depolama katmanını tasarlıyor", 1.5), read("todo/__init__.py"),
        write("todo/store.py", "store_hatali.py.sablon", delay=2.0),
        write("tests/test_store.py", "test_store.py.sablon", delay=1.5),
        test("python -m unittest tests.test_store -v")],
     "outcome": "done", "summary": "TodoStore yazıldı; add, list, done, remove ve clear_done hazır. Testler yazıldı."},
    {"task": "T02", "agent": "sonnet", "steps": [
        think("Jev'in notunu ve önceki denemeyi okuyor", 1.5), read("todo/store.py"),
        test("python -m unittest tests.test_store -v"),
        think("Numara 1'den başlıyor; indeks n-1 olmalı", 1.2),
        write("todo/store.py", "store.py.sablon", delay=1.5),
        write("tests/test_store.py", "test_store.py.sablon", delay=0.8),
        test("python -m unittest tests.test_store -v")],
     "outcome": "done", "summary": "Sıra numarası hatası düzeltildi: done ve remove artık 1'den başlayan numarayı "
                                   "doğru indekse çeviriyor. Yedi depolama testi geçiyor."},
    {"task": "T03", "steps": [
        think("Komut satırını tasarlıyor", 1.5), read("todo/store.py"),
        write("todo/cli.py", "cli.py.sablon", delay=2.0),
        cmd("python -m todo ekle 'Süt al'"), cmd("python -m todo listele")],
     "outcome": "done", "summary": "argparse ile ekle, listele, bitir ve sil alt komutları yazıldı. Hatalı numarada "
                                   "Türkçe hata ve 1 çıkış kodu dönüyor."},
    {"task": "T04", "steps": [
        think("CLI testlerini planlıyor", 1.2), read("todo/cli.py"),
        {"do": "guard", "tool": "Write", "input": {"file_path": "~/Desktop/jev-kuru-yedek.txt"}, "delay": 1.0},
        think("Proje dışına yazamıyor; testleri geçici klasörde tutacak", 1.0),
        write("tests/test_cli.py", "test_cli.py.sablon", delay=1.5),
        test("python -m unittest tests.test_cli -v")],
     "outcome": "broken_json", "summary": "CLI testleri yazıldı: ekle/listele, bitir/sil ve hatalı numara. "
                                          "Testler geçici veri dosyası kullanıyor."},
    # Ajan belirtilmedi: T05'i ilk alan ajanın kotası dolar (kabul ölçütü 4), sonra görev başkasına gider.
    {"task": "T05", "steps": [think("Görevi okuyor", 1.0)],
     "outcome": "quota",
     "error": "You've hit your usage limit. Upgrade to Pro or try again in 45m."},
    {"task": "T05", "steps": [
        think("Belgeyi planlıyor", 1.0), read("todo/cli.py"),
        write("README.md", "README.md.sablon", delay=1.5)],
     "outcome": "done", "summary": "README yazıldı: kurulum, kullanım örnekleri, veri dosyası ve testler."},
    {"task": "T06", "steps": [
        think("Parçaların birlikte çalıştığını nasıl kanıtlayacağını düşünüyor", 1.4), read("todo/cli.py"),
        read("todo/store.py"), write("tests/test_uctan_uca.py", "test_uctan_uca.py.sablon", delay=1.8),
        test("python -m unittest tests.test_uctan_uca -v")],
     "outcome": "done", "summary": "Uçtan uca testler yazıldı: python -m todo alt süreçte ekle, bitir, listele ve sil "
                                   "zincirini, kalıcılığı ve hatalı numarayı doğruluyor. Üç test geçiyor."},
    {"task": "D1-01", "steps": [
        think("Rapordaki G1 eksiğini okuyor", 1.2), read("todo/cli.py"),
        write("todo/cli.py", "cli_temizle.py.sablon", delay=1.5),
        write("tests/test_cli.py", "test_cli_temizle.py.sablon", delay=1.2),
        test("python -m unittest tests.test_cli -v")],
     "outcome": "done", "summary": "temizle alt komutu eklendi ve test edildi; tamamlanan görevler siliniyor."},
]

BRAIN = {
    "T02": [{
        "decision": "reassign", "agent": "sonnet", "effort": "high", "rollback": True,
        "guidance": "Hata todo/store.py içindeki _index yönteminde: kullanıcı 1'den başlayan numara veriyor ama kod "
                    "numarayı doğrudan indeks olarak kullanıyor. Aralık kontrolü 1 <= n <= len(items) olmalı ve "
                    "indeks n - 1 dönmeli. Önce tests/test_store.py'yi çalıştırıp kalan testleri gör.",
        "revised_task": None, "new_tasks": [],
        "rationale": "Doğrulama çıktısında test_done_marks_right_item ve test_remove kalıyor: bir kaydırma hatası. "
                     "Sol aynı yaklaşımı tekrarlayabilir; testleri iyi okuyan Sonnet'e somut notla vermek daha ucuz.",
        "deviation": "none", "report_note": "T02: ilk deneme testlerden kaldı, görev Sonnet'e verildi."}],
}

REVIEW_1_MD = """# Son Kontrol Raporu — Yapılacaklar CLI

## Genel sonuç
**Kısmen başarılı.** Depolama katmanı, temel komutlar, testler ve README planla uyumlu ve doğrulandı.
Ancak planın SC4 ölçütü karşılanmıyor: `temizle` alt komutu yazılmamış. Diğer dört ölçüt testlerle kanıtlandı.
Eksik küçük ve iyi tanımlı; tek bir düzeltme göreviyle kapatılabilir.

## Başarı ölçütleri
| SC | Durum | Kanıt | Eksik |
|---|---|---|---|
| SC1 | Karşılandı | `tests/test_cli.py`: test_add_and_list geçti; uçtan uca testler (T06) geçti | — |
| SC2 | Karşılandı | `tests/test_store.py`: 7 test geçti, Türkçe metin testi dahil | — |
| SC3 | Karşılandı | test_done_and_remove ve test_invalid_number geçti | — |
| SC4 | Karşılanmadı | `todo/cli.py` içinde `temizle` alt komutu yok | `temizle` komutu ve testi |
| SC5 | Karşılandı | README.md kullanım örnekleri içeriyor | `temizle` belgelenmemiş |

## Modül modül durum
- **M1 Depolama:** Tamam. `clear_done()` depolamada var ve test ediliyor.
- **M2 Komut satırı:** `ekle`, `listele`, `bitir`, `sil` tamam; `temizle` eksik. Uçtan uca testler (T06) komutları
  gerçek bir süreçte doğruluyor.
- **M3 Belgeler:** Tamam.

## Plandan sapmalar
- T02: Sol'un ilk denemesi sıra numarası hatası yüzünden testlerden kaldı. Jev görevi notla Sonnet'e verdi; sapma yok.
- T04: Sonnet'in proje dışına yazma denemesi koruma tarafından engellendi; görev proje içinde tamamlandı.
- T05: Luna'nın kotası doldu; kural katmanı görevi Sonnet'e yönlendirdi.

## Açık sorunlar ve riskler
- `temizle` olmadan tamamlanan görevler listede birikir.

## Nasıl çalıştırılır
```powershell
python -m todo ekle "Süt al"
python -m todo listele
python -m unittest discover -s tests -v
```

## Önerilen sonraki adımlar
- G1 düzeltmesinden sonra README'ye `temizle` örneği eklenebilir.

## Düzeltme önerileri
- **G1 (SC4):** `todo/cli.py` içine `temizle` alt komutunu ekle; `TodoStore.clear_done()` kullan. `tests/test_cli.py` içine temizle testi ekle.
"""

REVIEW_2_MD = """# Son Kontrol Raporu — Yapılacaklar CLI

## Genel sonuç
**Başarılı.** Düzeltme turunda `temizle` alt komutu eklendi ve test edildi. Beş başarı ölçütünün hepsi
testlerle kanıtlandı. Önceki rapora göre değişen: SC4 artık karşılanıyor (D1-01).

## Başarı ölçütleri
| SC | Durum | Kanıt | Eksik |
|---|---|---|---|
| SC1 | Karşılandı | test_add_and_list ve uçtan uca testler (T06) geçti | — |
| SC2 | Karşılandı | `tests/test_store.py`: 7 test geçti | — |
| SC3 | Karşılandı | test_done_and_remove ve test_invalid_number geçti | — |
| SC4 | Karşılandı | test_clear_done geçti (D1-01) | — |
| SC5 | Karşılandı | README.md kullanım örnekleri içeriyor | — |

## Modül modül durum
- **M1 Depolama:** Tamam.
- **M2 Komut satırı:** Tamam; `temizle` eklendi.
- **M3 Belgeler:** Tamam.

## Plandan sapmalar
- Önceki turdaki sapmalar geçerli; bu turda yeni sapma yok.

## Açık sorunlar ve riskler
- Kayda değer risk yok.

## Nasıl çalıştırılır
```powershell
python -m todo ekle "Süt al"
python -m todo temizle
python -m unittest discover -s tests -v
```

## Önerilen sonraki adımlar
- İstenirse görevlere tarih ve öncelik alanları eklenebilir (kapsam dışıydı).

## Düzeltme önerileri
- Yok.
"""

CRIT_OK = "Testler geçti"
REVIEW = {
    "1": {
        "verdict": "kismen",
        "summary": "Dört ölçüt karşılandı; SC4 (temizle komutu) eksik.",
        "report_markdown": REVIEW_1_MD,
        "criteria": [
            {"id": "SC1", "status": "met", "evidence": "tests/test_cli.py test_add_and_list; tests/test_uctan_uca.py (T06)",
             "gap": None},
            {"id": "SC2", "status": "met", "evidence": "tests/test_store.py 7 test", "gap": None},
            {"id": "SC3", "status": "met", "evidence": "test_done_and_remove, test_invalid_number", "gap": None},
            {"id": "SC4", "status": "unmet", "evidence": "todo/cli.py içinde temizle yok",
             "gap": "temizle alt komutu ve testi"},
            {"id": "SC5", "status": "met", "evidence": "README.md", "gap": None},
        ],
        "gaps": [{"id": "G1", "criteria": ["SC4"], "description": "temizle alt komutu yazılmamış",
                  "suggested_fix": "cli.py'ye temizle alt komutunu ekle (TodoStore.clear_done) ve test yaz"}],
        "risks": ["Tamamlanan görevler listede birikir"],
        "next_steps": ["G1'i düzelt"],
    },
    "2": {
        "verdict": "basarili",
        "summary": "Beş ölçütün hepsi karşılandı; SC4 düzeltme turunda kapandı.",
        "report_markdown": REVIEW_2_MD,
        "criteria": [
            {"id": "SC1", "status": "met", "evidence": "test_add_and_list", "gap": None},
            {"id": "SC2", "status": "met", "evidence": "tests/test_store.py", "gap": None},
            {"id": "SC3", "status": "met", "evidence": "test_done_and_remove, test_invalid_number", "gap": None},
            {"id": "SC4", "status": "met", "evidence": "test_clear_done (D1-01)", "gap": None},
            {"id": "SC5", "status": "met", "evidence": "README.md", "gap": None},
        ],
        "gaps": [],
        "risks": [],
        "next_steps": ["İstenirse tarih ve öncelik alanları"],
    },
}

FIX = {
    "2": {
        "tasks": [task("D1-01", "temizle alt komutu", "M2", "code",
                       "todo/cli.py içine temizle alt komutunu ekle (TodoStore.clear_done kullan) ve "
                       "tests/test_cli.py içine test_clear_done testini ekle.",
                       ["python -m todo temizle tamamlanan görevleri siler", "test_clear_done geçer"],
                       ["python -m unittest tests.test_cli -v"],
                       ["todo/cli.py", "tests/test_cli.py"],
                       [], ["SC4"], "S", "sol", "G1", reads=["todo/cli.py", "tests/test_cli.py"],
                       contracts=["C1"])],
    },
}

SCENARIO = {
    "ad": "Yapılacaklar listesi CLI (varsayılan kuru koşu senaryosu)",
    "aciklama": "Kuru koşuda istekten bağımsız olarak bu plan kullanılır. Sahte işçiler şablonlardan gerçek "
                "dosyalar yazar; doğrulama komutları gerçekten çalışır.",
    "plan": {**PLAN, "tasks": TASKS},
    "workers": WORKERS,
    "brain": BRAIN,
    "review": REVIEW,
    "fix": FIX,
}

if __name__ == "__main__":
    OUT.write_text(json.dumps(SCENARIO, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"yazıldı: {OUT}")
