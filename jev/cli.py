"""jev komut satırı (§5.12).

    jev "istek" [--olcek mini|kucuk|orta|buyuk] [seçenekler]   yeni koşu     jev   etkileşimli mod
    jev devam|durum|rapor [koşu]                      jev duzelt [koşu] ["not"]
    jev ofis [koşu]   jev ajanlar [--test] [ajan…]    jev gecmis [N]

Çıkış kodları: 0 rapor/başarılı · 1 hata · 2 kullanım hatası · 3 duraklatıldı · 4 iptal · 130 Ctrl+C.
"""
from __future__ import annotations

import os
import re
import signal
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__, context
from .config import ConfigError, load_config
from .gitops import GitError
from .quota import QuotaBook
from .runner import (ACTIVE_PHASES, SetupError, _stdin_is_tty, create_run, load_run, print_status,
                     show_report)
from .state import PHASE_TR, LockError, find_run, index_all, jev_dir, load_state, run_dir
from .terminal import AGENT_COLORS, Terminal
from .util import ek, expand_path, jev_home, local_hhmm, parse_iso, pid_alive, read_json, shorten

SUBCOMMANDS = {
    "devam": "devam", "durum": "durum", "rapor": "rapor", "duzelt": "duzelt", "düzelt": "duzelt",
    "ofis": "ofis", "ajanlar": "ajanlar", "gecmis": "gecmis", "geçmiş": "gecmis",
    "yardim": "yardim", "yardım": "yardim", "help": "yardim",
}
EXIT_WORDS = {"çıkış", "cikis", "çikiş", "exit", "quit", "q"}
RUN_ID_RX = re.compile(r"^\d{8}(?:-[\w-]*)?$")  # tam ya da baştan kısaltılmış kimlik; boşluklu metin nottur
ROLE_TR = {"planlayici": "planlayıcı", "denetci": "denetçi", "beyin": "beyin", "eskalasyon": "eskalasyon",
           "isci": "işçi"}
LOGIN_ARGS = {"codex": "login", "claude": "auth login"}

HELP_TEXT = """\
jev — çok ajanlı yazılım ofisi
Jev önce işin boyunu ölçer. Mini ve küçük işi tek işçi doğrudan yapar. Orta ve büyük işte Opus planı ve görev
kartlarını yazar, Jev kartları dağıtır; Sol, Sonnet ve Luna kodlar (Opus yalnızca takılan görevde). Son kontrolden
sonra rapor yazılır ve akış durur.

Kullanım:
  jev "X için Y tasarla" [seçenekler]   Yeni koşu başlatır
  jev                                   Etkileşimli mod ("Ne tasarlayalım?")
  jev devam [koşu]                      Duraklatılmış ya da yarıda kalmış koşuyu sürdürür
  jev durum [koşu]                      Aşama, görevler, kota ve beyin çağrıları
  jev rapor [koşu]                      Son raporu gösterir
  jev duzelt [koşu] ["not"]             Rapordaki eksikler için düzeltme turu açar
  jev ofis [koşu]                       Ofis arayüzünü açar (bitmiş ya da durmuş koşular için de)
  jev ajanlar [--test] [ajan…]          Ajanlar, CLI'lar, kota ve kullanım; --test her ajana "OK" yazdırır
  jev gecmis [N]                        Son N koşu (varsayılan 20)

Seçenekler:
  --proje DİZİN, -p DİZİN   Mevcut bir projede çalış (verilmezse proje_koku altında yeni klasör açılır)
  --olcek SEVİYE            İşin boyunu sen belirle: mini, kucuk, orta, buyuk (verilmezse Jev ölçer)
  --onay                    Plan yazılınca onayını bekle (terminalde onayla/reddet ya da ofisteki düğmeler);
                            büyük işte kendiliğinden istenir, mini ve küçükte plan olmadığı için etkisizdir
  --kuru                    Sahte ajanlarla kuru koşu; kota harcanmaz
  --kuru-hiz N              Kuru koşu hız çarpanı (ör. 5); --kuru'yu da açar
  --ayrintili, -v           Ajanların anlık eylemlerini terminalde de göster
  --arayuz-yok              Ofis arayüzünü açma
  --bitince-cik             Rapordan ya da duraklamadan sonra komut bekleme, çık
  --surum                   Sürümü yaz
  --yardim, -h              Bu yardımı göster

Koşu kimliği verilmezse bulunulan klasördeki projenin son koşusu, o da yoksa en son koşu kullanılır.
Kimliğin başını yazmak yeterli (ör. jev durum 20260927-1432).

Koşu sırasında terminale:  durum · çıkış     (Ctrl+C yarım görevi geri alıp güvenle durdurur)
Rapordan sonra:            düzelt [not] · rapor · durum · ofis · çıkış
Duraklamada:               devam · durum · ofis · çıkış

Çıkış kodları: 0 rapor/başarılı · 1 hata · 2 kullanım hatası · 3 duraklatıldı · 4 iptal · 130 Ctrl+C
Ayarlar: %USERPROFILE%\\.jev\\jev.toml (JEV_HOME ortam değişkeniyle değiştirilebilir)
`jev` komutu bulunamazsa: py -m jev …"""


