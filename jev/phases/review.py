"""[4] SON KONTROL: Jev tüm doğrulama komutlarını çalıştırır. Orta ve büyük işte denetçi (varsayılan Sol 6.1) kanıt
paketiyle denetleyip raporu yazar: plan, kart özetleri, Jev'in doğrulama sonuçları, değişikliklerin metni ve sonucun
kareleri (video, sayfa, PDF). Denetçi klasörü taramaz. Kimse kendi işini denetlemesin: büyük işte kodun çoğunu denetçi
kendisi yazdıysa son kontrolü yedek denetçi (varsayılan Opus) yapar; yedek soğumadaysa denetçi. Mini ve küçük işte ayrı
denetçi çağrılmaz: raporu Jev kendi doğrulamasından ve kabul sorusundan yazar.

Rapordan sonra akış DURUR (kural 7). Düzeltme turu yalnızca kullanıcı "düzelt" deyince başlar (fix.py).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import webbrowser
from collections import Counter
from pathlib import Path, PurePosixPath

from .. import context, gitops, machine
from ..config import SCALE_TR
from ..state import STATUS_TR, scale_level
from ..util import atomic_write_json, atomic_write_text, iso, read_json, read_jsonl, shorten
from ..verify import run_command
from . import sizing
from .common import Refused, Unavailable, call_fixed, refusal_pause

VERDICT_TR = {"basarili": "Başarılı", "kismen": "Kısmen başarılı", "basarisiz": "Başarısız"}
CRIT_TR = {"met": "karşılandı", "partial": "kısmen", "unmet": "karşılanmadı", "unverified": "doğrulanamadı"}
# İş bitince açılan sonuç: yalnızca çalıştırılmayan dosyalar (program ve betik asla başlatılmaz). Sıra: önce HTML,
# sonra video, görsel, PDF, ses.
OPEN_EXT = {".html": 0, ".htm": 0, ".mp4": 1, ".webm": 1, ".mov": 1, ".gif": 2, ".png": 2, ".jpg": 2, ".jpeg": 2,
            ".webp": 2, ".svg": 2, ".pdf": 3, ".mp3": 4, ".wav": 4, ".ogg": 4}
SKIP_DIRS = {"node_modules", "coverage", "htmlcov", ".git", ".jev", "__pycache__", ".venv", "venv", "test", "tests",
             "__tests__", "fixtures"}
SKIP_WORDS = {"test", "tests", "spec", "specs"}  # dosya adında ayrı bir sözcük olarak (latest.html atlanmaz)
# Denetçinin kanıtı: değişikliklerin metninde atlanan dosyalar (kilitler, küçültülmüş çıktı, Jev'in ortak bağlamı)
NOISE_FILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
               "pipfile.lock", "cargo.lock", "composer.lock"}
NOISE_SUFFIXES = (".min.js", ".min.css", ".map")
JEV_FILES = {"AGENTS.md", "CLAUDE.md"}
DATA_EXT = {".json", ".geojson", ".topojson", ".csv", ".tsv", ".svg", ".xml"}  # kısa tutulur
VIDEO_EXT = {".mp4", ".webm", ".mov", ".gif"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
PAGE_EXT = {".html", ".htm", ".svg"}
# Jev'in görev commit'i ("jev(T01): başlık [sol]"; paralel şeridin tek commit'i de aynı biçimde): kodu kim yazdı
COMMIT_AGENT = re.compile(r"^jev\([^)]*\): .*\[(\w+)\]$")


def report_name(round_: int) -> str:
    return "report" if round_ <= 1 else f"report-{round_}"


def latest_report(rdir, state: dict) -> tuple[dict | None, str]:
    """(report.json içeriği, report.md metni) — en son tur."""
    reps = state.get("reports") or []
    if not reps:
        return None, ""
    name = report_name(int(reps[-1].get("round", 1)))
    data = read_json(rdir / f"{name}.json")
    md_path = rdir / f"{name}.md"
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    return data, md


def collect_checks(run) -> list[tuple[str, str]]:
    """(kaynak, komut) — görevlerin verify komutları, proje testi, ölçüt komutları. Tekrarlar bir kez."""
    st, plan = run.state, run.plan
    items: list[tuple[str, str]] = []
    for t in st["tasks"]:
        if t.get("status") == "done":
            items += [(t["id"], c) for c in t.get("verify") or [] if str(c).strip()]
    test_cmd = ((plan.get("commands") or {}).get("test") or "").strip()
    if test_cmd:
        items.append(("tam_test", test_cmd))
    for c in plan.get("success_criteria") or []:
        if (c.get("command") or "").strip():
            items.append((c.get("id") or "SC", c["command"]))
    seen, out = set(), []
    for src, cmd in items:
        key = cmd.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append((src, cmd))
    return out


def fresh_rows(run) -> dict[str, dict]:
    """Tek görevli işte son görevin doğrulama satırları. Görevin commit'i hâlâ HEAD ise kod o doğrulamadan beri
    değişmedi: komutlar yeniden çalıştırılmaz (mini ve küçük işte son kontrol saniyeler sürer)."""
    try:
        head = gitops.head(run.project)
    except gitops.GitError:
        return {}
    out: dict[str, dict] = {}
    for t in run.state.get("tasks", []):
        if t.get("status") != "done" or not t.get("verify_from_worker") or not head or t.get("commit") != head:
            continue
        last = (t.get("attempts") or [{}])[-1]
        for r in last.get("verify") or []:
            if r.get("command") and r.get("source") == "isci":
                out.setdefault(str(r["command"]).strip(), r)
    return out


def run_review(run) -> None:
    cfg, st, project = run.cfg, run.state, run.project
    rnd = int(st.get("round", 1))
    level = scale_level(st)
    run.term.header(f"Son kontrol · tur {rnd}")
    reviewer = pick_reviewer(run, level)  # raporu kimin yazacağı baştan belli olsun (ofis, terminal)
    # 1) Jev doğrulamaları çalıştırır (denetçi salt okur, test çalıştırmaz)
    checks = collect_checks(run)
    fresh = fresh_rows(run)
    run.emit("agent.state", {"agent": "jev", "state": "verifying", "task_id": None, "phase": "reviewing",
                             "text": f"Son kontrol için {len(checks)} komut çalışıyor", "until": None})
    results: list[dict] = []
    timeout = cfg.verify_timeout_s(level)
    for src, cmd in checks:
        old = fresh.get(cmd.strip())
        if old is not None:
            row = {**old, "source": src, "note": "görevin doğrulamasından (kod o zamandan beri değişmedi)"}
        else:
            r = run_command(cmd, project, timeout, script_dir=run.rdir / "dogrulama", cancel=run.cancel)
            if r.cancelled:
                raise KeyboardInterrupt
            row = {**r.to_dict(60), "source": src}
        results.append(row)
        run.emit("verify", {"task_id": src, "command": cmd, "passed": row["passed"], "exit_code": row["exit_code"],
                            "source": "son_kontrol"})
        mark = run.term.c("geçti", "green") if row["passed"] else run.term.c("KALDI", "red")
        run.term.line(f"  [{src}] {shorten(cmd, 90)} → {mark}" + (" (az önce)" if old is not None else ""))
    atomic_write_json(run.rdir / f"dogrulama-{rnd}.json", results)
    # 2) denetim: mini ve küçük işte Jev, orta ve büyükte denetçi ya da yedeği (ölçeğin efor tavanıyla)
    frames: list[dict] = []
    if reviewer == "jev":
        rep, by = jev_report(run, results), "jev"
    else:
        frames = make_frames(run, rnd)
        rep, by = reviewer_review(run, results, level, rnd, frames, reviewer)
    publish_report(run, rep, results, rnd, by=by, frames=frames)


def pick_reviewer(run, level: str) -> str:
    """Raporu kim yazar: mini ve küçük işte Jev, orta ve büyükte denetçi (Sol). Kimse kendi işini denetlemesin: ölçek
    yedek denetçiye açıksa ([jev] yedek_denetci; varsayılan büyük) ve kodun yarısından fazlasını denetçi kendisi
    yazdıysa son kontrolü yedek (Opus) yapar. Yedek soğumadaysa beklenmez, denetim denetçide kalır."""
    cfg = run.cfg
    if cfg.jev_review(level):
        return "jev"
    agent, note = cfg.reviewer, ""
    backup = cfg.backup_review(level)
    if backup:
        lines = authorship(run.project, run.state.get("base_commit") or "")
        total = sum(lines.values())
        if total and lines[agent] * 2 > total:
            note = f"kodun çoğunu {context.display(agent)} yazdı (%{round(100 * lines[agent] / total)})"
            if run.quota.available(backup):
                agent = backup
            else:
                note += f" ama {context.display(backup)} soğumada"
    set_reviewer(run, agent, note)
    return agent


def set_reviewer(run, agent: str, note: str) -> None:
    """Son kontrolü yapacak ajanı koşu durumuna yazar ve duyurur (ofis: "raporu Opus yazacak"; rapor eki)."""
    run.state["reviewer"], run.state["review_note"] = agent, note
    run.save()
    run.emit("run.info", {"reviewer": agent})
    if note:
        run.log("info", f"{note[:1].upper()}{note[1:]}; son kontrolü {context.display(agent)} yapacak.")


def authorship(project: Path, base: str) -> Counter:
    """Başlangıçtan HEAD'e kodu kim yazdı: Jev'in görev commit'lerinde ajan başına değişen satır (eklenen + silinen).
    Kodun kendisi sayılır: kilit, küçültülmüş, veri ve ikili dosyalar, Jev'in ortak bağlamı ve görev dışı commit'ler
    (başlangıç, kaydedilmemiş değişiklikler) sayılmaz."""
    lines: Counter = Counter()
    if not base:
        return lines
    try:
        out = gitops.git(["log", "--no-merges", "--no-renames", "--numstat", "--format=@%s", f"{base}..HEAD"],
                         project, check=False).stdout
    except gitops.GitError:
        return lines
    agent = None
    for row in out.splitlines():
        if row.startswith("@"):
            m = COMMIT_AGENT.match(row[1:])
            agent = m.group(1) if m else None
            continue
        parts = row.split("\t")
        if (agent and len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and not _noise(parts[2])
                and PurePosixPath(parts[2]).suffix.lower() not in DATA_EXT):
            lines[agent] += int(parts[0]) + int(parts[1])
    return lines


def reviewer_review(run, results: list[dict], level: str, rnd: int, frames: list[dict],
                    agent: str) -> tuple[dict, str]:
    """Denetçinin tek çağrısı: kanıt paketi (değişikliklerin metni dahil) ve kareler; klasör ağacı verilmez.
    Yedek denetçi çağrılamazsa (kota, oturum, iki başarısız deneme) beklenmez: denetim denetçiye döner.
    Dönen: (rapor, raporu yazan ajan)."""
    cfg, st, project = run.cfg, run.state, run.project
    run.emit("agent.state", {"agent": "jev", "state": "idle", "task_id": None, "phase": "reviewing",
                             "text": "Denetçinin raporunu bekliyor", "until": None})
    base = st.get("base_commit") or ""
    diffstat = gitops.diff_stat(project, base, "HEAD") if base else ""
    diff = text_diff(project, base)
    _, prev_md = latest_report(run.rdir, st) if rnd > 1 else (None, "")
    refused: set[str] = set()  # son kontrolü güvenlik gerekçesiyle yapmayanlar
    while True:  # en fazla iki tur: yedek denetçi, sonra denetçi (denetçinin çağrısı Unavailable yükseltmez)
        prompt = context.review_prompt(cfg, agent, run.plan, st, run.rdir, results, diffstat, diff, frames, prev_md)
        effort = cfg.effort(agent, "denetim", cfg.step_cap(level, "denetim"))
        run.term.info(f"{context.display(agent)} son kontrolü yapıyor ({cfg.label(agent)}, efor {effort}"
                      + (f", {len(frames)} kare" if frames else "") + ")…")
        try:
            res = call_fixed(run, agent, "review", prompt, schema_name="review", effort=effort, timeout_key="denetim",
                             extra={"round": rnd, "passed": [r["passed"] for r in results]},
                             images=[f["path"] for f in frames],
                             optional=agent != cfg.reviewer and cfg.reviewer not in refused)
            return res.structured, agent
        except Unavailable as e:
            note = f"{context.display(agent)} son kontrolü yapamadı ({e.reason})"
            agent = cfg.reviewer
            set_reviewer(run, agent, note)
        except Refused as e:
            refused.add(e.agent)
            alt = refusal_reviewer(run, refused)
            if alt is None:
                raise refusal_pause(run, e.agent, "son kontrol", e.text)
            agent = alt
            set_reviewer(run, agent, f"{context.display(e.agent)} son kontrolü güvenlik gerekçesiyle yapmadı")


def refusal_reviewer(run, refused: set[str]) -> str | None:
    """Son kontrolü reddeden yerine: ret yedeği (ret_takasi), yedek denetçi, denetçi (reddetmemiş ilk ajan)."""
    cfg = run.cfg
    for a in (cfg.swap_partner, cfg.backup_reviewer, cfg.reviewer):
        if a and a in cfg.agents and a not in refused:
            return a
    return None


def publish_report(run, rep: dict, results: list[dict], rnd: int, by: str, frames: list[dict] | None = None) -> None:
    st, project = run.state, run.project
    name = report_name(rnd)
    atomic_write_json(run.rdir / f"{name}.json", rep)
    md = rep.get("report_markdown", "").rstrip() + "\n\n" + jev_appendix(run, results, frames)
    atomic_write_text(run.rdir / f"{name}.md", md)
    try:  # kullanıcı kolayca bulsun: proje klasöründe de son rapor (git dışında)
        atomic_write_text(project / ".jev" / "son-rapor.md", md)
    except OSError:
        pass
    st.setdefault("reports", []).append({"round": rnd, "file": f"{name}.md", "verdict": rep.get("verdict"),
                                         "summary": rep.get("summary", ""), "by": by, "ts": iso()})
    run.save()
    run.set_phase("reported")
    run.emit("report.ready", {"round": rnd, "verdict": rep.get("verdict"), "summary": rep.get("summary", ""),
                              "file": f"{name}.md", "gaps": len(rep.get("gaps") or []), "by": by,
                              "criteria": [{"id": c.get("id"), "status": c.get("status")}
                                           for c in rep.get("criteria") or []]})
    sc = st.get("scale") or {}
    sizing.record_history(run, {"tur": "sonuc", "sonuc": rep.get("verdict"), "tur_no": rnd,
                                "olcek": scale_level(st), "zorluk": sc.get("difficulty"),
                                "sure_sn": round(float(st.get("work_s") or 0)), "denetci": by})
    print_report_summary(run, rep, name)
    open_result(run, rep)


# --- denetçinin kanıtı: değişikliklerin metni -------------------------------------------------------

def _diff_path(head: str) -> str:
    """"diff --git a/X b/X" satırındaki yol (yeniden adlandırma kapalı: iki yol aynı; boşluklu yol da doğru çıkar)."""
    s = head[len("diff --git "):].strip()
    n = (len(s) - 5) // 2
    if n > 0 and s.startswith("a/") and s[2 + n:5 + n] == " b/":
        return s[2:2 + n]
    return s.rsplit(" b/", 1)[-1].strip('"')


def _noise(path: str) -> bool:
    """Kanıtta ve kod payında sayılmayan dosya: kilit, küçültülmüş çıktı, Jev'in ortak bağlam dosyaları."""
    name = PurePosixPath(path).name.lower()
    return path in JEV_FILES or name in NOISE_FILES or name.endswith(NOISE_SUFFIXES)


