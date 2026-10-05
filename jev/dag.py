"""Görev grafiği doğrulaması ve sıralama yardımcıları (belirtim §4, §5.6)."""
from __future__ import annotations

import re

ID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
CODE_TYPES = {"code", "test"}
COMPLEXITY_ORDER = {"S": 0, "M": 1, "L": 2}


def validate_tasks(tasks: list[dict], plan: dict | None = None, *, existing: list[dict] | None = None,
                   max_tasks: int | None = None, require_coverage: bool = True) -> list[str]:
    """Görev listesini doğrular; Türkçe hata listesi döndürür (boşsa geçerli).

    existing: önceden var olan görevler (bölme ve düzeltme turlarında). Yeni görevler bunlara bağlanabilir;
    kimlikler bunlarla çakışamaz. require_coverage: her başarı ölçütü ve modül en az bir görevle karşılanmalı.
    """
    errs: list[str] = []
    existing = existing or []
    old_ids = {t.get("id") for t in existing}
    if not tasks:
        return ["Görev listesi boş."]
    if max_tasks is not None and len(tasks) > max_tasks:
        errs.append(f"Çok fazla görev: {len(tasks)} (en fazla {max_tasks}).")
    ids: list[str] = []
    for i, t in enumerate(tasks):
        tid = str(t.get("id") or "")
        if not ID_RX.match(tid):
            errs.append(f"{i + 1}. görevin kimliği geçersiz: {tid!r} (harf, rakam, - _ . ; en fazla 32 karakter).")
        if tid in ids:
            errs.append(f"Görev kimliği tekrarlanıyor: {tid}.")
        if tid in old_ids:
            errs.append(f"Görev kimliği mevcut bir görevle çakışıyor: {tid}.")
        ids.append(tid)
    all_ids = set(ids) | old_ids
    modules = {m.get("id") for m in (plan or {}).get("modules", [])}
    criteria = {c.get("id") for c in (plan or {}).get("success_criteria", [])}
    contracts = {c.get("id") for c in (plan or {}).get("contracts") or []}
    for t in tasks:
        tid = t.get("id")
        for d in t.get("depends_on") or []:
            if d == tid:
                errs.append(f"{tid} kendisine bağımlı.")
            elif d not in all_ids:
                errs.append(f"{tid} olmayan bir göreve bağımlı: {d}.")
        if plan is not None:
            if modules and t.get("module") not in modules:
                errs.append(f"{tid}: bilinmeyen modül {t.get('module')!r} (plandaki modüller: {', '.join(sorted(modules))}).")
            for c in t.get("covers_criteria") or []:
                if criteria and c not in criteria:
                    errs.append(f"{tid}: bilinmeyen başarı ölçütü {c!r}.")
            for c in t.get("contracts") or []:
                if c not in contracts:
                    errs.append(f"{tid}: planda olmayan sözleşme {c!r}.")
        if t.get("type") in CODE_TYPES and not [v for v in t.get("verify") or [] if str(v).strip()]:
            errs.append(f"{tid}: {t.get('type')} görevinde en az bir doğrulama komutu (verify) olmalı.")
        if t.get("type") == "research" and not [o for o in t.get("outputs") or [] if str(o).strip()]:
            errs.append(f"{tid}: araştırma görevi bulgularını kaynaklarıyla bir dosyaya yazmalı (outputs boş).")
    for n in (plan or {}).get("needs") or []:
        if n.get("task") and n["task"] not in all_ids:
            errs.append(f"İhtiyaç {n.get('what')!r} olmayan bir göreve bağlanmış: {n['task']}.")
    cyc = find_cycle(existing + tasks)
    if cyc:
        errs.append("Döngüsel bağımlılık: " + " → ".join(cyc) + ".")
    if plan is not None and require_coverage:
        covered_c = {c for t in tasks + existing for c in (t.get("covers_criteria") or [])}
        missing_c = sorted(criteria - covered_c, key=_natural)
        if missing_c:
            errs.append("Hiçbir görevin karşılamadığı başarı ölçütleri: " + ", ".join(missing_c) + ".")
        covered_m = {t.get("module") for t in tasks + existing}
        missing_m = sorted(modules - covered_m, key=_natural)
        if missing_m:
            errs.append("Hiçbir görevin karşılamadığı modüller: " + ", ".join(missing_m) + ".")
    return errs


def _natural(s: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s or "")]