class UsageError(ValueError):
    """Hatalı komut satırı kullanımı (çıkış kodu 2)."""


@dataclass
class Args:
    positional: list[str] = field(default_factory=list)
    proje: str | None = None
    onay: bool = False
    kuru: bool = False
    kuru_hiz: float | None = None
    ayrintili: bool = False
    arayuz_yok: bool = False
    bitince_cik: bool = False
    test: bool = False
    surum: bool = False
    yardim: bool = False
    olcek: str | None = None


_FLAGS = {
    "--onay": "onay", "--kuru": "kuru", "--ayrintili": "ayrintili", "--ayrıntılı": "ayrintili", "-v": "ayrintili",
    "--arayuz-yok": "arayuz_yok", "--arayüz-yok": "arayuz_yok",
    "--bitince-cik": "bitince_cik", "--bitince-çık": "bitince_cik",
    "--test": "test", "--surum": "surum", "--sürüm": "surum", "--version": "surum", "-V": "surum",
    "-h": "yardim", "--help": "yardim", "--yardim": "yardim", "--yardım": "yardim",
}
_VALUED = {"--proje": "proje", "-p": "proje", "--kuru-hiz": "kuru_hiz", "--kuru-hız": "kuru_hiz",
           "--olcek": "olcek", "--ölçek": "olcek"}
SCALE_WORDS = {"mini": "mini", "kucuk": "kucuk", "küçük": "kucuk", "orta": "orta", "buyuk": "buyuk", "büyük": "buyuk"}


def parse_args(argv: list[str]) -> Args:
    """Elle ayrıştırma: `--x=değer` desteklenir, `--` sonrası her şey istek metnidir."""
    a = Args()
    i, only_positional = 0, False
    while i < len(argv):
        tok = argv[i]
        i += 1
        if only_positional or tok == "-" or not tok.startswith("-"):
            a.positional.append(tok)
            continue
        if tok == "--":
            only_positional = True
            continue
        name, eq, val = tok.partition("=")
        key = name.lower() if name.startswith("--") else name
        if key in _FLAGS:
            if eq:
                raise UsageError(f"{name} değer almaz.")
            setattr(a, _FLAGS[key], True)
            continue
        if key in _VALUED:
            if not eq:
                if i >= len(argv):
                    raise UsageError(f"{name} bir değer bekliyor.")
                val = argv[i]
                i += 1
            if _VALUED[key] == "kuru_hiz":
                try:
                    x = float(val.replace(",", "."))
                except ValueError:
                    raise UsageError(f"{name} bir sayı bekliyor (ör. 5).") from None
                if not 0 < x <= 1000:
                    raise UsageError(f"{name} 0'dan büyük ve en fazla 1000 olmalı.")
                a.kuru_hiz = x
            elif _VALUED[key] == "olcek":
                level = SCALE_WORDS.get(val.strip().replace("İ", "i").replace("I", "i").lower().replace("ı", "i"))
                if not level:
                    raise UsageError(f"{name} şunlardan biri olmalı: mini, küçük, orta, büyük.")
                a.olcek = level
            else:
                if not val.strip():
                    raise UsageError(f"{name} boş olamaz.")
                a.proje = val
            continue
        raise UsageError(f"Bilinmeyen seçenek: {tok}")
    return a


# --- giriş noktası ------------------------------------------------------------------------------

def run() -> None:
    """`jev` komutunun giriş noktası (pyproject ve `py -m jev`)."""
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break da Ctrl+C gibi güvenli durdursun
        try:
            signal.signal(signal.SIGBREAK, _break_to_interrupt)
        except (ValueError, OSError):
            pass
    code = main()
    _quiet_stdout()
    sys.exit(code)


def _break_to_interrupt(signum, frame):
    raise KeyboardInterrupt


