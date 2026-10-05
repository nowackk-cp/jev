"""[0] ÖLÇEKLEME: Jev işin boyunu (mini, kucuk, orta, buyuk) ve zorluğunu (kolay, orta, zor) ölçer. Mini ve küçük
işte plan atlanır: iş tek görevle (T01) yapılır; istekte açık kalan noktaları işçi kendisi seçer (SEÇİM satırları).

Karar sırası: --olcek bayrağı > istekteki açık ifade > Jev modeli > kelime kuralı. Bu modül ajan çağırmaz."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from .. import context, gitops, lanes, systemone
from ..config import DIFFICULTIES, DIFFICULTY_SIZE, DIFFICULTY_TR, SCALE_TR, SCALES
from ..state import scale_level
from ..util import (append_jsonl, atomic_write_json, atomic_write_text, ek, iso, jev_home, read_jsonl, shorten,
                    slugify)
from .common import init_task, task_event

SOURCE_TR = {"kullanici": "--olcek bayrağı", "ifade": "istekteki ifade", "jev": "Jev modeli", "kural": "kelime kuralı",
             "yukseltme": "kendiliğinden yükseltme", "plan": "planlayıcının planı",
             "opus": "beyin netleştirdi"}  # opus: eski koşular (ayrı netleştirme çağrısı kalktı)
HISTORY_FILE = "olcek_gecmisi.jsonl"
HARDER = {"kolay": "orta", "orta": "zor", "zor": "zor"}
SC1_STATEMENT = "İstek, kullanıcının beklediği alışılmış biçimde ve çalışır hâlde karşılandı."
SC1_VERIFICATION = "İşçinin doğrulama komutları Jev tarafından proje kökünde yeniden çalıştırılır."

SCALE_CRITERIA = {
    "mini": "Tek dosya ya da birkaç küçük dosya; tek ajan birkaç dakikada bitirir. Ör. basit bir hesap makinesi, "
            "tek bir betik, basit bir görsel ya da kısa bir belge; mevcut projede tek dosyalık küçük bir düzeltme.",
    "kucuk": "Birkaç dosyalık bir araç ya da değişiklik; plan gerekmez, tek ajan yapar. Ör. yapılacaklar listesi "
             "uygulaması, küçük bir komut satırı aracı, kısa bir animasyon videosu; mevcut projede küçük bir özellik.",
    "orta": "Birkaç modüllü bir iş; kısa bir plan ve birkaç görev gerekir. Ör. veritabanlı küçük bir web uygulaması, "
            "birkaç ekranlı bir oyun; mevcut projede birkaç modüle dokunan bir özellik.",
    "buyuk": "Kurumsal ya da üretim düzeyinde bir iş: çok modül, kimlik doğrulama, dağıtım, yüksek güvenilirlik; "
             "derin plan ve çok görev gerekir.",
}
DIFFICULTY_CRITERIA = {
    "kolay": "Bilinen, düz bir iş: herkesin nasıl yapılacağını bildiği türden.",
    "orta": "Biraz tasarım ya da birkaç parçanın uyumu gerekir.",
    "zor": "İnce mantık, algoritma, performans ya da çok parçalı bir entegrasyon gerekir.",
}


# --- metin yardımcıları -------------------------------------------------------------------

def lower_tr(text: str) -> str:
    """Türkçe küçük harf: İ → i, I → ı (str.lower 'İ'yi 'i̇' yapar)."""
    return (text or "").replace("İ", "i").replace("I", "ı").lower()


def upper_tr(text: str) -> str:
    return (text or "").replace("i", "İ").replace("ı", "I").upper()


def _has(low: str, phrase: str) -> bool:
    """Kelime başında eşleşme; sonuna ek gelebilir ("kurumsal" → "kurumsallaşmış")."""
    p = lower_tr(phrase).strip()
    return bool(p) and re.search(r"(?<!\w)" + re.escape(p), low) is not None


def phrase_hits(cfg, text: str, kind: str = "ifade") -> tuple[list[str], list[str]]:
    """[olcek] listelerinden istekte geçenler: (küçük yön, büyük yön). kind: ifade (dar liste) | kural (geniş)."""
    low = lower_tr(text)

    def hits(key: str) -> list[str]:
        return [p for p in cfg.get("olcek", key, default=[]) or [] if isinstance(p, str) and _has(low, p)]
    return hits(f"{kind}_mini"), hits(f"{kind}_buyuk")


def rule_level(cfg, text: str, is_new: bool) -> str:
    """Jev modeli yokken: yalnızca büyük yönde kelime → buyuk, yalnızca küçük yönde → mini; ikisi de ya da hiçbiri
    → yeni projede kucuk, mevcut projede orta."""
    m1, b1 = phrase_hits(cfg, text, "ifade")
    m2, b2 = phrase_hits(cfg, text, "kural")
    small, big = bool(m1 or m2), bool(b1 or b2)
    if big and not small:
        return "buyuk"
    if small and not big:
        return "mini"
    return "kucuk" if is_new else "orta"


def rule_difficulty(level: str) -> str:
    return {"mini": "kolay", "buyuk": "zor"}.get(level, "orta")


def pick_level(probs: dict[str, float], threshold: float) -> tuple[str, float]:
    """P(iş ≤ L) ≥ eşik olan en küçük L: en ucuz seviye, ama işin ondan büyük çıkma olasılığı düşükse."""
    vals = {lvl: max(0.0, float(probs.get(lvl, 0.0) or 0.0)) for lvl in SCALES}
    total = sum(vals.values())
    if total <= 0:
        return "buyuk", 1.0
    cum = 0.0
    for lvl in SCALES:
        cum += vals[lvl] / total
        if cum >= threshold - 1e-9:
            return lvl, round(min(cum, 1.0), 4)
    return "buyuk", 1.0


# --- proje ve öğrenme ----------------------------------------------------------------------

def project_info(run) -> dict:
    """Jev modeline giden proje bilgisi: yeni mi; mevcutsa dosya sayısı ve baskın uzantılar."""
    if run.state.get("new_project"):
        return {"proje": "yeni ve boş"}
    try:
        lines = [ln for ln in gitops.file_tree(run.project, 3000).splitlines() if ln.strip()]
    except gitops.GitError:
        lines = []
    more = 0
    if lines and lines[-1].startswith("… ve "):
        m = re.search(r"\d+", lines.pop())
        more = int(m.group(0)) if m else 0
    exts = Counter((PurePosixPath(f).suffix or PurePosixPath(f).name).lower() for f in lines)
    return {"proje": "mevcut", "dosya_sayisi": len(lines) + more,
            "baskin_uzantilar": [e for e, _ in exts.most_common(6)]}


def history_path():
    return jev_home() / HISTORY_FILE


def learning_on(run) -> bool:
    """Öğrenme açık mı: [olcek].ogrenme ve gerçek koşu (kuru koşu geçmişe yazmaz, geçmişten okumaz)."""
    return bool(run.cfg.get("olcek", "ogrenme", default=True)) and not run.state.get("dry_run")


def record_history(run, rec: dict) -> None:
    """Öğrenme kaydı (%USERPROFILE%\\.jev\\olcek_gecmisi.jsonl). tur: karar | yukseltme | sonuc."""
    if not learning_on(run):
        return
    try:
        append_jsonl(history_path(), {"run_id": run.run_id, "ts": iso(), **rec})
    except OSError as e:
        run.log("warn", f"Ölçek geçmişi yazılamadı: {e}")


def history_examples(n: int = 8) -> list[dict]:
    """Sonucu bilinen son n koşu: istek, ilk ölçek, zorluk, kaynak, sonuç, yükseltildi mi, düzeltme istendi mi."""
    try:
        rows = read_jsonl(history_path())
    except (OSError, ValueError):
        return []
    runs: dict[str, dict] = {}
    for r in rows:
        rid = r.get("run_id") if isinstance(r, dict) else None
        if not rid:
            continue
        cur = runs.setdefault(rid, {})
        kind = r.get("tur")
        if kind == "karar":
            cur.update(istek=shorten(str(r.get("istek") or ""), 160), olcek=r.get("olcek"), zorluk=r.get("zorluk"),
                       kaynak=r.get("kaynak"))
        elif kind == "yukseltme":
            cur["yukseltildi"] = (f"{SCALE_TR.get(r.get('onceki'), r.get('onceki') or '?')} → "
                                  f"{SCALE_TR.get(r.get('olcek'), r.get('olcek') or '?')}")
        elif kind == "sonuc":
            cur["sonuc"] = r.get("sonuc")
            if int(r.get("tur_no") or 1) > 1:
                cur["duzeltildi"] = True
    done = [e for e in runs.values() if e.get("istek") and e.get("sonuc")]
    return [{"istek": e["istek"], "olcek": e.get("olcek"), "zorluk": e.get("zorluk"), "kaynak": e.get("kaynak"),
             "sonuc": e["sonuc"], "yukseltildi": e.get("yukseltildi") or "",
             "duzeltildi": bool(e.get("duzeltildi"))} for e in done[-n:]]


# --- Jev modeline sorular -----------------------------------------------------------------

def questions(is_new: bool) -> dict:
    scope = "yeni bir proje" if is_new else "mevcut bir proje: projenin değil İSTENEN DEĞİŞİKLİĞİN boyunu ölç"
    return {
        "olcek": systemone.choice(
            "Jev bir yazılım ofisi: kodla üretilebilen her işi yapar (uygulama, betik, oyun, video, görsel, belge). "
            f"Bu istek ({scope}) ne büyüklükte bir iş? Aynı sonucu verecek en küçük seviyeyi düşün.", SCALE_CRITERIA),
        "zorluk": systemone.choice("Bu işin zorluğu ne?", DIFFICULTY_CRITERIA),
    }


def scenario(run) -> dict:
    """Kuru koşunun senaryosu (sahte adaptörden); gerçek koşuda ya da okunamazsa boş."""
    try:
        return run.gw.adapter("opus").scenario or {}
    except Exception:
        return {}


def dry_hint(run) -> dict:
    """Kuru koşu: sahte Jev modeline senaryonun ölçeği. Senaryoda ölçek yoksa büyük (mevcut uçtan uca testler
    bugünkü tam hattan geçer)."""
    sc = scenario(run)
    return {"olcek": sc.get("olcek") or "buyuk", "zorluk": sc.get("zorluk") or "orta"}


# --- aşama --------------------------------------------------------------------------------

def run_sizing(run) -> None:
    cfg, st = run.cfg, run.state
    request = st["request"]
    is_new = bool(st.get("new_project"))
    level = difficulty = source = p = None
    probs: dict[str, float] = {}
    if st.get("forced_scale") in SCALES:
        level, source = st["forced_scale"], "kullanici"
    small_hits, big_hits = phrase_hits(cfg, request)
    phrases = small_hits + big_hits
    if level is None and bool(small_hits) != bool(big_hits):
        level, source = ("mini" if small_hits else "buyuk"), "ifade"
    info = project_info(run)
    examples = history_examples() if learning_on(run) else []
    if run.jev.enabled:  # kullanıcı ya da ifade ölçeği belirlese de zorluk için sorulur
        run.emit("agent.state", {"agent": "jev", "state": "consulting", "task_id": None, "phase": "sizing",
                                 "text": "İşin boyunu ölçüyor", "until": None})
        jstate: dict = {"istek": request, "proje": info}
        if examples:
            jstate["gecmis"] = examples
        if small_hits and big_hits:
            jstate["ipuclari"] = phrases  # iki yönde de ifade var: karar modelin
        res = run.jev.ask("olcek", jstate, questions(is_new), hint=dry_hint(run) if st.get("dry_run") else None)
        ans = res.answers if res.ok else {}
        if res.ok and ans.get("olcek") and ans.get("zorluk"):
            probs = {lvl: round(float(ans["olcek"].probabilities.get(lvl, 0.0) or 0.0), 4) for lvl in SCALES}
            jev_level, cum = pick_level(probs, float(cfg.get("olcek", "emin_olma", default=0.75)))
            if ans["zorluk"].value in DIFFICULTIES:
                difficulty = ans["zorluk"].value
            if level is None:
                level, source, p = jev_level, "jev", cum
        else:
            run.log("warn", f"Jev modeli işin boyunu ölçemedi ({shorten(res.error_text or 'eksik cevap', 200)}); "
                            "kelime kuralı kullanılıyor.")
    if level is None:
        level, source = rule_level(cfg, request, is_new), "kural"
    if difficulty is None:
        difficulty = rule_difficulty(level)
    st["scale"] = {"level": level, "difficulty": difficulty, "source": source, "p": p, "probs": probs,
                   "phrases": phrases, "ts": iso()}
    if cfg.profile(level).get("plan_onayi") and not st.get("dry_run"):
        st["plan_approval"] = True  # kuru koşuda yalnızca --onay bayrağı geçerli (testler takılmasın)
    run.save()
    announce(run)
    record_history(run, {"tur": "karar", "istek": shorten(request, 300), "proje": info.get("proje"), "olcek": level,
                         "zorluk": difficulty, "kaynak": source, "p": p})
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": "sizing", "text": "",
                             "until": None})
    if cfg.skips_step(level, "plan"):
        setup_single_task(run)
        run.set_phase("executing")
    else:
        run.set_phase("planning")


# --- duyuru ve özet ------------------------------------------------------------------------

def source_label(sc: dict) -> str:
    src = sc.get("source")
    if src == "yukseltme":
        return f"{ek(SCALE_TR.get(sc.get('from') or 'mini', 'Mini'), 'den')} kendiliğinden yükseltme"
    return SOURCE_TR.get(src or "", src or "-")


def describe(st: dict) -> str:
    """'Mini · zorluk Kolay · kaynak: Jev modeli (%88)' (durum ekranı ve ofis)."""
    sc = st.get("scale") or {}
    lvl = sc.get("level") or "buyuk"
    txt = (f"{SCALE_TR.get(lvl, lvl)} · zorluk {DIFFICULTY_TR.get(sc.get('difficulty') or '', '-')} · kaynak: "
           f"{source_label(sc)}")
    if sc.get("p") is not None:
        txt += f" (%{round(float(sc['p']) * 100)})"
    return txt


def flow_note(cfg, level: str, difficulty: str, approval: bool) -> str:
    """Akışın tek satırlık özeti: terminal duyurusu ve ofis."""
    reviewer, backup = context.display(cfg.reviewer), cfg.backup_review(level)
    review = ("denetimi Jev yapar" if cfg.jev_review(level) else f"son kontrolü {reviewer} yapar"
              + (f" (kodun çoğunu {reviewer} yazdıysa {context.display(backup)})" if backup else ""))
    if cfg.skips_step(level, "plan"):
        who = " ya da ".join(context.display(a) for a in cfg.pool(difficulty)) or "uygun bir işçi"
        return f"plan yok; işi {who} tek başına yapar, {review}"
    return (f"{context.display(cfg.planner)} planı ve görev kartlarını yazar (en fazla {cfg.max_tasks(level)})"
            + (" ve onayına sunar" if approval else "") + f" → işçiler kartlarıyla çalışır → {review}")


def flow_phases(cfg, st: dict) -> list[str]:
    """Ofisin akış şeridi. Ölçeği olmayan eski koşu bugünkü tam hattan geçer."""
    head = ["sizing"] if st.get("scale") or st.get("phase") == "sizing" else []
    if st.get("scale") and cfg.skips_step(scale_level(st), "plan"):
        return head + ["executing", "reviewing", "reported"]
    return head + ["planning"] + (["awaiting_approval"] if st.get("plan_approval") else []) + [
        "executing", "reviewing", "reported"]


def announce(run) -> None:
    """Ölçek kararını duyurur (sorulmaz; kullanıcı --olcek ile baştan zorlayabilir)."""
    cfg, st = run.cfg, run.state
    sc = st["scale"]
    level, diff = sc["level"], sc["difficulty"]
    flow = flow_note(cfg, level, diff, bool(st.get("plan_approval")))
    run.emit("run.scale", {"level": level, "level_tr": SCALE_TR[level], "difficulty": diff,
                           "difficulty_tr": DIFFICULTY_TR[diff], "source": sc.get("source"),
                           "source_tr": source_label(sc), "p": sc.get("p"), "from": sc.get("from"), "flow": flow,
                           "phases": flow_phases(cfg, st)})
    how = f"%{round(float(sc['p']) * 100)}" if sc.get("p") is not None else source_label(sc)
    run.term.line(f"Jev: iş boyu {upper_tr(SCALE_TR[level])} ({how}) · zorluk {DIFFICULTY_TR[diff]} → {flow}", "cyan")


# --- tek görevli iş (mini, küçük) ---------------------------------------------------------------

def synthetic_plan(st: dict) -> dict:
    """Planlayıcının yazmadığı en küçük plan: rapor, ofis ve `düzelt` buna dayanır."""
    req = " ".join(st["request"].split())
    name = shorten(req, 60)
    name = upper_tr(name[:1]) + name[1:]
    rows = [f"# {name}", "", "## Özet", req, "", "## Ölçek", describe(st), "",
            "## Başarı ölçütü", f"- SC1: {SC1_STATEMENT} ({SC1_VERIFICATION})", "",
            "Plan yok: iş tek görevle (T01) yapılır."]
    return {
        "project_name": name, "slug": slugify(req, 40), "summary": shorten(req, 200),
        "plan_markdown": "\n".join(rows),
        "stack": {"language": "", "frameworks": [], "runtime_notes": ""},
        "commands": {"install": None, "test": None, "run": None, "lint": None},
        "modules": [{"id": "M1", "name": "İş", "responsibility": "İsteğin tamamı", "interfaces": "",
                     "depends_on": []}],
        "success_criteria": [{"id": "SC1", "statement": SC1_STATEMENT, "verification": SC1_VERIFICATION,
                              "command": None}],
        "assumptions": [], "out_of_scope": [], "risks": [], "conventions": "", "scale": None,
    }


def write_plan(run, plan: dict) -> None:
    st = run.state
    atomic_write_json(run.rdir / "plan.json", plan)
    atomic_write_text(run.rdir / "plan.md", plan["plan_markdown"].rstrip() + "\n")
    run.plan = plan
    st["project_name"] = plan["project_name"]
    run.emit("run.info", {"project_name": plan["project_name"], "summary": plan["summary"],
                          "request": st["request"], "branch": st.get("branch"), "dry_run": st.get("dry_run"),
                          "criteria": [c["id"] for c in plan["success_criteria"]],
                          "modules": [{"id": m["id"], "name": m["name"]} for m in plan["modules"]]})


def single_task(cfg, st: dict, tid: str = "T01", *, origin: str = "plan", round_: int = 1,
                description: str | None = None, guidance: str = "") -> dict:
    """Tek görev: işçi isteği baştan sona yapar; doğrulama komutlarını işçi verir, Jev yeniden çalıştırır.
    Aday işçiler zorluğun havuzundan ([olcek.havuz]); aralarından Jev seçer."""
    diff = (st.get("scale") or {}).get("difficulty")
    diff = diff if diff in DIFFICULTIES else "orta"
    req = " ".join(st["request"].split())
    if origin == "fix":
        title = "Düzeltme: " + shorten(" ".join((description or "").split()), 70)
    else:
        title = shorten(req, 80)
        title = upper_tr(title[:1]) + title[1:]
    task = init_task({
        "id": tid, "title": title, "module": "M1", "type": "code",
        "description": (description or req).strip(),
        "acceptance": [SC1_STATEMENT,
                       "İşçinin verdiği doğrulama komutları proje kökünde kendiliğinden bitiyor ve geçiyor."],
        "verify": [], "outputs": [], "depends_on": [], "covers_criteria": ["SC1"],
        "complexity": DIFFICULTY_SIZE[diff], "suggested_agent": None, "notes": "",
        "verify_from_worker": True, "aday_havuzu": cfg.pool(diff),
    }, origin=origin, round_=round_)
    task["guidance"] = guidance or ""
    return task


def setup_single_task(run) -> None:
    st = run.state
    write_plan(run, synthetic_plan(st))
    task = single_task(run.cfg, st)
    st["tasks"] = [task]
    run.save()
    run.emit("task.update", task_event(task))


# --- mini → küçük -----------------------------------------------------------------------------

def should_escalate(run, task: dict) -> bool:
    """Mini iş deneme hakkını bitirdi mi: beyne gitmeden küçüğe yükselir (kullanıcı kararı)."""
    return (scale_level(run.state) == "mini" and task.get("origin") == "plan"
            and int(task.get("attempt_count", 0)) >= run.cfg.attempts("mini"))


def escalate(run, task: dict, outcome: str, problem: str) -> None:
    """Mini iş olmadı: yarım iş ayrı bir dala kaydedilip geri alınır; iş küçük ölçekte ve bir üst zorlukta (daha
    güçlü işçi havuzu) yeni bir T01 ile baştan başlar. Beyin çağrılmaz."""
    cfg, st, project = run.cfg, run.state, run.project
    lanes.close_lane(run, task)  # mini tek sıralı çalışır; şerit olmaz, yine de ana projeye yalnızca buradan dokunulur
    branch = st.get("branch")
    cp = task.get("checkpoint") or st.get("base_commit")
    archive = None
    try:
        if cp and gitops.changes_since(project, cp)["all"]:
            archive = f"jev/{st['run_id']}-mini-deneme"
            gitops.git(["checkout", "-q", "-B", archive], project)
            gitops.commit_all(project, f"jev: {task['id']} mini denemesi (olmadı; iş küçüğe yükseltildi)")
            gitops.git(["checkout", "-q", "-f", branch], project)
    except gitops.GitError as e:
        archive = None
        run.log("warn", f"Mini denemesi ayrı dala kaydedilemedi: {shorten(str(e), 200)}")
    if cp:
        gitops.rollback(project, cp, branch)
    n = int(task.get("attempt_count", 0))
    st.setdefault("olcek_gecmisi", []).append({**task, "status": "escalated", "archived_level": "mini",
                                               "archive_branch": archive, "archived_at": iso()})
    sc = st.get("scale") or {}
    diff = HARDER.get(sc.get("difficulty") or "orta", "zor")
    st["scale"] = {**sc, "level": "kucuk", "difficulty": diff, "source": "yukseltme", "from": "mini", "p": None,
                   "ts": iso()}
    guidance = (f"Bu iş önce mini ölçekte {n} kez denendi ve olmadı (son sorun: "
                f"{context.OUTCOME_TR.get(outcome, outcome)} — {shorten(problem, 300)}). Yarım iş geri alındı; "
                "klasör işin başındaki hâlinde. İşi baştan yap ve doğrulamayı gerçekten çalıştır.")
    new = single_task(cfg, st, task["id"], guidance=guidance)
    new["failed_agents"] = list(task.get("failed_agents") or [])  # havuzda başka müsait ajan varsa o denenir
    st["tasks"] = [new if t.get("id") == task["id"] else t for t in st.get("tasks", [])]
    write_plan(run, synthetic_plan(st))
    run.save()
    run.term.warn(f"Mini iş {n} denemede olmadı: yarım iş geri alındı"
                  + (f" ({archive} dalında saklı)" if archive else "") + "; iş küçük ölçekte baştan başlıyor.")
    announce(run)
    run.emit("task.update", task_event(new))
    record_history(run, {"tur": "yukseltme", "olcek": "kucuk", "zorluk": diff, "onceki": "mini", "sorun": outcome})