def norm_path(p: str) -> str:
    """Dosya yolunu karşılaştırmak için sadeleştirir: ters bölü, baştaki ./ ve büyük-küçük harf (Windows) fark etmez."""
    s = str(p or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.rstrip("/").lower()


def ancestors(tasks: list[dict], tid: str) -> set[str]:
    """`tid`'in doğrudan ya da dolaylı bağımlı olduğu görevler."""
    deps = {t.get("id"): t.get("depends_on") or [] for t in tasks}
    out: set[str] = set()
    todo = list(deps.get(tid, []))
    while todo:
        d = todo.pop()
        if d not in out:
            out.add(d)
            todo += deps.get(d, [])
    return out


DEAD_STATUSES = ("split", "failed", "blocked")


def link_shared_files(tasks: list[dict], existing: list[dict] | None = None) -> list[str]:
    """Kartlardaki dosya ilişkilerinden eksik bağımlılıkları Jev kendisi ekler (planlayıcıya geri dönmeden):
    bir görevin okuduğu dosyayı başka bir görev yazıyorsa okuyan onu bekler; iki görev aynı dosyayı yazıyorsa
    listede sonra gelen öncekini bekler. Döngü doğuracak bağ eklenmez. Bölünmüş, başarısız ya da engellenmiş eski
    görevlere bağ kurulmaz (bağımlıyı sonsuza dek bekletir ya da engeller). Görevleri yerinde değiştirir; notları
    döndürür."""
    every = (existing or []) + tasks
    writers: dict[str, list[str]] = {}
    for t in every:
        if t.get("status") in DEAD_STATUSES:
            continue
        for o in t.get("outputs") or []:
            writers.setdefault(norm_path(o), []).append(t.get("id"))
    notes: list[str] = []

    def link(t: dict, dep: str, why: str) -> None:
        tid = t.get("id")
        if dep == tid or dep in ancestors(every, tid) or tid in ancestors(every, dep):
            return
        t["depends_on"] = list(t.get("depends_on") or []) + [dep]
        notes.append(f"{tid} → {dep} ({why})")

    order = {t.get("id"): i for i, t in enumerate(every)}
    for t in tasks:
        for f in t.get("reads") or []:
            for w in writers.get(norm_path(f), []):
                link(t, w, f"okuduğu {f} dosyasını yazıyor")
        for o in t.get("outputs") or []:
            for w in writers.get(norm_path(o), []):
                if order.get(w, 0) < order.get(t.get("id"), 0):
                    link(t, w, f"ikisi de {o} dosyasını yazıyor")
    return notes


def find_cycle(tasks: list[dict]) -> list[str] | None:
    """Varsa bir döngüyü (kimlik listesi) döndürür."""
    graph = {t.get("id"): [d for d in (t.get("depends_on") or [])] for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = GRAY
        stack.append(n)
        for d in graph.get(n, []):
            if d not in graph:
                continue
            if color[d] == GRAY:
                return stack[stack.index(d):] + [d]
            if color[d] == WHITE:
                r = visit(d)
                if r:
                    return r
        stack.pop()
        color[n] = BLACK
        return None

    for n in list(graph):
        if color[n] == WHITE:
            r = visit(n)
            if r:
                return r
    return None


def dependents_count(tasks: list[dict]) -> dict[str, int]:
    """Her görevi geçişli olarak bekleyen görev sayısı (kritik yol ağırlığı)."""
    rev: dict[str, set[str]] = {t["id"]: set() for t in tasks}
    for t in tasks:
        for d in t.get("depends_on") or []:
            rev.setdefault(d, set()).add(t["id"])
    memo: dict[str, set[str]] = {}

    def reach(n: str, seen: frozenset = frozenset()) -> set[str]:
        if n in memo:
            return memo[n]
        out: set[str] = set()
        for m in rev.get(n, ()):
            if m in seen:
                continue
            out.add(m)
            out |= reach(m, seen | {n})
        memo[n] = out
        return out

    return {t["id"]: len(reach(t["id"])) for t in tasks}


def topo_order(tasks: list[dict]) -> list[str]:
    ids = [t["id"] for t in tasks]
    deps = {t["id"]: [d for d in (t.get("depends_on") or []) if d in ids] for t in tasks}
    done: list[str] = []
    placed: set[str] = set()
    while len(done) < len(ids):
        progressed = False
        for i in ids:
            if i not in placed and all(d in placed for d in deps[i]):
                done.append(i)
                placed.add(i)
                progressed = True
        if not progressed:  # döngü: kalanları sırayla ekle
            done += [i for i in ids if i not in placed]
            break
    return done
