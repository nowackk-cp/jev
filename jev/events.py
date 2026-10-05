"""Olay kanalı: her olay hem events.jsonl'a yazılır hem de abonelere yayınlanır.

Zarf: {"seq", "ts", "run", "type", "data"}. İş parçacıkları arasında güvenlidir;
her abonenin kendi kuyruğu vardır. Dinleyiciler (listener) olay sırasıyla ve
kilit altında çağrılır; hızlı olmalı ve olay yaymamalıdır.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from .util import ends_mid_line, iso, read_jsonl

EVENT_TYPES = {
    "run.phase", "task.update", "agent.state", "agent.activity", "jev.dispatch", "jev.consult",
    "jev.decision", "quota", "verify", "guard.blocked", "report.ready", "log", "run.info", "agent.usage",
    "run.scale",
}


class EventBus:
    def __init__(self, run_id: str, path: Path | None = None):
        self.run_id = run_id
        self.path = path
        self._lock = threading.RLock()
        self._history: list[dict] = []
        self._seq = 0
        self._subs: list[queue.Queue] = []
        self._listeners: list[Callable[[dict], None]] = []
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._history = read_jsonl(path)
                if self._history:
                    self._seq = max(int(e.get("seq", 0)) for e in self._history)
            torn = ends_mid_line(path)  # önceki süreç bir olayı yazarken öldü: yarım satır kapatılır
            self._fh = path.open("a", encoding="utf-8", newline="\n")
            if torn:
                self._fh.write("\n")
                self._fh.flush()

    # --- yayın -------------------------------------------------------------
    def emit(self, type_: str, data: dict | None = None) -> dict:
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": iso(), "run": self.run_id, "type": type_, "data": data or {}}
            if self._fh is not None:
                self._fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                self._fh.flush()
            self._history.append(ev)
            for q in list(self._subs):
                q.put(ev)
            for fn in list(self._listeners):
                try:
                    fn(ev)
                except Exception:  # dinleyici hatası akışı durdurmasın
                    pass
            return ev

    def add_listener(self, fn: Callable[[dict], None], replay: bool = False) -> None:
        with self._lock:
            if replay:
                for ev in self._history:
                    try:
                        fn(ev)
                    except Exception:
                        pass
            self._listeners.append(fn)

    # --- abonelik ----------------------------------------------------------
    def subscribe(self, from_seq: int = 0) -> tuple[queue.Queue, list[dict]]:
        """Yeni bir kuyruk ve `from_seq`'ten sonraki geçmiş olayları döndürür (kayıpsız)."""
        with self._lock:
            q: queue.Queue = queue.Queue()
            backlog = [e for e in self._history if e["seq"] > from_seq]
            self._subs.append(q)
            return q, backlog

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self._history if e["seq"] > seq]

    @property
    def last_seq(self) -> int:
        return self._seq

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def short(data: Any, n: int = 200) -> str:
    s = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"