def _is_test(p: PurePosixPath) -> bool:
    words = {x.lower() for x in p.parts[:-1]} | set(re.split(r"[^a-z0-9]+", p.name.lower()))
    return bool(words & (SKIP_WORDS | {"__tests__"}))


def text_diff(project: Path, base: str, max_chars: int = 30000, per_file: int = 6000) -> str:
    """Başlangıçtan HEAD'e değişikliklerin metni: önce kaynak kod, sonra testler, en son veri dosyaları (kısa tutulur).
    Kilit ve küçültülmüş dosyalar, medya (video, resim, PDF, ses: karelerde) ve Jev'in ortak bağlam dosyaları atlanır;
    silinen dosyanın yalnızca başlığı kalır.
    Her dosya ve toplam kısaltılır; sığmayanların adı sona yazılır (değişiklik özeti hepsini zaten sayar)."""
    if not base:
        return ""
    try:
        raw = gitops.git(["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "-D", "-U2", base,
                          "HEAD"], project, check=False).stdout
    except gitops.GitError:
        return ""
    files: list[tuple[int, str, str]] = []
    for part in re.split(r"(?m)^(?=diff --git )", raw):
        if not part.startswith("diff --git "):
            continue
        path = _diff_path(part.split("\n", 1)[0])
        p = PurePosixPath(path)
        if _noise(path) or (p.suffix.lower() in OPEN_EXT and p.suffix.lower() not in PAGE_EXT):  # medya: karelerde
            continue
        files.append((2 if p.suffix.lower() in DATA_EXT else 1 if _is_test(p) else 0, path, part))
    files.sort(key=lambda x: x[0])  # kararlı: aynı sınıfta git'in sırası
    out: list[str] = []
    left: list[str] = []
    used = 0
    for rank, path, part in files:
        cap = min(per_file, 1500) if rank == 2 else per_file
        if len(part) > cap:
            part = part[:cap].rstrip() + f"\n… ({len(part) - cap} karakter kısaltıldı)\n"
        if used + len(part) > max_chars:
            left.append(path)
            continue
        out.append(part)
        used += len(part)
    if left:
        out.append(f"… yer kalmadığından gösterilmeyen dosyalar ({len(left)}): {', '.join(left[:30])}"
                   + (" …" if len(left) > 30 else "") + "\n")
    return "".join(out).rstrip()