def _quiet_stdout() -> None:
    """Çıktı bir boruya gidip boru erken kapandıysa (ör. `| head`) kapanışta gürültülü hata basılmasın."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            try:
                fd = os.open(os.devnull, os.O_WRONLY)
                os.dup2(fd, stream.fileno())
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    term = Terminal()
    try:
        a = parse_args(list(sys.argv[1:] if argv is None else argv))
    except UsageError as e:
        term.error(str(e))
        term.dim("Yardım için: jev --yardim")
        return 2
    term.verbose = a.ayrintili
    if a.surum:
        print(f"jev {__version__}")
        return 0
    sub = SUBCOMMANDS.get(a.positional[0].strip().lower()) if a.positional else None
    if sub in ("durum", "rapor", "gecmis") or (sub == "ajanlar" and not a.test):
        term.stamps = False  # liste çıktısı: saat damgası gürültü olur
    if a.yardim or sub == "yardim":
        print(HELP_TEXT)
        return 0
    try:
        cfg = load_config()
        if sub is None:
            return cmd_new(cfg, a, term)
        return HANDLERS[sub](cfg, a, term, a.positional[1:])
    except UsageError as e:
        term.error(str(e))
        term.dim("Yardım için: jev --yardim")
        return 2
    except (SetupError, ConfigError, LockError, FileNotFoundError, GitError) as e:
        term.error(str(e))
        return 1
    except KeyboardInterrupt:
        term.line("")
        term.warn("Durduruldu.")
        return 130


# --- yeni koşu ----------------------------------------------------------------------------------

def cmd_new(cfg, a: Args, term: Terminal) -> int:
    request = " ".join(a.positional).strip()
    if not request:
        if not _stdin_is_tty():
            print(HELP_TEXT)
            return 2
        term.header(f"jev {__version__} · yazılım ofisi")
        term.line("Jev işin boyunu ölçer: küçük işi tek işçi yapar; büyük işte Opus planı ve görev kartlarını yazar, "
                  "Jev dağıtır.", stamp=False)
        term.dim("Örnek: Python için birim dönüştürücü CLI tasarla   (çıkmak için boş bırak)")
        try:
            request = input("Ne tasarlayalım? ").strip()
        except EOFError:
            return 0
        if not request or request.lower() in EXIT_WORDS:
            return 0
        words = request.split()
        sub = SUBCOMMANDS.get(words[0].lower())
        if sub and (len(words) == 1 or sub == "duzelt"):  # "durum", "devam", "düzelt eksik testler" …
            if sub == "yardim":
                print(HELP_TEXT)
                return 0
            return HANDLERS[sub](cfg, a, term, words[1:])
    runner = create_run(cfg, request, project_arg=a.proje,
                        approval=a.onay or bool(cfg.get("plan_onayi", default=False)),
                        dry=a.kuru or a.kuru_hiz is not None, speed=a.kuru_hiz or 1.0, term=term,
                        scale=a.olcek, ui=not a.arayuz_yok, verbose=a.ayrintili, exit_after=a.bitince_cik)
    return _run_session(runner, term)


def _run_session(runner, term: Terminal, body: Callable[[], int] | None = None, intro: bool = True) -> int:
    """Kilidi alır, oturumu yürütür, her durumda kapatır. open() başarısızsa close() çağrılmaz:
    close() durumu kaydeder ve kilidi tutan başka bir sürecin state.json'unu ezebilir."""
    try:
        runner.open()
    except BaseException:
        runner.bus.close()
        raise
    try:
        if intro:
            _intro(runner, term)
        return body() if body else runner.session()
    except KeyboardInterrupt:
        term.line("")
        if runner.state.get("phase") in ACTIVE_PHASES:
            runner.interrupted()
        else:
            term.warn("Çıkılıyor. " + _resume_hint(runner.state))
        return 130
    finally:
        runner.close()


def _scenario_name(cfg) -> str | None:
    from .adapters.mock import load_scenario
    try:
        name = str(load_scenario(cfg.get("kuru", "senaryo", default="") or None).get("ad") or "")
    except (OSError, ValueError):
        return None
    return name.split(" (")[0].strip() or None


def _intro(runner, term: Terminal) -> None:
    st = runner.state
    term.header(f"jev · koşu {runner.run_id}")
    term.line(f"İstek: {shorten(st.get('request', ''), 200)}", stamp=False)
    term.line(f"Proje: {runner.project} · dal {st.get('branch')}", stamp=False)
    if st.get("dry_run"):
        term.line(f"KURU KOŞU: sahte ajanlar, kota harcanmaz (hız ×{_fmt_num(st.get('speed') or 1)}).",
                  "yellow", stamp=False)
        name = _scenario_name(runner.cfg)
        if name:
            term.dim(f"  Senaryo sabit: isteğin ne olursa olsun «{name}» kurulur.", stamp=False)
    if runner.url:
        term.line(f"Ofis: {runner.url}", "cyan", stamp=False)
    from .runner import _stdin_is_tty
    if _stdin_is_tty():  # komut satırı yalnızca gerçek bir konsolda okunur
        term.dim("Koşu sırasında: durum · çıkış   (Ctrl+C güvenle durdurur)")


