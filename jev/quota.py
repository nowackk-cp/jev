"""Kota: kalıp tespiti, sıfırlanma zamanını ayrıştırma, soğuma defteri (kota.json)."""
from __future__ import annotations

import datetime as _dt
import re
import threading
from pathlib import Path

from .util import atomic_write_json, iso, jev_home, now, parse_iso, read_json

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTHS.update({"oca": 1, "şub": 2, "sub": 2, "nis": 4, "haz": 6, "tem": 7, "ağu": 8, "agu": 8,
                "eyl": 9, "eki": 10, "kas": 11, "ara": 12})

_TIME = r"(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)?"
_ANCHOR = r"(?:resets?(?:\s+at)?|try again(?:\s+at)?|available(?:\s+again)?\s+at|until|sıfırlanma)"
_RE_EPOCH = re.compile(r"\|\s*(\d{10})\b")
_RE_DATE_TIME = re.compile(
    _ANCHOR + r"\s+([A-Za-zçğıöşüÇĞİÖŞÜ]{3})[a-zçğıöşü]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(?:(\d{4}),?\s*)?"
    r"(?:at\s+)?" + _TIME, re.I)
_RE_TIME = re.compile(_ANCHOR + r"\s+" + _TIME + r"(?![\d/])", re.I)
_RE_REL = re.compile(
    r"\bin\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,]*(?:and\s+)?)+)", re.I)
_RE_REL_PART = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)
# A quota message can be logged a few seconds after its advertised reset time.
# Treat a just-passed clock time as the current reset, instead of rolling it
# forward by a full day. Gateway applies a one-minute minimum cooldown.
_RESET_CLOCK_GRACE = _dt.timedelta(minutes=5)


def matches_any(text: str, patterns: list[str]) -> bool:
    if not text:
        return False
    for p in patterns:
        try:
            if re.search(p, text, re.I):
                return True
        except re.error:
            if p.lower() in text.lower():
                return True
    return False


def _hm(h: str, m: str | None, ampm: str | None) -> tuple[int, int] | None:
    hour, minute = int(h), int(m or 0)
    if ampm:
        a = ampm.lower().replace(".", "")
        if hour == 12:
            hour = 0
        if a.startswith("p"):
            hour += 12
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour, minute


def epoch_reset(value, ref: _dt.datetime | None = None) -> _dt.datetime | None:
    """CLI'ın verdiği sıfırlanma anı (Unix saniyesi) → UTC. Makul aralıkta değilse None."""
    try:
        t = _dt.datetime.fromtimestamp(int(value), _dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    ref = ref or now()
    return t if ref - _dt.timedelta(days=1) < t < ref + _dt.timedelta(days=10) else None


def parse_reset(text: str, ref: _dt.datetime | None = None) -> _dt.datetime | None:
    """Hata metninden sıfırlanma zamanını çıkarır (UTC). Bulamazsa None."""
    if not text:
        return None
    ref = ref or now()
    local_ref = ref.astimezone()
    m = _RE_EPOCH.search(text)
    t = epoch_reset(m.group(1), ref) if m else None
    if t:
        return t
    m = _RE_REL.search(text)
    if m:
        total = _dt.timedelta()
        for num, unit in _RE_REL_PART.findall(m.group(1)):
            n, u = int(num), unit.lower()
            if u.startswith("d"):
                total += _dt.timedelta(days=n)
            elif u.startswith("h"):
                total += _dt.timedelta(hours=n)
            elif u.startswith("m"):
                total += _dt.timedelta(minutes=n)
            else:
                total += _dt.timedelta(seconds=n)
        if total.total_seconds() > 0:
            return ref + total
    m = _RE_DATE_TIME.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3])
        hm = _hm(m.group(4), m.group(5), m.group(6))
        if mon and hm:
            year = int(m.group(3)) if m.group(3) else local_ref.year
            try:
                t = local_ref.replace(year=year, month=mon, day=int(m.group(2)), hour=hm[0], minute=hm[1],
                                      second=0, microsecond=0)
                if not m.group(3) and t < local_ref - _dt.timedelta(days=1):
                    t = t.replace(year=year + 1)
                return t.astimezone(_dt.timezone.utc)
            except ValueError:
                pass
    m = _RE_TIME.search(text)
    if m:
        hm = _hm(m.group(1), m.group(2), m.group(3))
        if hm and (m.group(2) or m.group(3)):  # "resets 3" gibi belirsiz sayıları alma
            t = local_ref.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            # Just after midnight, a just-passed reset clock belongs to yesterday.
            if t - local_ref >= _dt.timedelta(days=1) - _RESET_CLOCK_GRACE:
                t -= _dt.timedelta(days=1)
            if t <= local_ref:
                if local_ref - t <= _RESET_CLOCK_GRACE:
                    return ref
                t += _dt.timedelta(days=1)
            return t.astimezone(_dt.timezone.utc)
    return None


class QuotaBook:
    """Ajan soğumaları. Koşular (ve projeler) arasında paylaşılır."""

    def __init__(self, path: Path | None = None):
        self.path = path or (jev_home() / "kota.json")
        self._lock = threading.Lock()

    def _load(self) -> dict:
        d = read_json(self.path, {}) or {}
        return d if isinstance(d, dict) else {}

    def mark(self, agent: str, until: _dt.datetime, reason: str = "") -> None:
        with self._lock:
            d = self._load()
            d[agent] = {"until": iso(until), "reason": reason[:300], "set_at": iso()}
            atomic_write_json(self.path, d)

    def clear(self, agent: str | None = None) -> None:
        with self._lock:
            d = self._load()
            if agent is None:
                d = {}
            else:
                d.pop(agent, None)
            atomic_write_json(self.path, d)

    def until(self, agent: str, at: _dt.datetime | None = None) -> _dt.datetime | None:
        rec = self._load().get(agent)
        if not rec:
            return None
        t = parse_iso(rec.get("until"))
        if t is None or t <= (at or now()):
            return None
        return t

    def available(self, agent: str, at: _dt.datetime | None = None) -> bool:
        return self.until(agent, at) is None

    def all(self) -> dict[str, dict]:
        out = {}
        for a, rec in self._load().items():
            t = parse_iso(rec.get("until"))
            if t and t > now():
                out[a] = rec
        return out


def log_unknown_error(agent: str, text: str) -> None:
    try:
        p = jev_home() / "bilinmeyen-hatalar.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(f"--- {iso()} {agent}\n{text.strip()[:4000]}\n")
    except OSError:
        pass