# --- denetçinin kanıtı: sonucun kareleri --------------------------------------------------------------

def outputs(run, *, modified: bool = False) -> list[str]:
    """Koşuda eklenen (istenirse değişen de) açılabilir çıktılar, öncelik sırasıyla. Test, kapsam ve bağımlılık
    klasörleri atlanır; önce HTML, sonra video, görsel, PDF, ses; kök dizine yakın ve index.html önce."""
    base = run.state.get("base_commit")
    if not base:
        return []
    try:
        ch = gitops.changes_since(run.project, base)
    except gitops.GitError:
        return []
    cands = []
    for f in ch["added"] + (ch["modified"] if modified else []):
        p = PurePosixPath(f)
        ext = p.suffix.lower()
        if ext not in OPEN_EXT or any(part.lower() in SKIP_DIRS for part in p.parts[:-1]):
            continue
        if SKIP_WORDS & set(re.split(r"[^a-z0-9]+", p.name.lower())):
            continue
        cands.append((OPEN_EXT[ext], len(p.parts), p.name.lower() != "index.html", f))
    return [c[3] for c in sorted(cands)]


def make_frames(run, rnd: int) -> list[dict]:
    """Denetçinin bakacağı kareler ({"path", "what"}): video ve GIF'ten üç an, PDF'ten ilk iki sayfa, HTML ve SVG'den
    tarayıcı görüntüsü, resimler olduğu gibi. En fazla [sinirlar].denetim_karesi (0 = kapalı); kuru koşuda yok.
    Araç yoksa ya da bir dosya açılamazsa o dosya atlanır: denetim karesiz de yapılır."""
    limit = int(run.cfg.get("sinirlar", "denetim_karesi", default=6) or 0)
    if limit <= 0 or run.state.get("dry_run"):
        return []
    out_dir = run.rdir / "kareler" / f"tur-{rnd}"
    frames: list[dict] = []
    for rel in outputs(run, modified=True):
        if len(frames) >= limit:
            break
        src = run.project / rel
        if not src.is_file():
            continue
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{len(frames) + 1:02d}-" + re.sub(r"[^A-Za-z0-9_-]+", "_", PurePosixPath(rel).stem)[:40]
            frames += _frames_of(src, rel, out_dir, stem, limit - len(frames))
        except Exception as e:  # noqa: BLE001 — kare çıkmazsa denetim yine yapılır
            run.log("warn", f"Kare çıkarılamadı ({rel}): {shorten(str(e), 200)}")
    if frames:
        run.log("info", f"Denetçi için {len(frames)} kare hazırlandı ({out_dir}).")
    return frames