def _resume_hint(st: dict) -> str:
    ph = st.get("phase")
    rid = st.get("run_id")
    if ph == "reported":
        return f"Koşu kaydedildi; düzeltme için: jev duzelt {rid} \"not\" · izlemek için: jev ofis {rid}"
    if ph == "aborted":
        return "Koşu iptal edilmişti."
    return f"Koşu kaydedildi; sürdürmek için: jev devam {rid}"


def _fmt_num(x) -> str:
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:g}"


# --- koşu seçimi --------------------------------------------------------------------------------

def _single_id(rest: list[str], cmd: str) -> str | None:
    if len(rest) > 1:
        raise UsageError(f"`jev {cmd}` en fazla bir koşu kimliği alır. Yeni bir istek başlatmak istiyorsan "
                         f"isteği tırnak içinde yaz: jev \"…\"")
    return rest[0] if rest else None


def _split_run_and_note(rest: list[str]) -> tuple[str | None, str]:
    """`jev duzelt [koşu] ["not"]`: ilk sözcük bir koşu kimliğiyse koşu, gerisi not."""
    if not rest:
        return None, ""
    first = rest[0].strip()
    if RUN_ID_RX.match(first) or first in index_all():
        return first, " ".join(rest[1:]).strip()
    return None, " ".join(rest).strip()


def _resolve(a: Args, run_id: str | None) -> tuple[Path, str]:
    cwd = expand_path(a.proje).resolve() if a.proje else Path.cwd()
    return find_run(run_id, cwd)


