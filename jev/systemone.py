"""Jev'in kendi yapay zekâsı: TypeSafe Jev (System One modeli), https://docs.typesafe.ai/api.md

Jev metin üretmez: aynı duruma (state) sorulan her soruya yapılandırılmış bir yanıt verir. Soru türleri:
choice (seçeneklerden biri + olasılıklar + güven), score (sıralı ölçek) ve noul (0–1 doğruluk değeri).
Jev bu modeli iki yerde kullanır: sorunlu görevde karar (phases/brain.py) ve görevin hangi ajana verileceği
(routing.choose_agent). Model kapalıysa, anahtar yoksa ya da çağrı başarısızsa eski yol çalışır: kararı
Opus/Sol verir, dağıtımı kural yapar. Kuru koşuda ağa çıkılmaz; MockSystemOne senaryodaki kararları oynar.

Anahtar yalnızca bu süreçte kullanılır; ajan alt süreçlerine geçmez (adapters/base.child_env).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .util import append_jsonl, atomic_write_json, iso, read_json, shorten

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_KEY_ENV = "TYPESAFE_API_KEY"
INPUT_USD_PER_MTOK = 0.042  # çıktı tokenları ücretsiz ("too cheap to meter")
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


# --- soru kurucular ---------------------------------------------------------------------

def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def noul(instructions: str, true: str, false: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}


# --- sonuç ------------------------------------------------------------------------------

@dataclass
class Answer:
    type: str
    value: Any                       # choice: seçenek adı · score: 0..n-1 arası sayı · noul: 0..1
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0

    def p(self, option: str | None = None) -> float:
        """Seçilen (ya da verilen) seçeneğin olasılığı."""
        if self.type == "noul":
            return float(self.value)
        return float(self.probabilities.get(str(self.value if option is None else option), 0.0))

    def ranked(self) -> list[tuple[str, float]]:
        return sorted(((k, float(v)) for k, v in self.probabilities.items()), key=lambda kv: -kv[1])


@dataclass
class Result:
    ok: bool
    answers: dict[str, Answer] = field(default_factory=dict)
    model: str = ""
    usage: dict = field(default_factory=dict)
    duration_s: float = 0.0
    error_kind: str = "none"         # none | off | auth | invalid | limit | network
    error_text: str = ""
    extra: dict = field(default_factory=dict)  # yalnızca kuru koşu: senaryodaki tam karar


class SystemOneError(Exception):
    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind
        self.text = text


def parse_answers(raw: dict, questions: dict[str, dict]) -> dict[str, Answer]:
    got = raw.get("answers")
    if not isinstance(got, dict):
        raise SystemOneError("invalid", "Yanıtta 'answers' yok.")
    out: dict[str, Answer] = {}
    for name, q in questions.items():
        a = got.get(name)
        if not isinstance(a, dict):
            raise SystemOneError("invalid", f"'{name}' sorusu yanıtlanmadı.")
        t = q["type"]
        probs = {str(k): float(v) for k, v in (a.get("probabilities") or {}).items()}
        if t == "choice":
            v = a.get("choice")
            if v not in q["criteria"]:
                raise SystemOneError("invalid", f"'{name}': bilinmeyen seçenek {v!r}.")
            out[name] = Answer(t, v, probs, float(a.get("confidence", 0.0)))
        elif t == "score":
            out[name] = Answer(t, float(a.get("score", 0.0)), probs, float(a.get("confidence", 0.0)))
        else:
            v = float(a.get("noul", 0.5))
            out[name] = Answer(t, v, {"true": v, "false": 1.0 - v}, abs(2.0 * v - 1.0))
    return out


# --- istemci ----------------------------------------------------------------------------

class SystemOne:
    """Gerçek istemci. Standart kütüphaneyle (urllib) çalışır: Jev'in bağımlılığı yok ilkesi korunur."""

    def __init__(self, cfg, rdir: Path | None = None,
                 opener: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep):
        m = cfg.get("jev", "model", default={}) or {}
        self.on = bool(m.get("acik", True))
        self.url = m.get("adres") or DEFAULT_URL
        self.model = m.get("model") or "jev-latest"
        self.key_env = m.get("anahtar_ortam") or DEFAULT_KEY_ENV
        self.threshold = float(m.get("guven_esigi", 0.6))
        self.dispatch = bool(m.get("dagitim", True))
        self.timeout = float(m.get("zaman_asimi_sn", 20))
        self.tries = max(1, int(m.get("deneme", 3)))
        self.rdir = Path(rdir) if rdir else None
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self.last_error = ""

    @property
    def key(self) -> str:
        return (os.environ.get(self.key_env) or "").strip()

    @property
    def enabled(self) -> bool:
        return self.on and bool(self.key)

    def status(self) -> str:
        if not self.on:
            return "kapalı (jev.model.acik = false)"
        if not self.key:
            return f"anahtar yok ({self.key_env} ortam değişkeni boş)"
        return f"hazır · {self.model} · {self.url}"

    def ask(self, purpose: str, state: Any, questions: dict[str, dict], *, task_id: str | None = None,
            hint: dict | None = None) -> Result:
        if not self.enabled:
            return Result(False, error_kind="off", error_text=self.status())
        body = {"state": state, "model": self.model, "questions": questions}
        t0 = time.monotonic()
        try:
            raw = self._post(body)
            res = Result(True, parse_answers(raw, questions), model=str(raw.get("model") or self.model),
                         usage=dict(raw.get("usage") or {}))
        except SystemOneError as e:
            res = Result(False, error_kind=e.kind, error_text=e.text)
        res.duration_s = time.monotonic() - t0
        self.last_error = "" if res.ok else res.error_text
        self._record(purpose, task_id, questions, res)
        return res

    def _post(self, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last = ""
        for n in range(self.tries):
            req = urllib.request.Request(self.url, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                "Accept": "application/json", "User-Agent": "jev-orkestrator"})
            try:
                with self._open(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = _read_error(e)
                if e.code in (401, 403):
                    raise SystemOneError("auth", f"TypeSafe anahtarı geçersiz ya da yetkisiz ({e.code}). {detail}")
                if e.code in (400, 404, 422):
                    raise SystemOneError("invalid", f"TypeSafe isteği reddetti ({e.code}): {detail}")
                last = f"TypeSafe {e.code}: {detail}"
                if e.code not in RETRY_STATUS:
                    raise SystemOneError("network", last)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"TypeSafe'e ulaşılamadı: {getattr(e, 'reason', e)}"
            except ValueError as e:
                raise SystemOneError("invalid", f"TypeSafe yanıtı JSON değil: {e}")
            if n + 1 < self.tries:
                self._sleep(min(8.0, 1.0 * 2 ** n))
        raise SystemOneError("limit" if " 429" in last or " 529" in last else "network", last)

    # --- kayıt ------------------------------------------------------------------------------
    def _record(self, purpose: str, task_id: str | None, questions: dict, res: Result) -> None:
        if self.rdir is None:
            return
        try:
            append_jsonl(self.rdir / "jev-model.jsonl", {
                "ts": iso(), "purpose": purpose, "task_id": task_id, "model": res.model, "ok": res.ok,
                "error_kind": res.error_kind, "error_text": shorten(res.error_text, 400),
                "duration_ms": int(res.duration_s * 1000), "usage": res.usage,
                "questions": {k: q["type"] for k, q in questions.items()},
                "answers": {k: {"value": a.value, "confidence": round(a.confidence, 3),
                                "probabilities": {o: round(p, 3) for o, p in a.probabilities.items()}}
                            for k, a in res.answers.items()}})
            p = self.rdir / "usage.json"
            data = read_json(p, {}) or {}
            u = data.setdefault("jev", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                                        "duration_s": 0.0})
            inp = int(res.usage.get("input_tokens", 0) or 0)
            u["calls"] = int(u.get("calls", 0)) + 1
            u["input_tokens"] = int(u.get("input_tokens", 0)) + inp
            u["output_tokens"] = int(u.get("output_tokens", 0)) + int(res.usage.get("output_tokens", 0) or 0)
            u["cost_usd"] = round(float(u.get("cost_usd", 0.0)) + inp * INPUT_USD_PER_MTOK / 1_000_000, 6)
            u["duration_s"] = round(float(u.get("duration_s", 0.0)) + res.duration_s, 1)
            atomic_write_json(p, data)
        except OSError:
            pass