def _frames_of(src: Path, rel: str, out_dir: Path, stem: str, room: int) -> list[dict]:
    ext = src.suffix.lower()
    if ext in IMAGE_EXT:
        return _image(src, rel, out_dir, stem)
    if ext in PAGE_EXT:
        png = out_dir / f"{stem}.png"
        if ext == ".svg":  # SVG ekrana sığdırılarak (vektör: küçükse büyütülür)
            page = out_dir / f"{stem}.html"
            page.write_text('<!doctype html><html><body style="margin:0;background:#fff"><img src="'
                            + src.resolve().as_uri() + '" style="display:block;width:100vw;height:100vh;'
                            'object-fit:contain"></body></html>', encoding="utf-8")
            if _screenshot(page, png) or _magick([str(src), "-resize", "1280x900>", str(png)], png):
                return [{"path": str(png), "what": f"{rel}: görüntü"}]
            return []
        if _screenshot(src, png):
            return [{"path": str(png), "what": f"{rel}: tarayıcıda ilk ekran (1280×900; file:// ile açıldı, sunucu "
                                              "ya da derleme isteyen sayfa boş görünebilir)"}]
        return []
    if ext == ".pdf":
        return _pdf_pages(src, rel, out_dir, stem, min(2, room))
    if ext in VIDEO_EXT:
        return _video_frames(src, rel, out_dir, stem, min(3, room))
    return []