def _live_owner(project: Path) -> dict | None:
    """Projenin kilidini tutan canlı (başka) bir jev süreci varsa kilit bilgisi."""
    info = read_json(jev_dir(project) / "lock", {}) or {}
    try:
        pid = int(info.get("pid", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    if pid and pid != os.getpid() and pid_alive(pid):
        return info
    return None


def _busy_error(term: Terminal, owner: dict, rid: str, st: dict, hint: str = "") -> int:
    term.error(f"Bu projede başka bir jev süreci çalışıyor (PID {owner.get('pid')}, koşu {owner.get('run_id')}).")
    if owner.get("run_id") == rid and st.get("ui_url"):
        term.line(f"Ofis: {st['ui_url']}", "cyan", stamp=False)
    if hint:
        term.dim(hint)
    return 1


# --- alt komutlar -------------------------------------------------------------------------------

def cmd_devam(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    project, rid = _resolve(a, _single_id(rest, "devam"))
    st = load_state(run_dir(project, rid))
    owner = _live_owner(project)
    if owner:
        return _busy_error(term, owner, rid, st, "O süreçte `devam` yaz ya da ofisteki Devam düğmesini kullan.")
    if st.get("phase") == "aborted":
        term.error("Bu koşu iptal edilmiş; devam ettirilemez. Yeni bir koşu başlat.")
        return 4
    runner = load_run(cfg, project, rid, term=term, ui=not a.arayuz_yok, verbose=a.ayrintili,
                      exit_after=a.bitince_cik)
    if a.kuru_hiz is not None:
        if runner.state.get("dry_run"):
            runner.state["speed"] = a.kuru_hiz
            runner.gw.speed = a.kuru_hiz
        else:
            term.warn("--kuru-hiz yalnızca kuru koşularda geçerli; yok sayıldı.")

    def body() -> int:
        if runner.state.get("phase") in ACTIVE_PHASES:
            runner.mark_crashed()
        if not runner.prepare_resume():
            return 4
        return runner.session(drive_first=True)

    return _run_session(runner, term, body)


def cmd_duzelt(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    rid, note = _split_run_and_note(rest)
    project, rid = _resolve(a, rid)
    st = load_state(run_dir(project, rid))
    owner = _live_owner(project)
    if owner:
        return _busy_error(term, owner, rid, st,
                           "O süreçte `düzelt [not]` yaz ya da ofisteki Düzelt düğmesini kullan.")
    runner = load_run(cfg, project, rid, term=term, ui=not a.arayuz_yok, verbose=a.ayrintili,
                      exit_after=a.bitince_cik)

    def body() -> int:
        from .phases.fix import start_fix
        if runner.state.get("phase") in ACTIVE_PHASES:
            runner.mark_crashed()
        ph = runner.state.get("phase")
        if ph == "paused":
            term.warn("Koşu duraklatılmış; önce `jev devam` ile raporu tamamla, sonra düzelt.")
            return 3
        if ph == "aborted":
            term.error("Bu koşu iptal edilmiş; düzeltme turu açılamaz.")
            return 4
        ok = start_fix(runner, note)
        return runner.session(drive_first=ok)

    return _run_session(runner, term, body)


def cmd_durum(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    project, rid = _resolve(a, _single_id(rest, "durum"))
    rdir = run_dir(project, rid)
    st = load_state(rdir)
    owner = _live_owner(project)
    live = bool(owner and owner.get("run_id") == rid)
    quota = QuotaBook(rdir / "kuru-kota.json" if st.get("dry_run") else None)
    print_status(term, st, cfg, quota, st.get("ui_url") if live else None, running=live)
    ph = st.get("phase")
    if live:
        term.dim(f"Bu koşu şu an çalışıyor (PID {owner.get('pid')}).")
    elif ph in ACTIVE_PHASES:
        term.warn("Koşu etkin görünüyor ama çalışan bir jev süreci yok (süreç beklenmedik şekilde kapanmış). "
                  f"Sürdürmek için: jev devam {rid}")
    elif ph == "paused":
        term.dim(f"Sürdürmek için: jev devam {rid}")
    elif ph == "reported":
        term.dim(f"Rapor: jev rapor {rid} · düzeltme: jev duzelt {rid} \"not\"")
    return 0


def cmd_rapor(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    project, rid = _resolve(a, _single_id(rest, "rapor"))
    rdir = run_dir(project, rid)
    return 0 if show_report(term, rdir, load_state(rdir)) else 1


def cmd_ofis(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    if a.arayuz_yok:
        raise UsageError("`jev ofis` ile --arayuz-yok birlikte kullanılamaz.")
    project, rid = _resolve(a, _single_id(rest, "ofis"))
    st = load_state(run_dir(project, rid))
    owner = _live_owner(project)
    if owner:
        if owner.get("run_id") == rid and st.get("ui_url"):
            term.info(f"Koşu başka bir jev sürecinde açık; ofis: {st['ui_url']}")
            if cfg.get("arayuz", "tarayici_ac", default=True):
                try:
                    webbrowser.open(st["ui_url"])
                except Exception:
                    pass
            return 0
        return _busy_error(term, owner, rid, st)
    runner = load_run(cfg, project, rid, term=term, ui=True, open_browser=True, verbose=a.ayrintili)
    runner.ui = True  # ayarda arayüz kapalı olsa da `jev ofis` açar (tarayıcı ise tarayici_ac ayarına uyar)

    def body() -> int:
        if runner.server is None:
            term.error("Ofis arayüzü başlatılamadı.")
            return 1
        if runner.state.get("phase") in ACTIVE_PHASES:
            runner.mark_crashed()
        term.header(f"jev ofis · koşu {rid}")
        term.line(f"İstek: {shorten(runner.state.get('request', ''), 200)}", stamp=False)
        term.line(f"Ofis: {runner.url}", "cyan", stamp=False)
        term.dim("Kapatmak için: çıkış ya da Ctrl+C")
        return runner.session(drive_first=False)

    return _run_session(runner, term, body, intro=False)


def cmd_ajanlar(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    names = list(cfg.agents)
    wanted = [r.strip().lower() for r in rest if r.strip()]
    unknown = [w for w in wanted if w not in names]
    if unknown:
        raise UsageError(f"Bilinmeyen ajan: {', '.join(unknown)} (bilinenler: {', '.join(names)})")
    sel = wanted or names
    cool = QuotaBook().all()
    term.header("Ajanlar")
    for n in sel:
        ag = cfg.agent(n)
        roles = ", ".join(ROLE_TR.get(r, r) for r in ag.get("roller", []))
        name = term.c(f"{context.display(n):<7}", AGENT_COLORS.get(n, "bold"))
        term.line(f"{name} {cfg.label(n):<20} {cfg.provider(n):<7} {roles}", stamp=False)
        efor = ag.get("efor") or {}
        if efor:
            term.dim("        efor: " + " · ".join(f"{k}={v}" for k, v in efor.items()))
        if n in cool:
            rec = cool[n]
            term.line(f"        kota dolu: ~{ek(local_hhmm(parse_iso(rec.get('until'))), 'e')} kadar"
                      + (f" ({shorten(rec.get('reason', ''), 80)})" if rec.get("reason") else ""), "yellow",
                      stamp=False)
    from .config import SCALE_TR, SCALES
    by_agent = [x for x in SCALES if not cfg.jev_review(x)]
    if by_agent:
        rv, backed = context.display(cfg.reviewer), [SCALE_TR[x].lower() for x in by_agent if cfg.backup_review(x)]
        term.line(f"Son kontrol ({', '.join(SCALE_TR[x].lower() for x in by_agent)} iş): {rv} · "
                  + (f"yedek denetçi: {context.display(cfg.backup_reviewer)} ({', '.join(backed)} işte kodun çoğunu "
                     f"{rv} yazdıysa)" if backed else "yedek denetçi kapalı"), stamp=False)
    fb_agent, fb_effort = cfg.fallback_brain
    term.line(f"Jev'in beyni: {context.display(cfg.brain)} · yedek beyin: {context.display(fb_agent)} "
              f"(efor {fb_effort})", stamp=False)
    from .systemone import SystemOne
    jm = SystemOne(cfg)
    term.line(f"Jev modeli (TypeSafe): {jm.status()}", "green" if jm.enabled else "yellow", stamp=False)
    if jm.enabled:
        term.dim(f"        kararı Jev verir; görev bölme ve düzeltme metnini ve emin olmadığı kararları "
                 f"{context.display(cfg.brain)} yazar · dağıtım: {'Jev' if jm.dispatch else 'kural'} · "
                 f"güven eşiği {jm.threshold:.2f}")
    elif jm.on:
        term.dim(f"        anahtar için: setx {jm.key_env} \"<anahtar>\" (console.typesafe.ai); o zamana dek "
                 "kararı beyin verir")

    if not wanted:
        _scale_table(cfg, term)

    from .discovery import cli_version, find_cli
    term.header("CLI'lar")
    for p in sorted({cfg.provider(n) for n in names}):
        info = find_cli(p, cfg.get("cli", p, default="auto") or "auto")
        if info.path:
            ver = cli_version(info)
            term.line(f"{p:<7} {ver or 'sürüm okunamadı'} · {info.path} ({info.source})", stamp=False)
        else:
            term.line(f"{p:<7} bulunamadı — ayarlarda [cli] {p} = \"tam yol\" verebilirsin", "red", stamp=False)

    totals = _usage_totals()
    term.header("Kullanım (kuru koşular hariç)")
    if not totals:
        term.dim("Henüz gerçek bir koşu kullanımı yok.")
    for n in names:
        u = totals.get(n)
        if u:
            term.line(f"{context.display(n):<7} {u['calls']} çağrı · {_fmt_tokens(u['input_tokens'])} girdi · "
                      f"{_fmt_tokens(u['output_tokens'])} çıktı token · {_fmt_duration(u['duration_s'])}",
                      stamp=False)
    ju = totals.get("jev")
    if ju and not wanted:
        term.line(f"{'Jev':<7} {ju['calls']} çağrı · {_fmt_tokens(ju['input_tokens'])} girdi token · "
                  f"~${ju['input_tokens'] * 0.042 / 1_000_000:.4f} (TypeSafe Jev modeli)", stamp=False)
    if a.test:
        return _agent_test(cfg, a, term, sel)
    return 0


def _scale_table(cfg, term: Terminal) -> None:
    """[olcek.*] profilleri: işin boyu akışı, efor tavanlarını ve süreleri belirler (büyük = bugünkü tam hat).
    Tavan yoksa adımın ajan tablosundaki eforu yazılır; işçi ve beyin için "tablo" (ajanın kendi tablosu)."""
    from .config import DIFFICULTY_TR, SCALE_TR, SCALES

    def step(lvl: str, key: str, agent: str, effort_key: str) -> str:
        if cfg.skips_step(lvl, key):
            return "yok"
        return cfg.effort(agent, effort_key, cfg.step_cap(lvl, key)) or "-"

    term.header("Ölçekler (Jev işin boyunu ölçer; --olcek ile sen de seçebilirsin)")
    term.dim(f"{'':<6} {'plan, kart':<12} {'işçi S/M/L':<16} {'beyin':<7} {'denetim':<13} "
             f"{'deneme':<7} {'doğrulama':<10} {'paralel':<8} onay", stamp=False)
    reviewer = context.display(cfg.reviewer)
    for lvl in SCALES:
        prof = cfg.profile(lvl)
        review = "Jev" if cfg.jev_review(lvl) else f"{reviewer} " + step(lvl, "denetim", cfg.reviewer, "denetim")
        if not cfg.jev_review(lvl) and cfg.backup_review(lvl):
            review += "*"
        caps = [cfg.worker_cap(lvl, x) for x in ("S", "M", "L")]
        caps_txt = "tablo" if not any(caps) else "/".join(c or "tablo" for c in caps)
        plan = step(lvl, "plan", cfg.planner, "plan")
        if plan != "yok":
            plan += f" ≤{cfg.max_tasks(lvl)}"
        term.line(f"{SCALE_TR[lvl]:<6} {plan:<12} {caps_txt:<16} "
                  f"{cfg.brain_cap(lvl) or 'tablo':<7} {review:<13} {cfg.attempts(lvl):<7} "
                  f"{str(cfg.verify_timeout_s(lvl) // 60) + ' dk':<10} {cfg.parallel(lvl):<8} "
                  f"{'evet' if prof.get('plan_onayi') else '-'}", stamp=False)
    pools = " · ".join(f"{DIFFICULTY_TR[d].lower()}: " + ", ".join(context.display(a) for a in cfg.pool(d))
                       for d in ("kolay", "orta", "zor"))
    term.dim(f"Tek görevli işte (mini, küçük) işçi zorluğa göre seçilir: {pools}", stamp=False)
    term.dim("tablo: tavan yok, ajanın kendi efor tablosu geçerli · Jev gerektiğinde eforu tavanın üstüne çıkarabilir",
             stamp=False)
    if any(not cfg.jev_review(x) and cfg.backup_review(x) for x in SCALES):
        backup = context.display(cfg.backup_reviewer)
        term.dim(f"* yedek denetçi: kodun çoğunu {reviewer} yazdıysa son kontrolü {backup} yapar; {backup} soğumadaysa "
                 f"{reviewer}", stamp=False)


def _usage_totals() -> dict[str, dict]:
    totals: dict[str, dict] = {}
    for rid, rec in index_all().items():
        if rec.get("dry") or not rec.get("project"):
            continue
        data = read_json(run_dir(Path(rec["project"]), rid) / "usage.json", {}) or {}
        for ag, u in data.items():
            if not isinstance(u, dict):
                continue
            t = totals.setdefault(ag, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "duration_s": 0.0})
            t["calls"] += int(u.get("calls", 0) or 0)
            t["input_tokens"] += int(u.get("input_tokens", 0) or 0)
            t["output_tokens"] += int(u.get("output_tokens", 0) or 0)
            t["duration_s"] += float(u.get("duration_s", 0) or 0)
    return totals


def _fmt_tokens(n: int) -> str:
    n = int(n or 0)
    if n >= 999_950:  # yuvarlanınca "1000.0k" yazılmasın
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_duration(s: float) -> str:
    s = int(round(s or 0))
    if s >= 3600:
        return f"{s // 3600} sa {s % 3600 // 60} dk"
    if s >= 60:
        return f"{s // 60} dk {s % 60} sn"
    return f"{s} sn"


def _agent_test(cfg, a: Args, term: Terminal, sel: list[str]) -> int:
    """Her ajana en düşük eforla "Yalnızca OK yaz" gönderir (§5.12)."""
    from .events import EventBus
    from .gateway import Gateway
    dry = a.kuru or a.kuru_hiz is not None
    tdir = jev_home() / "ajan-testi" / time.strftime("%Y%m%d-%H%M%S")
    tdir.mkdir(parents=True, exist_ok=True)
    state = {"run_id": "ajan-testi", "round": 1, "call_seq": 0, "tasks": [], "dry_run": dry}
    bus = EventBus("ajan-testi", None)
    quota = QuotaBook(tdir / "kuru-kota.json") if dry else QuotaBook()
    gw = Gateway(cfg, rdir=tdir, project=tdir, state=state, bus=bus, quota=quota, dry=dry,
                 speed=a.kuru_hiz or 5.0)
    if a.ayrintili:
        def show(ev: dict) -> None:
            if ev["type"] == "agent.activity":
                d = ev["data"]
                term.dim(f"    {context.display(d.get('agent') or '?')} · {d.get('kind')}: "
                         f"{shorten(d.get('text') or '', 150)}")
        bus.add_listener(show)
    term.header("Ajan testi" + (" (kuru)" if dry else ""))
    if not dry:
        term.dim("Her ajana en düşük eforla tek satırlık bir deneme mesajı gidiyor (az miktarda kota harcar).")
    prompt = "Bu bir bağlantı denemesidir. Yalnızca OK yaz; başka hiçbir şey yazma, hiçbir araç kullanma."
    ok_count = 0
    try:
        for n in sel:
            label = f"{context.display(n)} ({cfg.label(n)}, efor {cfg.effort(n, 'test')})"
            term.line(f"{label} deneniyor…")
            res = gw.call(n, "test", prompt, readonly=True, effort=cfg.effort(n, "test"), timeout_key="test")
            took = f"{res.duration_s:.1f} sn" if res.duration_s else "-"
            tokens = res.usage or {}
            tok = f"{_fmt_tokens(tokens.get('input_tokens', 0))}/{_fmt_tokens(tokens.get('output_tokens', 0))} token"
            if res.ok:
                ok_count += 1
                answer = shorten((res.text or "").strip().replace("\n", " "), 60) or "(boş yanıt)"
                term.ok(f"{context.display(n):<7} {took:>8} · {tok} · yanıt: {answer}")
                continue
            kind = res.error_kind
            if kind == "quota":
                why = "kota dolu" + (f" (~{ek(local_hhmm(res.reset_at), 'de')} açılır)" if res.reset_at else "")
            elif kind == "auth":
                why = f"giriş gerekli — terminalde `{_login_hint(cfg, cfg.provider(n))}` çalıştır"
            elif kind == "timeout":
                why = "zaman aşımı"
            elif kind == "cancelled":
                why = "iptal edildi"
            else:
                why = shorten((res.error_text or kind or "bilinmeyen hata").strip().replace("\n", " "), 160)
            term.error(f"{context.display(n):<7} {took:>8} · {why}")
    finally:
        bus.close()
    total = len(sel)
    (term.ok if ok_count == total else term.warn)(f"{ok_count}/{total} ajan yanıt verdi. Kayıtlar: {tdir}")
    return 0 if ok_count == total else 1


def _login_hint(cfg, provider: str) -> str:
    """Giriş komutu. CLI PATH'te değilse PowerShell'de doğrudan çalışan tam yollu biçim."""
    from .discovery import find_cli
    args = LOGIN_ARGS.get(provider)
    if not args:
        return "giriş"
    info = find_cli(provider, cfg.get("cli", provider, default="auto") or "auto")
    if info.path and info.source != "PATH":
        return f'& "{info.path}" {args}'
    return f"{provider} {args}"


def cmd_gecmis(cfg, a: Args, term: Terminal, rest: list[str]) -> int:
    n = 20
    if rest:
        if len(rest) > 1 or not rest[0].isdigit() or int(rest[0]) < 1:
            raise UsageError("Kullanım: jev gecmis [N]  (N pozitif bir sayı)")
        n = int(rest[0])
    idx = index_all()
    if not idx:
        term.info("Henüz koşu yok. Yeni koşu için: jev \"istek\"")
        return 0
    items = sorted(idx.items(), key=lambda kv: kv[1].get("created") or kv[0], reverse=True)[:n]
    term.header(f"Son {len(items)} koşu" if len(idx) > len(items) else f"Koşular ({len(items)})")
    for rid, rec in items:
        ph = rec.get("phase")
        proj = rec.get("project") or ""
        exists = bool(proj) and run_dir(Path(proj), rid).exists()
        tags = []
        if rec.get("dry"):
            tags.append("kuru")
        rnd = int(rec.get("round", 1) or 1)
        if rnd > 1:
            tags.append(f"tur {rnd}")
        phase_tr = PHASE_TR.get(ph, ph or "?")
        color = {"reported": "green", "paused": "yellow", "aborted": "red"}.get(ph)
        if exists and ph in ACTIVE_PHASES:
            if _live_owner(Path(proj)):
                phase_tr += " (çalışıyor)"
            else:
                phase_tr += " (yarıda kaldı → jev devam)"
                color = "yellow"
        term.line(f"{rid}  " + term.c(phase_tr, color) if color else f"{rid}  {phase_tr}", stamp=False)
        term.dim(f"    {shorten(rec.get('request', ''), 90)}" + (f"  [{' · '.join(tags)}]" if tags else ""))
        term.dim(f"    {proj}" + ("" if exists else "  (silinmiş)"))
    return 0


HANDLERS: dict[str, Callable[..., int]] = {
    "devam": cmd_devam, "durum": cmd_durum, "rapor": cmd_rapor, "duzelt": cmd_duzelt, "ofis": cmd_ofis,
    "ajanlar": cmd_ajanlar, "gecmis": cmd_gecmis,
}