def _read_error(e: urllib.error.HTTPError) -> str:
    try:
        return shorten(e.read().decode("utf-8", "replace"), 300)
    except Exception:  # noqa: BLE001 - hata gövdesi okunamazsa önemli değil
        return ""


# --- kuru koşu --------------------------------------------------------------------------

class MockSystemOne(SystemOne):
    """Ağa çıkmaz. Karar sorusunda senaryodaki beyin kararını (sırasıyla) oynar; diğer sorularda `hint`
    verilen seçeneği, yoksa ilk seçeneği yüksek güvenle seçer."""

    def __init__(self, cfg, rdir: Path | None, scenario: Callable[[], dict]):
        super().__init__(cfg, rdir)
        self._scenario = scenario

    @property
    def enabled(self) -> bool:
        return self.on

    def status(self) -> str:
        return "kuru koşu (sahte Jev modeli)" if self.on else super().status()

    def ask(self, purpose, state, questions, *, task_id=None, hint=None) -> Result:
        if not self.on:
            return Result(False, error_kind="off", error_text=self.status())
        hint = dict(hint or {})
        extra: dict = {}
        if purpose == "karar":
            entry = self._scenario_decision(task_id, state)
            extra["kuru_karar"] = entry
            hint.setdefault("karar", entry.get("decision"))
            if entry.get("agent"):
                hint.setdefault("ajan", entry["agent"])
            hint.setdefault("geri_al", bool(entry.get("rollback", True)))
            hint.setdefault("sapma", entry.get("deviation") or "none")
            hint.setdefault("efor", entry.get("effort") or "varsayilan")
        answers: dict[str, Answer] = {}
        for name, q in questions.items():
            h = hint.get(name)
            if q["type"] == "noul":
                v = 0.9 if (h is None or h) else 0.1
                answers[name] = Answer("noul", v, {"true": v, "false": 1 - v}, abs(2 * v - 1))
            elif q["type"] == "score":
                n = len(q["criteria"])
                answers[name] = Answer("score", float(n // 2), {str(n // 2): 1.0}, 0.9)
            else:
                opts = list(q["criteria"])
                pick = h if h in opts else opts[0]
                rest = [o for o in opts if o != pick]
                probs = {pick: 0.9 if rest else 1.0}
                for o in rest:
                    probs[o] = round(0.1 / len(rest), 4)
                answers[name] = Answer("choice", pick, probs, 0.85 if rest else 1.0)
        res = Result(True, answers, model="jev-kuru", usage={"input_tokens": len(json.dumps(state)) // 4,
                                                             "output_tokens": 0}, extra=extra)
        self._record(purpose, task_id, questions, res)
        return res

    def _scenario_decision(self, task_id: str | None, state: Any) -> dict:
        """Sahte adaptörle aynı sayaç (kuru-sayac.json · brain): `jev devam` sonrasında da aynı sıra."""
        key = task_id or "-"
        options = ((self._scenario() or {}).get("brain") or {}).get(key) or []
        p = self.rdir / "kuru-sayac.json" if self.rdir else None
        counts = (read_json(p, {}) or {}) if p else {}
        used = int((counts.get("brain") or {}).get(key, 0))
        if used < len(options):
            entry = dict(options[used])
            if p is not None:
                counts.setdefault("brain", {})[key] = used + 1
                atomic_write_json(p, counts)
            return entry
        s = state if isinstance(state, dict) else {}
        tried = set(s.get("denenen_ajanlar") or [])
        free = [a.get("ad") for a in s.get("musait_ajanlar") or [] if a.get("ad") not in tried]
        if int(s.get("kalan_deneme", 1) or 0) <= 0:
            return {"decision": "skip", "deviation": "major"}
        if free:
            return {"decision": "reassign", "agent": free[0]}
        return {"decision": "retry"}


def make(cfg, rdir: Path | None, *, dry: bool = False, scenario: Callable[[], dict] | None = None) -> SystemOne:
    if dry:
        return MockSystemOne(cfg, rdir, scenario or (lambda: {}))
    return SystemOne(cfg, rdir)