def _run_tool(argv: list[str], timeout: float, *, output: bool = True) -> subprocess.CompletedProcess | None:
    """Kare aracını çalıştırır. Tarayıcının alt süreçleri boruyu açık tutabileceğinden çıktısı alınmaz (output=False)."""
    pipe = subprocess.PIPE if output else subprocess.DEVNULL
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=pipe, stderr=pipe, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None


def _tool(command: str, name: str | None = None) -> str | None:
    """PATH'teki araç; yoksa Jev'in ortam ölçümündeki yolu (ör. PATH'e eklenmemiş ImageMagick)."""
    found = shutil.which(command)
    if found or not name:
        return found
    return next((a.get("yol") for a in machine.environment().get("araclar") or [] if a.get("ad") == name), None)


def _browser() -> str | None:
    """Başsız tarayıcı: Remotion'ın chrome-headless-shell'i (en hızlısı), Chrome ya da Edge."""
    exe = machine.remotion_browser() or _tool("chrome", "Google Chrome")
    if exe:
        return exe
    for pattern in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        p = os.path.expandvars(pattern)
        if "%" not in p and os.path.isfile(p):
            return p
    return shutil.which("msedge") or shutil.which("chromium")


def _screenshot(page: Path, png: Path) -> bool:
    exe = _browser()
    if not exe:
        return False
    with tempfile.TemporaryDirectory(prefix="jev-kare-", ignore_cleanup_errors=True) as profile:
        _run_tool([exe, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                   "--no-default-browser-check", "--disable-extensions", "--mute-audio", "--window-size=1280,900",
                   "--virtual-time-budget=4000", f"--user-data-dir={profile}", f"--screenshot={png}",
                   page.resolve().as_uri()], 45, output=False)
    return png.is_file() and png.stat().st_size > 0


def _magick(args: list[str], png: Path) -> bool:
    exe = _tool("magick", "ImageMagick")
    r = _run_tool([exe, *args], 60) if exe else None
    return bool(r and r.returncode == 0 and png.is_file())


def _image(src: Path, rel: str, out_dir: Path, stem: str) -> list[dict]:
    """Resim olduğu gibi verilir; çok büyükse küçültülmüş kopyası."""
    if src.stat().st_size <= 8_000_000:
        return [{"path": str(src), "what": f"{rel}: resim"}]
    png = out_dir / f"{stem}.png"
    if _magick([str(src), "-resize", "1600x1600>", str(png)], png):
        return [{"path": str(png), "what": f"{rel}: resim (küçültüldü)"}]
    return []


def _pdf_pages(src: Path, rel: str, out_dir: Path, stem: str, n: int) -> list[dict]:
    out: list[dict] = []
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        with pymupdf.open(str(src)) as doc:
            total = doc.page_count
            for i in range(min(n, total)):
                png = out_dir / f"{stem}-s{i + 1}.png"
                doc[i].get_pixmap(dpi=100).save(str(png))
                out.append({"path": str(png), "what": f"{rel}: sayfa {i + 1}/{total}"})
        return out
    tool = _tool("pdftoppm")
    if tool:
        _run_tool([tool, "-png", "-r", "100", "-f", "1", "-l", str(n), str(src), str(out_dir / stem)], 60)
        for i, png in enumerate(sorted(out_dir.glob(f"{stem}-*.png"))[:n]):
            out.append({"path": str(png), "what": f"{rel}: sayfa {i + 1}"})
    return out


