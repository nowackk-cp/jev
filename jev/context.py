"""Bağlam yönetimi (belirtim §5.11, §8): rol promptları, görev paketi, AGENTS.md/CLAUDE.md, progress.md."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import DIFFICULTY_TR, SCALE_TR
from .machine import catalog, environment, environment_text
from .state import scale_level
from .util import append_jsonl, jev_home, shorten, tail_lines

PROMPT_DIR = Path(__file__).parent / "prompts"
MARK_BEGIN = "<!-- JEV:BASLA -->"
MARK_END = "<!-- JEV:BITIR -->"
DISPLAY = {"opus": "Opus", "sol": "Sol", "sonnet": "Sonnet", "luna": "Luna", "jev": "Jev"}
OUTCOME_TR = {
    "done": "başarılı", "verify_failed": "doğrulama başarısız", "agent_failed": "ajan başarısız dedi",
    "agent_blocked": "ajan ilerleyemedi (blocked)", "timeout": "zaman aşımı", "crash": "süreç hatası",
    "guard": "koruma durdurdu", "schema": "geçersiz çıktı", "quota": "kota doldu", "cancelled": "durduruldu",
    "auth": "oturum kapalı", "no_diff": "değişiklik yok", "merge_conflict": "birleştirme çakışması", "missing_outputs": "beklenen dosyalar yok",
    "audit": "denetim sorunu", "transient": "geçici hata", "not_accepted": "Jev kabul etmedi",
    "refusal": "güvenlik reddi",
}
DECISION_TR = {"retry": "tekrar dene", "reassign": "başka ajana ver", "split": "böl", "revise": "görevi düzelt",
               "skip": "atla", "pause": "duraklat", "abort": "iptal et"}

# --- şablonlar ----------------------------------------------------------------------------

_BLOCK = re.compile(r"\{\{#(\w+)\}\}\n?(.*?)\{\{/\1\}\}\n?", re.S)
_VAR = re.compile(r"\{\{(\w+)\}\}")


def template(name: str) -> str:
    """Kullanıcının %USERPROFILE%\\.jev\\prompts\\<ad>.md dosyası varsa o, yoksa paketteki şablon."""
    user = jev_home() / "prompts" / f"{name}.md"
    p = user if user.exists() else PROMPT_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8-sig")


def render(text: str, values: dict) -> str:
    """`{{ad}}` yer tutucularını doldurur. `{{#ad}}…{{/ad}}` bölümü yalnızca değer doluysa kalır."""
    def block(m: re.Match) -> str:
        return m.group(2) if values.get(m.group(1)) else ""

    text = _BLOCK.sub(block, text)
    return _VAR.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def prompt(name: str, values: dict) -> str:
    return render(template(name), values).strip() + "\n"


def display(agent: str) -> str:
    return DISPLAY.get(agent, agent.capitalize())


def sen_kimsin(agent: str) -> str:
    """"Sol'sun", "Sonnet'sin", "Luna'sın": Türkçe ünlü uyumuna göre."""
    name = display(agent)
    vowels = [c for c in name.lower() if c in "aıoueiöü"]
    last = vowels[-1] if vowels else "e"
    suffix = {"a": "sın", "ı": "sın", "o": "sun", "u": "sun", "e": "sin", "i": "sin", "ö": "sün", "ü": "sün"}[last]
    return f"{name}'{suffix}"


# --- ortam, biçim ve ölçek ----------------------------------------------------------------

SCALE_NOTE = {"mini": "tek dosya ya da birkaç küçük dosya; tek ajan birkaç dakikada bitirir",
              "kucuk": "birkaç dosyalık bir araç; plansız, tek ajan",
              "orta": "birkaç modüllü bir iş; kısa plan, az sayıda görev",
              "buyuk": "kurumsal ya da üretim düzeyinde bir iş; derin plan"}
SELF_TERMINATING = ("Doğrulama komutları kendiliğinden bitmeli ve birkaç saniyede sonuç vermeli: pencere açmamalı, "
                    "sunucu başlatıp beklememeli, girdi beklememeli. Arayüzü açarak değil, mantığını test ederek doğrula.")
INSTALL_RULE = ("Gereken bir araç kurulu değilse önce kurulu olanlarla çöz; olmuyorsa proje klasörüne yerel kur (ör. "
                "npm install, python -m pip install --target); o da olmuyorsa winget ile sessiz ve kullanıcı kapsamında "
                "kur (winget install --id <kimlik> -e --silent --scope user --accept-package-agreements "
                "--accept-source-agreements). Kurduğun her şeyi notes alanına "
                "\"KURULUM: <araç> — <nasıl>\" satırıyla yaz.")
TEST_RULE = {"kolay": "Bir iki basit ve hızlı kontrol yaz ve çalıştır (ör. mantık dosyası için küçük bir test betiği).",
             "orta": "Ana davranışları kapsayan kısa testler yaz ve çalıştır.",
             "zor": "Ana davranışların yanında zor durumları da test et: sınır değerler, hatalı girdi, hata yolları."}


def format_rule() -> str:
    """Alışılmış biçim kuralı: kodla üretilebilen her iş için beklenen çıktı biçimi."""
    e = environment()
    ui = "arayüz gerektiren araç → tek bir HTML sayfası (mantık ayrı bir .js dosyasında" + (
        "; tarayıcıda ve Node'da çalışır, Node ile test edilir)" if e["node"] else ")")
    video = "video → onu üreten betik ve .mp4 dosyası" + ("" if e["ffmpeg"] else " (ffmpeg kurulu değil; gerekirse kur)")
    return ("İsteğin herkesin beklediği ALIŞILMIŞ BİÇİMİNİ üret (ör. hesap makinesi → tuş takımlı arayüz, form değil). "
            "İstekte biçim belirtilmişse o geçerli; mevcut projede projenin dili ve yapısı geçerli. Değilse: " + ui
            + " · arayüz gerektirmeyen iş → Python betiği · " + video + " · görsel → .png ya da .svg · "
            "belge → .md (istenirse .pdf ya da .docx).")


def quality_rule() -> str:
    """Kalite kuralı: gerçek kaynak, doğru içerik, görsel öz denetim. Ortak kaynak kataloğu varsa önce o okunur."""
    cat = catalog()
    first = (f"Hazır veri, yazı tipi, ikon, paket ya da araç tarifi gerekiyorsa önce ortak kaynak kataloğunu oku "
             f"({cat}); internetten indirmeden önce orada ara. " if cat else "")
    return (
        "KALİTE: Sonuç, alanının iyi bir uzmanının elinden çıkmış gibi olmalı; işin gerektirdiği ölçüde (basit bir "
        "hesap makinesi dış kaynak istemez). " + first + "Ezberden doğru üretemeyeceğin şeyleri elle yaklaşık "
        "uydurma; gerçek ve açık lisanslı kaynak kullan: harita ve sınırlar → gerçek coğrafi veri (ör. Natural Earth, "
        "tarihî sınır verisi) ve tutarlı bir izdüşüm, elle çizilmiş kıta ya da sınır değil · ikon → açık ikon setleri "
        "· yazı tipi → Türkçe karakterleri destekleyen açık fontlar · grafik, animasyon, PDF → olgun kütüphaneler. "
        "Kullandığın veri ve varlıkları projeye kopyala (sonuç internete ya da ortak klasörlere bağlı kalmasın); "
        "kaynağını ve lisansını KAYNAKLAR.md dosyasına yaz. Tarih, sayı ve isimlerde doğruluğa özen göster. Görsel "
        "çıktıyı kendin gözden geçir: videodan birkaç kareyi, sayfanın ekran görüntüsünü ya da PDF sayfalarını %TEMP% "
        "altında PNG'ye çıkarıp bak; yanlış, taşan, bozuk ya da okunmayan yeri düzelt. Kaynağa erişemiyorsan sessizce "
        "kaba bir taklit üretme; durumu notes alanına yaz.")


def worker_quality_rule() -> str:
    """Kart işçisinin kalite kuralı: kaynak ve veri kararlarını planlayıcı karta yazdı; işçi uygular ve kendi
    çıktısına bakar. Katalog yolu ortam satırında (yalnızca görsel, video, harita ve belge işlerinde okunur)."""
    return ("KALİTE: Sonuç, alanının iyi bir uzmanının elinden çıkmış gibi olsun. Ezberden doğru üretemeyeceğin şeyi "
            "elle yaklaşık uydurma: kartın gösterdiği gerçek kaynağı kullan, projeye kopyala, kaynağını ve lisansını "
            "KAYNAKLAR.md dosyasına yaz. Tarih, sayı ve isimlerde doğruluğa özen göster. Görsel bir çıktı ürettiysen "
            "kendin bak (sayfanın ekran görüntüsünü, videodan birkaç kareyi ya da PDF sayfalarını %TEMP% altında PNG'ye "
            "çıkar) ve yanlış, taşan, bozuk ya da okunmayan yeri düzelt. Kaynağa erişemiyorsan sessizce kaba bir taklit "
            "üretme; durumu notes alanına yaz.")


def scale_text(state: dict) -> str:
    sc = state.get("scale") or {}
    lvl = scale_level(state)
    txt = f"{SCALE_TR.get(lvl, lvl)} ({SCALE_NOTE.get(lvl, '')})"
    if sc.get("difficulty"):
        txt += f" · zorluk {DIFFICULTY_TR.get(sc['difficulty'], sc['difficulty'])}"
    return txt + ". Çözümü, dosya sayısını ve belgeleri işin boyuyla orantılı tut."


def project_state_text(is_new: bool) -> str:
    return "yeni ve boş bir proje (Jev bir git deposu açtı)" if is_new else "mevcut bir proje; önce yapısını incele"


# --- plan parçaları ----------------------------------------------------------------------

def stack_text(plan: dict) -> str:
    s = plan.get("stack") or {}
    parts = [s.get("language") or ""]
    if s.get("frameworks"):
        parts.append(", ".join(s["frameworks"]))
    if s.get("runtime_notes"):
        parts.append(s["runtime_notes"])
    return " · ".join(p for p in parts if p)


def module_section(plan: dict, module_id: str | None) -> str:
    for m in plan.get("modules", []):
        if m.get("id") == module_id:
            deps = ", ".join(m.get("depends_on") or []) or "yok"
            return (f"{m['id']} {m.get('name', '')}: {m.get('responsibility', '')} "
                    f"(arayüz: {m.get('interfaces', '') or '-'}; bağımlılıklar: {deps})")
    return module_id or "-"


def criteria_text(plan: dict, ids: list[str] | None = None) -> str:
    rows = []
    for c in plan.get("success_criteria", []):
        if ids is not None and c.get("id") not in ids:
            continue
        cmd = f"; komut: {c['command']}" if c.get("command") else ""
        rows.append(f"- {c['id']}: {c.get('statement', '')} (doğrulama: {c.get('verification', '')}{cmd})")
    return "\n".join(rows) or "yok"


def bullet(items: list[str] | None, empty: str = "yok") -> str:
    items = [str(i) for i in items or [] if str(i).strip()]
    return "\n".join(f"- {i}" for i in items) if items else empty


# --- deneme geçmişi ---------------------------------------------------------------------

def attempt_history(task: dict, tail: int = 40, max_attempts: int = 4) -> str:
    rows = []
    for a in (task.get("attempts") or [])[-max_attempts:]:
        dur = a.get("duration_s")
        dur_txt = f"{dur / 60:.1f} dk" if isinstance(dur, (int, float)) else "?"
        rows.append(f"- Deneme {a.get('n')} · {display(a.get('agent', '?'))} · {dur_txt} · "
                    f"sonuç: {OUTCOME_TR.get(a.get('outcome'), a.get('outcome'))}")
        if a.get("guidance"):
            rows.append(f"  Bu denemeye verilen not: {shorten(a['guidance'], 400)}")
        if a.get("summary"):
            rows.append(f"  Ajanın özeti: {shorten(a['summary'], 600)}")
        if a.get("problem"):
            rows.append(f"  Sorun: {shorten(a['problem'], 600)}")
        if a.get("diffstat"):
            rows.append(f"  Değişiklik: {a['diffstat']}")
        for v in a.get("verify") or []:
            mark = "GEÇTİ" if v.get("passed") else "KALDI"
            rows.append(f"  Doğrulama: `{v.get('command')}` → {mark}")
            if not v.get("passed") and v.get("output_tail"):
                rows.append("  Çıktı sonu:\n" + _indent(tail_lines(v["output_tail"], tail)))
        if a.get("error"):
            rows.append("  Hata:\n" + _indent(tail_lines(a["error"], tail)))
    return "\n".join(rows)


def _indent(text: str, pad: str = "    ") -> str:
    return "\n".join(pad + ln for ln in text.splitlines())


# --- ilerleme, bağlı kartlar ve plan belgesi ----------------------------------------------

def append_progress(rdir: Path, task: dict, agent: str, files: list[str], summary: str) -> None:
    p = rdir / "progress.md"
    new = not p.exists()
    with p.open("a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write("# İlerleme\n\n")
        f.write(f"## {task['id']} · {task.get('title', '')} [{display(agent)}]\n")
        if files:
            f.write("- Dosyalar: " + ", ".join(files[:20]) + (" …" if len(files) > 20 else "") + "\n")
        f.write(f"- Özet: {shorten(summary, 500)}\n\n")


NEED_TR = {"research": "araştırma", "data": "veri", "website": "web sitesi", "tool": "araç", "resource": "hazır kaynak"}
_DEP_MARK = {"done": "", "skipped": " [atlandı: bu iş yapılmadı]"}


def deps_text(state: dict, task: dict, max_chars: int = 4000) -> str:
    """Kartın doğrudan bağlı olduğu kartlarda yapılanlar (özet ve dosyalar). İşçi yalnızca bunları bilir; projenin
    geri kalanını taramaz."""
    by_id = {t["id"]: t for t in state.get("tasks", [])}
    rows = []
    for d in task.get("depends_on") or []:
        t = by_id.get(d)
        if t is None:
            continue
        mark = _DEP_MARK.get(t.get("status"), f" [{t.get('status')}]")
        line = f"- {t['id']} {t.get('title', '')}{mark}: {shorten(t.get('summary') or 'özet yok', 400)}"
        files = t.get("files") or []
        if files:
            line += f" (dosyalar: {', '.join(files[:10])}{' …' if len(files) > 10 else ''})"
        rows.append(line)
    text = "\n".join(rows)
    return text if len(text) <= max_chars else text[:max_chars] + "\n…"


def contract_text(plan: dict, ids: list[str] | None) -> str:
    """Kartın sözleşmeleri: Jev plandaki tanımları karta olduğu gibi koyar; işçi planı açmaz."""
    by_id = {c.get("id"): c for c in plan.get("contracts") or []}
    rows = []
    for i in ids or []:
        c = by_id.get(i)
        if c is None:
            continue
        body = str(c.get("definition") or "").strip()
        head = f"- {i} {c.get('name', '')}".rstrip() + ":"
        rows.append(head + "\n" + _indent(body) if "\n" in body else f"{head} {body}")
    return "\n".join(rows)


def plan_document(plan: dict, tasks: list[dict] | None = None) -> str:
    """plan.md: planlayıcının düzyazısına Jev JSON alanlarından modülleri, ölçütleri, sözleşmeleri, ihtiyaçları ve
    (verilirse) kartları ekler; planlayıcı bunları ikinci kez yazmaz. Sözleşmesiz eski planlar olduğu gibi kalır."""
    md = (plan.get("plan_markdown") or f"# {plan.get('project_name') or 'Plan'}").rstrip()
    if "contracts" not in plan:
        return md + "\n"
    out = [md]

    def section(title: str, rows: list[str]) -> None:
        if rows:
            out.extend(["", f"## {title}", *rows])

    section("Varsayımlar", [f"- {x}" for x in plan.get("assumptions") or []])
    section("Kapsam dışı", [f"- {x}" for x in plan.get("out_of_scope") or []])
    section("Modüller", [f"- **{m.get('id')} {m.get('name', '')}**: {m.get('responsibility', '')}"
                         + (f" · arayüz: {m['interfaces']}" if m.get("interfaces") else "")
                         + (f" · bağımlı: {', '.join(m['depends_on'])}" if m.get("depends_on") else "")
                         for m in plan.get("modules") or []])
    section("Başarı ölçütleri", [f"- **{c.get('id')}** {c.get('statement', '')} · doğrulama: "
                                 f"{c.get('verification', '')}" + (f" · komut: `{c['command']}`" if c.get("command") else "")
                                 for c in plan.get("success_criteria") or []])
    section("Sözleşmeler", [contract_text(plan, [c.get("id") for c in plan["contracts"]])] if plan["contracts"] else [])
    section("İhtiyaçlar", [f"- {NEED_TR.get(n.get('kind'), n.get('kind'))}: {n.get('what', '')}"
                           + (f" → {n['task']}" if n.get("task") else "") for n in plan.get("needs") or []])
    cmds = plan.get("commands") or {}
    section("Komutlar", [f"- {k}: `{v}`" for k, v in (("kurulum", cmds.get("install")), ("test", cmds.get("test")),
                                                      ("çalıştırma", cmds.get("run")), ("lint", cmds.get("lint"))) if v])
    section("Riskler", [f"- {x}" for x in plan.get("risks") or []])
    section("Görev kartları", [
        f"- **{t['id']}** {t.get('title', '')} · {t.get('type', '')} · {t.get('complexity', '')}"
        + (f" · bağımlı: {', '.join(t['depends_on'])}" if t.get("depends_on") else "")
        + (f" · ölçütler: {', '.join(t['covers_criteria'])}" if t.get("covers_criteria") else "")
        + (f" · öneri: {display(t['suggested_agent'])}" if t.get("suggested_agent") else "")
        for t in tasks or []])
    return "\n".join(out).rstrip() + "\n"


# --- paketler ---------------------------------------------------------------------------

def plan_prompt(agent: str, request: str, project: Path, is_new: bool, tree: str, state: dict | None = None, *,
                max_tasks: int = 12, prev_errors: list[str] | None = None, prev_output: str = "") -> str:
    """Planlayıcının tek çağrısı: ihtiyaçlar, kısa plan, sözleşmeler ve görev kartları. Yeni projede klasör
    taranmaz (dosya ağacı yok). Onarımda hata listesi ve önceki çıktı eklenir."""
    st = state or {}
    lvl = scale_level(st)
    return prompt("plan", {
        "sen_kimsin": sen_kimsin(agent), "istek": request.strip(), "proje_yolu": str(project),
        "proje_durumu": project_state_text(is_new), "dosya_agaci": "" if is_new else tree,
        "mevcut_proje": not is_new, "ortam": environment_text(), "olcek": scale_text(st),
        "bicim_kurali": format_rule(), "komut_kurali": SELF_TERMINATING, "kalite_kurali": quality_rule(),
        "tam_plan": lvl == "buyuk", "kisa_plan": lvl != "buyuk", "en_fazla_gorev": max_tasks,
        "onceki_hatalar": bullet(prev_errors, "") if prev_errors else "", "onceki_cikti": prev_output or "(yok)"})


def mini_prompt(cfg, state: dict, task: dict, agent: str, rdir: Path, project: Path, tree: str = "",
                guidance: str = "") -> str:
    """Tek görevli iş (mini, küçük): plan ve başka ajan yok; işçi isteği baştan sona yapar, gerekirse kendisi
    araştırır ve açık kalan noktalarda kendisi seçer (SEÇİM satırları)."""
    sc = state.get("scale") or {}
    fix = task.get("origin") == "fix"
    return prompt("isci_mini", {
        "sen_kimsin": sen_kimsin(agent), "model": cfg.agents[agent]["model"], "istek": state["request"].strip(),
        "duzeltme": task.get("description", "") if fix else "", "tur": task.get("round", 1),
        "olcek": scale_text(state), "ortam": environment_text(), "proje_yolu": str(project),
        "proje_durumu": project_state_text(not tree.strip()), "dosya_agaci": tree, "gorev_id": task["id"],
        "deneme_gecmisi": attempt_history(task), "jev_notu": guidance or "", "bicim_kurali": format_rule(),
        "test_kurali": TEST_RULE.get(sc.get("difficulty") or "orta", TEST_RULE["orta"]),
        "kurulum_kurali": INSTALL_RULE, "komut_kurali": SELF_TERMINATING, "kalite_kurali": quality_rule()})


def worker_prompt(cfg, state: dict, plan: dict, task: dict, agent: str, rdir: Path, guidance: str = "",
                  workdir: Path | None = None) -> str:
    """Görev kartı: işçi yalnızca kartı ve kartta adı geçen dosyaları okur; planı açmaz, klasörü taramaz. Sözleşme
    tanımlarını ve doğrudan bağlı kartların özetini Jev karta koyar. `workdir`: paralel görevin kendi çalışma kopyası
    (verilirse işçiye aynı anda çalışma kuralı eklenir)."""
    limit = int(cfg.get("sinirlar", "paket_karakter", default=60000) or 60000)
    crit = task.get("covers_criteria") or []
    values = {
        "sen_kimsin": sen_kimsin(agent), "model": cfg.agents[agent]["model"],
        "proje_adi": plan.get("project_name", ""), "plan_ozeti": plan.get("summary", ""),
        "stack": stack_text(plan) or "-", "test_komutu": (plan.get("commands") or {}).get("test") or "yok",
        "modul_bolumu": module_section(plan, task.get("module")),
        "gorev_id": task["id"], "baslik": task.get("title", ""), "aciklama": task.get("description", ""),
        "notlar": task.get("notes") or "", "okunacaklar": bullet(task.get("reads"), ""),
        "outputs": ", ".join(task.get("outputs") or []) or "belirtilmedi",
        "sozlesmeler": contract_text(plan, task.get("contracts")), "onceki_isler": deps_text(state, task),
        "ilgili_olcutler": criteria_text(plan, crit) if crit else "",
        "kabul_listesi": bullet(task.get("acceptance")), "verify_listesi": bullet(task.get("verify")),
        "arastirma": task.get("type") == "research", "deneme_gecmisi": attempt_history(task),
        "jev_notu": guidance or "", "ortam": environment_text(), "komut_kurali": SELF_TERMINATING,
        "kurulum_kurali": INSTALL_RULE, "kalite_kurali": worker_quality_rule(),
        "paralel": workdir is not None, "calisma_klasoru": str(workdir or ""),
    }
    text = prompt("isci_gorev", values)
    # sınır aşılırsa önce bağlı kartların özeti, sonra deneme geçmişi kısaltılır
    if len(text) > limit:
        values["onceki_isler"] = deps_text(state, task, max_chars=1500)
        text = prompt("isci_gorev", values)
    if len(text) > limit:
        values["deneme_gecmisi"] = attempt_history(task, tail=15, max_attempts=2)
        text = prompt("isci_gorev", values)
    if len(text) > limit:
        text = text[:limit] + "\n…(paket sınırı nedeniyle kısaltıldı)\n"
    return text


def graph_summary(state: dict) -> str:
    rows = []
    for t in state.get("tasks", []):
        deps = ",".join(t.get("depends_on") or []) or "-"
        rows.append(f"{t['id']}[{t.get('status')}; bağımlı: {deps}; ajan: {t.get('agent') or '-'}]")
    return " ".join(rows)


def dependents_of(state: dict, task_id: str) -> list[str]:
    return [t["id"] for t in state.get("tasks", []) if task_id in (t.get("depends_on") or [])
            and t.get("status") not in ("split",)]


def task_public(task: dict) -> dict:
    keys = ("id", "title", "module", "type", "description", "reads", "outputs", "contracts", "acceptance", "verify",
            "depends_on", "covers_criteria", "complexity", "suggested_agent", "notes")
    return {k: task.get(k) for k in keys}


def brain_prompt(cfg, state: dict, plan: dict, task: dict, problem: str, rdir: Path, available: list[str],
                 cooling: dict[str, str]) -> str:
    max_att = cfg.attempts(scale_level(state))
    used = int(task.get("attempt_count", 0))
    left_att = max(0, max_att - used)
    left_brain = max(0, int(cfg.limit("kosu_basina_beyin_cagrisi")) - int(state.get("brain_calls", 0)))
    logs = [a.get("call") for a in task.get("attempts") or [] if a.get("call")]
    log_paths = [str(rdir / "calls" / f"{c}.stdout.jsonl") for c in logs[-3:]]
    log_paths += [str(rdir / "events.jsonl"), str(rdir / "decisions.jsonl")]
    deps = dependents_of(state, task["id"])
    return prompt("jev_karar", {
        "plan_ozeti": plan.get("summary", ""), "modul_bolumu": module_section(plan, task.get("module")),
        "ilgili_olcutler": criteria_text(plan, task.get("covers_criteria") or []),
        "gorev_json": json.dumps(task_public(task), ensure_ascii=False), "sorun_turu": problem,
        "deneme_gecmisi": attempt_history(task, tail=150) or "yok", "graf_ozeti": graph_summary(state),
        "bagimlilar": ", ".join(deps) or "yok",
        "musait_ajanlar": ", ".join(available) or "yok",
        "sogumadakiler": ", ".join(f"{a} (~{u})" for a, u in cooling.items()) or "yok",
        "kalan_deneme": left_att, "kalan_beyin": left_brain, "log_yollari": ", ".join(log_paths),
        "gorev_id": task["id"], "hak_bitti": "evet" if left_att <= 0 else "",
    })


def task_digest(state: dict, with_summary: bool = True) -> str:
    rows = []
    for t in state.get("tasks", []):
        line = f"- {t['id']} [{t.get('status')}] {t.get('title', '')} · ajan: {display(t['agent']) if t.get('agent') else '-'}"
        if t.get("covers_criteria"):
            line += f" · ölçütler: {', '.join(t['covers_criteria'])}"
        rows.append(line)
        if with_summary and t.get("summary"):
            rows.append(f"  {shorten(t['summary'], 300)}")
    return "\n".join(rows) or "yok"


def decisions_digest(rdir: Path) -> str:
    from .util import read_jsonl
    rows = []
    for d in read_jsonl(rdir / "decisions.jsonl"):
        rows.append(f"- {d.get('task_id')}: {d.get('decision')}"
                    + (f" → {d.get('agent')}" if d.get("agent") else "")
                    + f" (sapma: {d.get('deviation', 'none')}; karar veren: {d.get('brain', '?')}) — "
                    + shorten(d.get("rationale") or "", 300))
    return "\n".join(rows) or "Karar gerekmedi."


def verify_digest(results: list[dict]) -> str:
    rows = []
    for r in results:
        mark = "GEÇTİ" if r.get("passed") else "KALDI"
        rows.append(f"- [{r.get('source', '')}] `{r.get('command')}` → {mark} (çıkış kodu {r.get('exit_code')})")
        if not r.get("passed") and r.get("output_tail"):
            rows.append(_indent(tail_lines(r["output_tail"], 30)))
    return "\n".join(rows) or "Çalıştırılacak test ya da doğrulama komutu yok."


def review_prompt(cfg, agent: str, plan: dict, state: dict, rdir: Path, results: list[dict], diffstat: str,
                  diff: str = "", frames: list[dict] | None = None, previous_report: str = "") -> str:
    """Denetçinin kanıt paketi: istek, plan, kart özetleri, kararlar, Jev'in doğrulama sonuçları, değişikliklerin
    metni ve sonucun kareleri. Dosya ağacı yok: denetçi klasörü taramaz, gerekirse birkaç belirli dosyayı okur."""
    return prompt("denetim", {
        "sen_kimsin": sen_kimsin(agent), "model": cfg.agents[agent]["model"], "istek": state["request"].strip(),
        "plan": plan_document(plan), "gorev_ozetleri": task_digest(state), "kararlar": decisions_digest(rdir),
        "secimler": bullet(choice_lines(state), ""), "kurulumlar": bullet(install_lines(state), ""),
        "test_sonuclari": verify_digest(results), "diff_stat": diffstat or "değişiklik yok", "diff": diff or "",
        "dal": state.get("branch", ""), "kareler": bullet([f"{f['path']} — {f['what']}" for f in frames or []], ""),
        "onceki_rapor": previous_report, "olcek": scale_text(state)})


_NOTE_LINE = re.compile(r"^\s*[-*]?\s*(KURULUM|ÇALIŞTIRMA|CALISTIRMA|SEÇİM|SECIM)\s*:\s*(.+?)\s*$", re.I | re.M)


def _note_kind(word: str) -> str:
    """Not satırının türü: k (kurulum), s (seçim), c (çalıştırma)."""
    w = word[:1].lower()
    return w if w in ("k", "s") else "c"


def note_lines(notes: str | None, kind: str) -> list[str]:
    """İşçi notlarındaki "KURULUM: …", "ÇALIŞTIRMA: …" ya da "SEÇİM: …" satırları (kind: kurulum | calistirma |
    secim)."""
    want = _note_kind(kind)
    return [m.group(2) for m in _NOTE_LINE.finditer(notes or "") if _note_kind(m.group(1)) == want]


def install_lines(state: dict) -> list[str]:
    """İşçi notlarındaki kurulumlar (tekrarsız, sırayla). Olmayan denemeler ve mini'den yükseltilen iş de sayılır:
    sisteme kurulan araç geri almada kaldırılmaz."""
    out: list[str] = []
    for t in state.get("tasks", []) + [x for x in state.get("olcek_gecmisi") or [] if isinstance(x, dict)]:
        notes = [a.get("worker_notes") for a in t.get("attempts") or [] if isinstance(a, dict)]
        for n in notes + [t.get("worker_notes")]:
            for line in note_lines(n, "KURULUM"):
                if line not in out:
                    out.append(line)
    return out


def choice_lines(state: dict) -> list[str]:
    """İşçilerin "SEÇİM: …" satırları: istekte ya da kartta açık kalan noktalarda verdikleri kararlar (birden çok
    kart varsa kart kimliğiyle)."""
    tasks = state.get("tasks", [])
    out: list[str] = []
    for t in tasks:
        for line in note_lines(t.get("worker_notes"), "secim"):
            row = f"{t['id']}: {line}" if len(tasks) > 1 else line
            if row not in out:
                out.append(row)
    return out


def run_lines(task: dict) -> list[str]:
    """Görevin işçi notlarındaki "ÇALIŞTIRMA: …" satırları (kullanıcı sonucu nasıl açar ya da çalıştırır)."""
    return note_lines(task.get("worker_notes"), "calistirma")


def fix_prompt(agent: str, plan: dict, state: dict, report: dict, note: str, git_log: str, fix_no: int) -> str:
    unmet = [c for c in report.get("criteria", []) if c.get("status") != "met"]
    gaps = {"gaps": report.get("gaps", []), "karsilanmayan_olcutler": unmet}
    return prompt("duzelt", {
        "sen_kimsin": sen_kimsin(agent), "plan": plan_document(plan),
        "rapor_markdown": report.get("report_markdown", ""), "gaps_json": json.dumps(gaps, ensure_ascii=False),
        "kullanici_notu": note or "yok", "gorev_ozetleri": task_digest(state), "git_log": git_log or "-",
        "tur": fix_no})


# --- AGENTS.md / CLAUDE.md --------------------------------------------------------------------

def agents_md_section(plan: dict) -> str:
    cmds = plan.get("commands") or {}
    cmd_rows = [f"- {k}: `{v}`" for k, v in (("kurulum", cmds.get("install")), ("test", cmds.get("test")),
                                            ("çalıştırma", cmds.get("run")), ("lint", cmds.get("lint"))) if v]
    return "\n".join([
        f"# {plan.get('project_name', 'Proje')}", "",
        plan.get("summary", ""), "",
        "## Yığın", stack_text(plan) or "-", "",
        "## Komutlar (Windows PowerShell)", *(cmd_rows or ["- (henüz yok)"]), "",
        "## Kodlama kuralları", plan.get("conventions") or "-", "",
        "## Çok ajanlı çalışma kuralları (Jev)",
        "- Bu proje Jev adlı bir orkestratörle, birden çok yapay zekâ ajanı tarafından yazılıyor.",
        "- Yalnızca sana verilen görevi yap; başka görevlerin işine girme.",
        "- Git durumunu değiştirme: commit, reset, checkout, switch, rebase, push, clean, stash yasak. Commit'i Jev atar.",
        "- `.jev/` ve `.git/` klasörlerine yazma. Proje klasörünün dışına yazma (geçici dosyalar için %TEMP% serbest).",
        "- Bitirmeden önce testleri ve görevin doğrulama komutlarını çalıştır.",
        "- Komutlar Windows PowerShell 5.1'de çalışır. Doğrulama komutları kendiliğinden bitmeli: pencere açmamalı,"
        " sunucu başlatıp beklememeli, girdi beklememeli.",
        "- Görevin sonunda verilen JSON şemasına uyan bir sonuç ver.",
    ]).strip() + "\n"


def upsert_section(path: Path, section: str) -> bool:
    """Dosyada JEV bölümünü ekler ya da günceller. Değiştiyse True."""
    block = f"{MARK_BEGIN}\n{section.rstrip()}\n{MARK_END}\n"
    old = path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""
    if MARK_BEGIN in old and MARK_END in old:
        a = old.index(MARK_BEGIN)
        b = old.index(MARK_END) + len(MARK_END)
        rest = old[b:]
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n"):
            rest = rest[1:]
        new = old[:a] + block + rest
    elif old.strip():
        new = old.rstrip() + "\n\n" + block
    else:
        new = block
    if new == old:
        return False
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True


def write_shared_context(project: Path, plan: dict) -> list[str]:
    changed = []
    if upsert_section(project / "AGENTS.md", agents_md_section(plan)):
        changed.append("AGENTS.md")
    if upsert_section(project / "CLAUDE.md", "@AGENTS.md"):
        changed.append("CLAUDE.md")
    return changed


def log_decision(rdir: Path, rec: dict) -> None:
    append_jsonl(rdir / "decisions.jsonl", rec)