def _clock(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def _duration(src: Path) -> float | None:
    probe = _tool("ffprobe")
    if probe:
        r = _run_tool([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)], 30)
        try:
            if r is not None and r.returncode == 0:
                return float(r.stdout.decode("utf-8", "replace").strip())
        except ValueError:
            pass
    ff = _tool("ffmpeg")
    r = _run_tool([ff, "-hide_banner", "-i", str(src)], 30) if ff else None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "replace")) if r else None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def _video_frames(src: Path, rel: str, out_dir: Path, stem: str, n: int) -> list[dict]:
    ff = _tool("ffmpeg")
    if not ff or n <= 0:
        return []
    dur = _duration(src) or 0.0
    picks = {1: (0.5,), 2: (0.2, 0.8)}.get(n, (0.1, 0.5, 0.9)) if dur > 0 else (0.0,)
    out: list[dict] = []
    for i, frac in enumerate(picks):
        t = dur * frac
        png = out_dir / f"{stem}-{i + 1}.png"
        r = _run_tool([ff, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                       "-vf", "scale='min(1280,iw)':-2", str(png)], 60)
        if r is not None and r.returncode == 0 and png.is_file():
            out.append({"path": str(png), "what": f"{rel}: {_clock(t)} anı"
                                                  + (f" (süre {_clock(dur)})" if dur > 0 else "")})
    return out


# --- mini ve küçük: Jev'in raporu ---------------------------------------------------------------

def _status(task: dict) -> str:
    s = task.get("status") or ""
    return STATUS_TR.get(s, s).lower()


def _dur(seconds) -> str:
    s = int(round(float(seconds or 0)))
    return f"{s // 60} dk {s % 60} sn" if s >= 60 else f"{s} sn"


def jev_report(run, results: list[dict]) -> dict:
    """Mini ve küçük işin raporu (review şemasıyla): ayrı denetçi çağrılmaz (kullanıcı kararı). Karar Jev'in
    doğrulamasından çıkar: iş bitti ve komutlar geçti → başarılı; iş bitti ama bir komut kaldı → kısmen; iş bitmedi →
    başarısız."""
    st, plan = run.state, run.plan
    run.emit("agent.state", {"agent": "jev", "state": "reviewing", "task_id": None, "phase": "reviewing",
                             "text": "Raporu yazıyor", "until": None})
    tasks = st.get("tasks") or []
    done = [t for t in tasks if t.get("status") == "done"]
    undone = [t for t in tasks if t.get("status") != "done"]
    failed = [r for r in results if not r.get("passed")]
    if not done:
        verdict, status = "basarisiz", "unmet"
    elif undone or failed or not results:
        verdict, status = "kismen", "partial" if results else "unverified"
    else:
        verdict, status = "basarili", "met"
    files = sorted({f for t in done for f in t.get("files") or []})
    runs = run_hints(run)
    who = ", ".join(dict.fromkeys(context.display(t["agent"]) for t in done if t.get("agent"))) or "-"
    ok_n = len(results) - len(failed)
    accept = next((t.get("kabul") for t in reversed(done) if t.get("kabul")), None)
    accept_txt = f"; Jev'in kabul kontrolü: isteğin alışılmış biçimi (%{round(accept['p'] * 100)})" if accept else ""
    if verdict == "basarisiz":
        last = next((a for t in undone for a in reversed(t.get("attempts") or []) if a.get("problem")), {})
        summary = "İş bitmedi: " + shorten(context.OUTCOME_TR.get(last.get("outcome"), last.get("outcome") or "")
                                           + (" — " + last["problem"] if last.get("problem") else ""), 300)
    else:
        n_txt = "tek görevle" if len(done) == 1 else f"{len(done)} görevle"
        summary = (f"İş {n_txt} yapıldı ({who}); {ok_n}/{len(results)} doğrulama komutu geçti"
                   + (f"; {len(files)} dosya" if files else "") + ".")
    evidence = (f"Jev işçinin doğrulama komutlarını proje kökünde yeniden çalıştırdı: {ok_n}/{len(results)} geçti"
                + accept_txt + ".") if results else "Jev'in yeniden çalıştırabildiği doğrulama komutu yok."
    gaps: list[dict] = []
    if verdict != "basarili":
        what = ("; ".join(f"`{shorten(r.get('command') or '', 80)}` kaldı" for r in failed[:3])
                or "; ".join(f"{t['id']} {_status(t)}"
                             for t in undone[:3]) or "doğrulama komutu yok")
        gaps.append({"id": "G1", "criteria": ["SC1"], "description": what,
                     "suggested_fix": "Sorunu gider; doğrulama komutlarını kendiliğinden biten biçimde yeniden çalıştır."})
    risks = []
    if any(PurePosixPath(f).suffix.lower() in (".html", ".htm") for f in files):
        risks.append("Arayüz insan gözüyle denetlenmedi; Jev mantık testlerini ve kabul sorusunu kullandı.")
    skipped = [n for t in done for a in (t.get("attempts") or [])[-1:] for n in a.get("notes") or []
               if n.startswith("Yeniden çalıştırılmayan")]
    risks += skipped[:1]
    next_steps = [f"Çalıştırma: {x}" for x in runs[:3]] + [
        "Değişiklik istersen: `düzelt <not>` (tek görevle, plansız yapılır)."]
    scale = SCALE_TR.get(scale_level(st))
    rows = [f"# {plan.get('project_name') or 'İş'} — son kontrol (Jev)", "",
            f"**Karar:** {VERDICT_TR[verdict]} — {summary}", "",
            (f"{scale} iş" if scale else "Tek görevli iş") + ": plan ve ayrı denetçi yok. Jev işçinin doğrulama komutlarını yeniden çalıştırdı"
            + (" ve işin isteğe uygun olup olmadığını sordu." if accept else "."), ""]
    rows += ["## İş", ""]
    for t in tasks:
        att = t.get("attempts") or []
        dur = sum(float(a.get("duration_s") or 0) for a in att)
        rows.append(f"- {t['id']} · {t.get('title', '')} · {context.display(t['agent']) if t.get('agent') else '-'}"
                    f" · {len(att)} deneme · {_dur(dur)} · {_status(t)}")
        if t.get("summary"):
            rows.append(f"  {shorten(t['summary'], 400)}")
    if files:
        rows += ["", "## Dosyalar", ""] + [f"- `{f}`" for f in files[:40]]
    rows += ["", "## Doğrulama", ""]
    rows += [f"- `{r.get('command')}` → {'geçti' if r.get('passed') else 'KALDI'}" for r in results] or ["- yok"]
    if runs:
        rows += ["", "## Çalıştırma", ""] + [f"- {x}" for x in runs]
    rows += ["", "## Ölçüt", "", f"- SC1: {CRIT_TR[status]} — {evidence}"]
    if gaps:
        rows += ["", "## Eksikler", ""] + [f"- {g['id']}: {g['description']}" for g in gaps]
    return {"verdict": verdict, "summary": summary, "report_markdown": "\n".join(rows),
            "criteria": [{"id": "SC1", "status": status, "evidence": evidence,
                          "gap": gaps[0]["description"] if gaps else None}],
            "gaps": gaps, "risks": risks, "next_steps": next_steps}


def run_hints(run) -> list[str]:
    """Kullanıcı sonucu nasıl açar ya da çalıştırır: işçilerin ÇALIŞTIRMA satırları, yoksa planın run komutu."""
    out: list[str] = []
    for t in run.state.get("tasks", []):
        if t.get("status") == "done":
            for x in context.run_lines(t):
                if x not in out:
                    out.append(x)
    cmd = ((run.plan or {}).get("commands") or {}).get("run")
    if not out and cmd:
        out.append(f"`{cmd}`")
    # düzeltme turundaki kısa satır ilk turdakinin parçasıysa tekrar yazılmaz
    return [x for x in out if not any(x != y and x.rstrip(". ") in y for y in out)]


# --- sonucu açma ---------------------------------------------------------------------------

def result_file(run) -> Path | None:
    """Koşuda eklenen, açılabilecek ana çıktı (HTML, video, görsel, PDF). Test, kapsam ve bağımlılık klasörleri
    atlanır; kök dizine yakın ve index.html önce."""
    for f in outputs(run):
        path = run.project / f
        if path.is_file():
            return path
    return None


def open_result(run, rep: dict) -> None:
    """İş bitince sonuç açılır: yalnızca HTML, video, görsel, PDF ya da ses (programlar ve betikler asla
    başlatılmaz; çalıştırma komutu raporda yazar). Kuru koşuda ve iş başarısızsa açılmaz."""
    if run.state.get("dry_run") or rep.get("verdict") == "basarisiz":
        return
    if not run.cfg.get("arayuz", "sonucu_ac", default=True):
        return
    path = result_file(run)
    if path is None:
        return
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606 — varsayılan uygulama (tarayıcı, video oynatıcı, görüntüleyici)
        else:
            webbrowser.open(path.as_uri())
    except OSError as e:
        run.log("warn", f"Sonuç açılamadı ({path.name}): {e}")
        return
    run.log("info", f"Sonuç açıldı: {path}")


# --- ek ve özet ---------------------------------------------------------------------------------

def jev_appendix(run, results: list[dict], frames: list[dict] | None = None) -> str:
    """Raporun sonuna Jev'in kendi kayıtları: ölçek, işçilerin seçimleri, kurulumlar, denetçinin kareleri, dal,
    commit'ler, kararlar, koruma, birleştirme komutları."""
    st, project = run.state, run.project
    base = st.get("base_commit") or ""
    n_commits = gitops.count_commits(project, base) if base else 0
    c = Counter(t.get("status") for t in st["tasks"])
    decisions = read_jsonl(run.rdir / "decisions.jsonl")
    guards = read_jsonl(run.rdir / "guard.jsonl")
    passed = sum(1 for r in results if r.get("passed"))
    lines = ["---", "", "## Ek: Jev'in kayıtları", ""]
    if st.get("scale"):
        lines.append(f"- Ölçek: {sizing.describe(st)}")
        esc = [x for x in st.get("olcek_gecmisi") or [] if isinstance(x, dict)]
        for x in esc:
            lines.append(f"  - {SCALE_TR.get(x.get('archived_level') or 'mini')} denemesi olmadı "
                         f"({x.get('attempt_count', 0)} deneme); yarım iş geri alındı"
                         + (f", `{x['archive_branch']}` dalında saklı" if x.get("archive_branch") else ""))
    for r in st.get("retler") or []:
        where = r.get("asama", "") + (f" {r['gorev']}" if r.get("gorev") else "")
        lines.append(f"- Güvenlik reddi: {context.display(r.get('agent', ''))} ({where}) yanıtlamadı; bu işi "
                     f"{context.display(r.get('yerine', ''))} yaptı.")
    if st.get("review_note") and st.get("reviewer"):
        lines.append(f"- Son kontrol: {context.display(st['reviewer'])} — {st['review_note']}")
    choices = context.choice_lines(st)
    if choices:
        lines.append("- İşçilerin seçimleri (istekte ya da kartta açık kalan noktalar):")
        lines += [f"  - {x}" for x in choices]
    installs = context.install_lines(st)
    if installs:
        lines.append("- Sisteme kurulanlar:")
        lines += [f"  - {x}" for x in installs]
    if frames:
        lines.append("- Denetçinin baktığı kareler:")
        lines += [f"  - {f['what']} (`{f['path']}`)" for f in frames]
    lines += [f"- Dal: `{st.get('branch')}` · başlangıç `{base[:8]}` · {n_commits} commit",
              "- Görevler: " + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())),
              f"- Doğrulama: {passed}/{len(results)} komut geçti",
              f"- Jev'in kararları: {len(decisions)} (beyin çağrısı: {st.get('brain_calls', 0)})",
              f"- Koruma engellemeleri: {len(guards)}"]
    for g in guards[:10]:
        lines.append(f"  - {g.get('ajan') or '?'} · {g.get('category_tr', g.get('category'))}: "
                     f"{shorten(str(g.get('reason')), 160)} ({g.get('eylem')})")
    devs = st.get("deviations") or []
    if devs:
        lines.append("- Plandan sapmalar:")
        for d in devs:
            lines.append(f"  - {d.get('task_id')} · {d.get('decision')} · **{d.get('deviation')}** — "
                         f"{shorten(d.get('note') or '', 240)}")
    origin = st.get("origin_branch") or "main"
    lines += ["", "### Dalı birleştirmek için", "", "Jev push, merge ya da rebase yapmaz. İncelemeden sonra:", "",
              "```powershell", f'cd "{project}"', f"git switch {origin}", f"git merge --no-ff {st.get('branch')}",
              "```", ""]
    return "\n".join(lines)


def print_report_summary(run, rep: dict, name: str) -> None:
    t = run.term
    verdict = rep.get("verdict", "")
    color = {"basarili": "green", "kismen": "yellow", "basarisiz": "red"}.get(verdict, "bold")
    t.header("Son Kontrol Raporu")
    t.line(t.c(VERDICT_TR.get(verdict, verdict), color) + " — " + shorten(rep.get("summary", ""), 400))
    for c in rep.get("criteria") or []:
        mark = {"met": "✓", "partial": "~", "unmet": "✗", "unverified": "?"}.get(c.get("status"), "·")
        gap = f" — eksik: {shorten(c['gap'], 100)}" if c.get("gap") else ""
        t.line(f"  {mark} {c.get('id')}: {CRIT_TR.get(c.get('status'), c.get('status'))}{gap}", stamp=False)
    gaps = rep.get("gaps") or []
    if gaps:
        t.line(f"Düzeltme önerileri: {len(gaps)}", "yellow")
        for g in gaps[:8]:
            t.line(f"  {g.get('id')}: {shorten(g.get('description', ''), 140)}", stamp=False)
    for x in run_hints(run)[:3]:
        t.line(f"Çalıştırma: {shorten(x, 160)}", "cyan")
    installs = context.install_lines(run.state)
    if installs:
        t.line("Sisteme kurulanlar: " + "; ".join(shorten(x, 80) for x in installs[:4]), "yellow")
    t.line(f"Rapor: {run.rdir / (name + '.md')}")
    if run.url:
        t.line(f"Ofis: {run.url}")
    t.line(t.c(fix_hint(run, rep), "bold"))


def fix_hint(run, rep: dict) -> str:
    """Rapordan sonra kullanıcıya düzeltmenin nasıl başlatılacağını söyler (akış burada durur)."""
    clean = rep.get("verdict") == "basarili" and not rep.get("gaps")
    head = "Eksik yok; yine de değişiklik istersen" if clean else "Düzeltmek için"
    if run.interactive():
        tail = " ya da ofisteki Düzelt düğmesine bas" if run.url else ""
        return f"{head}: `düzelt [not]` yaz{tail}."
    return f'{head}: jev duzelt {run.run_id} "not"'
