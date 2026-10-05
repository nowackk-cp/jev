"""Olay kanalı ve ofis sunucusu (belirtim §7).

- Olay kanalı: sıra numarası, events.jsonl'a yazma ve yeniden açınca kaldığı sıradan devam, kayıpsız abonelik.
- Sunucu: erişim anahtarı, Host/Origin denetimi, düğme komutları ve hata kodları (403/404/405/409/413/415).
- Canlı akış (SSE): `?from=` ve `Last-Event-ID` ile kaldığı yerden, boşluksuz ve tekrarsız devam.
- Sızıntı yok: yanıtlarda ve akışta erişim anahtarı, ui_url, dosya yolları ve ham ajan çıktısı bulunmaz;
  rapor HTML'i kaçışlıdır.
"""
import ast
import datetime as dt
import http.client
import json
import threading
import time
import types
import unittest

import _ortak
from jev.events import EVENT_TYPES, EventBus
from jev.office import snapshot
from jev.office.server import MAX_BODY, OfficeServer
from jev.phases.fix import NOTHING_TO_FIX
from jev.phases.review import report_name
from jev.state import TYPE_TR
from jev.util import atomic_write_json, atomic_write_text, iso, now, read_jsonl


# --- HTTP yardımcıları --------------------------------------------------------------------

def call(port: int, method: str, path: str, *, headers: dict | None = None, body: bytes | None = None):
    """Tek istek (sunucu HTTP/1.0 konuşur: her yanıttan sonra bağlantıyı kapatır). (durum, yanıt, gövde)"""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r, r.read()
    finally:
        c.close()


def js(raw: bytes):
    return json.loads(raw.decode("utf-8"))


class Sse:
    """Canlı akışı okuyan küçük istemci; tarayıcıdaki EventSource'un gördüğü blokları döndürür."""

    def __init__(self, port: int, path: str, headers: dict | None = None, timeout: float = 10):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        self.conn.request("GET", path, headers=headers or {})
        self.resp = self.conn.getresponse()

    def block(self) -> dict | None:
        """Bir sonraki blok ({"id", "data"} ya da {"retry"} ya da {"event", "data"}); akış kapandıysa None.
        `:` ile başlayan canlılık satırları atlanır."""
        cur: dict = {}
        while True:
            line = self.resp.readline()
            if not line:
                return cur or None
            line = line.decode("utf-8").rstrip("\r\n")
            if not line:
                if cur:
                    return cur
                continue
            if line.startswith(":"):
                continue
            k, _, v = line.partition(":")
            cur[k] = v[1:] if v.startswith(" ") else v

    def events(self, n: int) -> list[dict]:
        """Sıradaki `n` olay (zarflar); her bloğun `id`si olayın sırasıdır."""
        out = []
        while len(out) < n:
            b = self.block()
            if b is None or b.get("event") == "kapandi":
                raise AssertionError(f"akış {len(out)} olaydan sonra kapandı")
            if "id" in b:
                ev = json.loads(b["data"])
                if int(b["id"]) != ev["seq"]:
                    raise AssertionError(f"id {b['id']} ≠ seq {ev['seq']}")
                out.append(ev)
        return out

    def close(self) -> None:
        # HTTP/1.0 yanıtında soket yanıt nesnesine geçer: yalnızca bağlantıyı kapatmak soketi açık bırakır
        self.resp.close()
        self.conn.close()


def path_forms(p) -> list[str]:
    """Bir yolun yanıtlarda görünebileceği biçimleri: ters bölüleri, düz bölüleri ve JSON kaçışlı hâli."""
    s = str(p)
    return [s, p.as_posix(), json.dumps(s)[1:-1]]


# --- olay kanalı --------------------------------------------------------------------------

class EventBusTests(unittest.TestCase):
    def test_envelope_and_since(self):
        bus = EventBus("r1")
        e1 = bus.emit("log", {"level": "info", "text": "a"})
        e2 = bus.emit("run.phase")
        self.assertEqual(set(e1), {"seq", "ts", "run", "type", "data"})
        self.assertEqual((e1["seq"], e2["seq"], bus.last_seq), (1, 2, 2))
        self.assertEqual((e2["run"], e2["type"], e2["data"]), ("r1", "run.phase", {}))
        self.assertEqual([e["seq"] for e in bus.since(0)], [1, 2])
        self.assertEqual(bus.since(1), [e2])
        self.assertEqual(bus.since(2), [])
        bus.history().clear()  # kopya döner
        self.assertEqual(bus.history(), [e1, e2])

    def test_file_survives_restart(self):
        p = _ortak.temp_dir("olay") / "kosu" / "events.jsonl"
        bus = EventBus("r1", p)  # klasörü kendisi açar
        bus.emit("log", {"text": "çalışma ağacı"})
        bus.emit("log", {"text": "iki"})
        bus.close()
        bus.close()  # ikinci kez kapatmak zararsız
        self.assertIn("çalışma ağacı", p.read_text(encoding="utf-8"))  # Türkçe karakterler kaçışsız
        again = EventBus("r1", p)  # `jev devam`: numaralar kaldığı yerden sürer, akış yeniden oynatılabilir
        try:
            self.assertEqual((again.last_seq, [e["seq"] for e in again.history()]), (2, [1, 2]))
            self.assertEqual(again.emit("log")["seq"], 3)
        finally:
            again.close()
        self.assertEqual([e["seq"] for e in read_jsonl(p)], [1, 2, 3])

    def test_restart_after_a_torn_line(self):
        p = _ortak.temp_dir("olay") / "events.jsonl"
        bus = EventBus("r1", p)
        bus.emit("log", {"text": "bir"})
        bus.close()
        with p.open("ab") as f:  # süreç öldürüldü: son olay yarım kaldı
            f.write(b'{"seq": 2, "ts": "2026-')
        again = EventBus("r1", p)
        try:
            self.assertEqual(again.last_seq, 1)
            self.assertEqual(again.emit("log", {"text": "üç"})["seq"], 2)  # yarım olayın numarası yeniden kullanılır
        finally:
            again.close()
        self.assertEqual([(e["seq"], e["data"]["text"]) for e in read_jsonl(p)], [(1, "bir"), (2, "üç")])

    def test_subscribe_backlog_then_live(self):
        bus = EventBus("r1")
        for i in range(5):
            bus.emit("log", {"i": i})
        q, backlog = bus.subscribe(3)
        self.assertEqual([e["seq"] for e in backlog], [4, 5])
        self.assertTrue(q.empty())
        bus.emit("log")
        self.assertEqual(q.get_nowait()["seq"], 6)
        bus.unsubscribe(q)
        bus.unsubscribe(q)
        bus.emit("log")
        self.assertTrue(q.empty())

    def test_concurrent_emits_are_numbered_once(self):
        p = _ortak.temp_dir("olay") / "events.jsonl"
        bus = EventBus("r1", p)

        def pump():
            for _ in range(250):
                bus.emit("log", {"text": "x" * 50})

        threads = [threading.Thread(target=pump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bus.close()
        want = list(range(1, 1001))
        self.assertEqual([e["seq"] for e in bus.history()], want)
        self.assertEqual([e["seq"] for e in read_jsonl(p)], want)  # satırlar iç içe geçmedi

    def test_subscribing_mid_stream_loses_nothing(self):
        """Abone olunurken yayılan olay ya geçmişte ya kuyrukta olur: kaybolmaz, iki kez gelmez."""
        bus = EventBus("r1")
        stop = threading.Event()

        def pump():
            while not stop.is_set():
                bus.emit("log")

        threads = [threading.Thread(target=pump) for _ in range(3)]
        for t in threads:
            t.start()
        try:
            while bus.last_seq < 200:
                time.sleep(0.001)
            q, backlog = bus.subscribe(50)
            mark = bus.last_seq
            while bus.last_seq < mark + 200:
                time.sleep(0.001)
        finally:
            stop.set()
            for t in threads:
                t.join()
        got = [e["seq"] for e in backlog]
        while not q.empty():
            got.append(q.get_nowait()["seq"])
        self.assertEqual(got, list(range(51, bus.last_seq + 1)))

    def test_listeners(self):
        bus = EventBus("r1")
        bus.emit("log", {"n": 1})
        seen, replayed = [], []

        def boom(ev):
            raise RuntimeError("dinleyici hatası")

        bus.add_listener(boom, replay=True)  # hatalı dinleyici akışı durdurmaz
        bus.add_listener(seen.append)
        bus.add_listener(replayed.append, replay=True)
        bus.emit("log", {"n": 2})
        self.assertEqual([e["seq"] for e in seen], [2])
        self.assertEqual([e["seq"] for e in replayed], [1, 2])

    def test_emitted_types_are_declared(self):
        found = set()
        for path in (_ortak.ROOT / "jev").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                arg = node.args[0]
                if name == "emit" and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
        self.assertGreater(len(found), 8)
        self.assertLessEqual(found, EVENT_TYPES)


# --- anlık görüntü yardımcıları -----------------------------------------------------------

class SnapshotHelpers(unittest.TestCase):
    def test_deviation(self):
        for value, want in ((True, "minor"), ("minor", "minor"), ("major", "major"), (False, "none"),
                            (None, "none"), ("büyük", "none")):
            with self.subTest(value=value):
                self.assertEqual(snapshot._deviation({"deviation": value}), want)
        self.assertEqual(snapshot._deviation({}), "none")

    def test_feed_worthy(self):
        def ev(type_, **data):
            return {"type": type_, "data": data}

        yes = [ev("jev.decision", decision="retry"), ev("log", level="warn", text="x"), ev("quota", agent="sol"),
               ev("task.update", status="done"), ev("task.update", status="needs_decision"),
               ev("report.ready", round=1)]
        no = [ev("log", level="debug", text="x"), ev("task.update", status="running"),
              ev("agent.activity", kind="tool"), ev("agent.state", state="working"), ev("agent.usage")]
        self.assertEqual([snapshot._feed_worthy(e) for e in yes], [True] * len(yes))
        self.assertEqual([snapshot._feed_worthy(e) for e in no], [False] * len(no))

    def test_normalize_state(self):
        norm = snapshot._normalize_state
        future, past = iso(now() + dt.timedelta(hours=1)), iso(now() - dt.timedelta(minutes=1))
        s = norm(None, "executing", None)
        self.assertEqual((s["state"], s["text"], s["task_id"]), ("idle", "", None))

        working = {"state": "working", "text": "T04 yazıyor", "task_id": "T04", "until": None, "seq": 5}
        self.assertEqual(norm(working, "executing", None), working)
        for phase in ("reported", "paused", "awaiting_approval", "aborted"):  # koşu bekliyor: herkes dinlenir
            s = norm(working, phase, None)
            self.assertEqual((s["state"], s["text"], s["task_id"]), ("idle", "", None), phase)
        self.assertEqual(working["state"], "working")  # girdi değişmez

        done = {"state": "done", "text": "T04 bitti!", "task_id": "T04"}
        self.assertEqual(norm(done, "executing", None)["text"], "T04 bitti!")
        waiting = {"state": "idle", "text": "raporu bekliyor", "task_id": None}
        self.assertEqual(norm(waiting, "executing", None)["text"], "raporu bekliyor")
        self.assertEqual(norm(waiting, "reported", None)["text"], "")

        asleep = {"state": "sleeping", "text": "Kota doldu", "until": future, "task_id": None}
        self.assertEqual(norm(asleep, "paused", None)["state"], "sleeping")  # dinlenme aşamasında da uyur
        s = norm({**asleep, "until": past}, "executing", None)  # süresi geçti: uyanır
        self.assertEqual((s["state"], s["text"]), ("idle", ""))

        quota = {"until": future, "hhmm": "12:00", "reason": "kullanım sınırı"}
        s = norm(None, "executing", quota)
        self.assertEqual((s["state"], s["until"], s["text"]), ("sleeping", future, "Kota doldu"))
        self.assertEqual(norm(working, "executing", quota)["state"], "working")  # kota yalnızca boştakini uyutur
        self.assertEqual(norm(working, "reported", quota)["state"], "sleeping")

    def test_plan_view_without_plan(self):
        stub = types.SimpleNamespace(plan={}, rdir=_ortak.temp_dir("plansiz"))
        self.assertEqual(snapshot.plan_view(stub), {"ok": False, "mesaj": "Plan henüz hazır değil."})


# --- sunucu -------------------------------------------------------------------------------

_SHARED: dict = {}


def _office():
    """Planı yazılmış bir kuru koşu ve onun ofis sunucusu; modüldeki sunucu testleri paylaşır."""
    if not _SHARED:
        run = _ortak.planned_run()
        office = OfficeServer(run, port=_ortak.free_port())
        office.start()
        run.server, run.url = office, office.url  # Runner.start_office gibi: adres durumda da durur
        run.state["ui_url"] = office.url
        _SHARED.update(run=run, office=office)
    return _SHARED["run"], _SHARED["office"]


def tearDownModule():
    if _SHARED:
        _SHARED["office"].stop()
        _SHARED["run"].bus.close()


class _Office(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner, cls.office = _office()
        cls.port = cls.office.port

    def setUp(self):
        run = self.runner
        run.state["phase"] = "executing"
        run.state["reports"] = []
        run._pending = False
        self.queued()

    def queued(self) -> list[tuple]:
        out = []
        while not self.runner.controls.empty():
            out.append(self.runner.controls.get_nowait())
        return out

    def get(self, path: str, *, key: bool = True, headers: dict | None = None):
        h = {"X-Jev-Key": self.office.key} if key else {}
        h.update(headers or {})
        return call(self.port, "GET", path, headers=h)

    def post(self, cmd: str, body=None, *, key: bool = True, headers: dict | None = None, path: str | None = None):
        h = {"X-Jev-Key": self.office.key} if key else {}
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        return call(self.port, "POST", path or f"/api/komut/{cmd}", headers=h, body=body)

    def write_report(self, round_: int, verdict: str, *, gaps=(), md: str = "# Rapor\n") -> None:
        run, name = self.runner, report_name(round_)
        atomic_write_json(run.rdir / f"{name}.json", {
            "verdict": verdict, "summary": f"Tur {round_} özeti", "gaps": list(gaps),
            "criteria": [{"id": "C1", "status": "met", "evidence": "testler geçti"}], "risks": [], "next_steps": []})
        atomic_write_text(run.rdir / f"{name}.md", md)
        run.state["reports"].append({"round": round_, "file": f"{name}.md", "verdict": verdict,
                                     "summary": f"Tur {round_} özeti", "ts": iso()})


class AccessTests(_Office):
    def test_static_page_is_hardened_and_needs_no_key(self):
        st, r, raw = call(self.port, "GET", "/")
        self.assertEqual((st, r.getheader("Content-Type")), (200, "text/html; charset=utf-8"))
        for h, v in {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                     "X-Frame-Options": "DENY", "Cache-Control": "no-cache"}.items():
            self.assertEqual(r.getheader(h), v, h)
        csp = r.getheader("Content-Security-Policy")
        for part in ("script-src 'self'", "frame-ancestors 'none'", "form-action 'none'", "base-uri 'none'"):
            self.assertIn(part, csp)
        self.assertNotIn(self.office.key.encode(), raw)
        st, r, raw = call(self.port, "HEAD", "/office.js")
        self.assertEqual((st, raw), (200, b""))
        self.assertGreater(int(r.getheader("Content-Length")), 0)
        self.assertEqual(call(self.port, "GET", "/favicon.ico")[0], 204)

    def test_api_needs_the_key(self):
        k = self.office.key
        wrong = k[:-1] + ("A" if k[-1] != "A" else "B")
        st, r, raw = self.get("/api/durum", key=False)
        self.assertEqual((st, js(raw)), (403, {"ok": False, "mesaj": "Erişim anahtarı geçersiz. Terminaldeki ofis "
                                                                     "adresini aç."}))
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        for path, headers in (("/api/durum", {"X-Jev-Key": wrong}), (f"/api/durum?k={wrong}", {}),
                              ("/api/durum?k=", {}), ("/api/olaylar", {}), ("/api/ajan/opus/log", {})):
            with self.subTest(path=path.replace(wrong, "<yanlis>"), header=bool(headers)):
                self.assertEqual(self.get(path, key=False, headers=headers)[0], 403)
        self.assertEqual(self.get(f"/api/durum?k={k}", key=False)[0], 200)  # sayfa adresindeki anahtar
        self.assertEqual(self.get("/api/durum")[0], 200)  # başlıktaki anahtar

    def test_host_header_is_checked(self):  # DNS rebinding
        for host in ("evil.example", f"evil.example:{self.port}", f"127.0.0.1:{self.port + 1}", "127.0.0.1"):
            with self.subTest(host=host):
                st, _, raw = self.get("/api/durum", headers={"Host": host})
                self.assertEqual((st, js(raw)["mesaj"]), (403, "Geçersiz Host başlığı."))
        self.assertEqual(call(self.port, "GET", "/", headers={"Host": "evil.example"})[0], 403)
        for host in (f"localhost:{self.port}", f"LOCALHOST:{self.port}"):
            self.assertEqual(self.get("/api/durum", headers={"Host": host})[0], 200)

    def test_unknown_paths(self):
        st, _, raw = self.get("/gizli.txt", key=False)
        self.assertEqual((st, js(raw)), (404, {"ok": False, "mesaj": "Böyle bir sayfa yok."}))
        # ajan adı yalnızca bilinen adlardan biri olabilir; adres hiçbir zaman dosya yolu olarak kullanılmaz
        for path, msg in (("/api/yok", "Böyle bir adres yok."), ("/api/ajan/yok/log", "Böyle bir ajan yok."),
                          ("/api/ajan/OPUS/log", "Böyle bir adres yok."),
                          ("/api/ajan/..%2Fstate/log", "Böyle bir adres yok."),
                          ("/api/ajan/../state.json", "Böyle bir adres yok.")):
            with self.subTest(path=path):
                st, _, raw = self.get(path)
                self.assertEqual((st, js(raw)["mesaj"]), (404, msg))

    def test_agent_log_tail(self):
        for i in range(3):
            self.runner.emit("agent.state", {"agent": "luna", "state": "working", "text": f"satır {i}",
                                             "task_id": "T05"})

        def lines(query):
            return js(self.get(f"/api/ajan/luna/log{query}")[2])["lines"]

        self.assertEqual([ln["text"] for ln in lines("?tail=2")], ["satır 1", "satır 2"])
        self.assertEqual(len(lines("?tail=0")), 1)  # en az bir satır
        self.assertEqual(lines("?tail=abc"), lines(""))  # geçersizse varsayılan


class CommandTests(_Office):
    def test_devam_is_accepted_once(self):
        self.runner.state["phase"] = "paused"
        st, _, raw = self.post("devam")
        self.assertEqual((st, js(raw)), (200, {"ok": True, "mesaj": "Tamam."}))
        st, _, raw = self.post("devam")
        self.assertEqual((st, js(raw)), (409, {"ok": False, "mesaj": "Önceki komut işleniyor."}))
        self.assertEqual(self.queued(), [("devam", "", "arayuz")])

    def test_buttons_only_in_their_phase(self):
        for cmd in ("duzelt", "onayla", "reddet", "devam"):
            st, _, raw = self.post(cmd)
            self.assertEqual((st, js(raw)["mesaj"]), (409, "Bu komut şu an kullanılamaz (aşama: Uygulama)."), cmd)
        self.assertEqual(self.queued(), [])

    def test_approval_buttons_reach_the_orchestrator(self):
        run = self.runner
        run.state["phase"] = "awaiting_approval"
        self.assertEqual(self.post("onayla")[0], 200)
        self.assertEqual(run.next_command(), ("onayla", ""))  # orkestratör aldı: sıradaki komut kabul edilir
        self.assertEqual(self.post("reddet", {"not": "yok sayılır"})[0], 200)
        self.assertEqual(run.next_command(), ("reddet", ""))
        self.assertIn("Arayüzden komut: onayla", run.term.text())

    def test_request_checks(self):
        self.runner.state["phase"] = "paused"
        k, p = self.office.key, self.port
        cases = [
            (dict(key=False, path=f"/api/komut/devam?k={k}"), 403, "Erişim anahtarı geçersiz."),  # yalnızca başlık
            (dict(headers={"Origin": "http://evil.example"}), 403, "Geçersiz kaynak (Origin)."),
            (dict(headers={"Origin": "null"}), 403, "Geçersiz kaynak (Origin)."),
            (dict(headers={"Origin": f"http://127.0.0.1:{p + 1}"}), 403, "Geçersiz kaynak (Origin)."),
            (dict(headers={"Host": "evil.example"}), 403, "Geçersiz Host başlığı."),
            (dict(path="/api/komut/sil"), 404, "Bilinmeyen komut."),
            (dict(path="/api/komut/"), 404, "Bilinmeyen komut."),
            (dict(path="/api/baska"), 404, "Böyle bir adres yok."),
            (dict(body=b"devam", headers={"Content-Type": "text/plain"}), 415, "Gövde JSON olmalı."),
            (dict(body=b"{bozuk", headers={"Content-Type": "application/json"}), 400, "Gövde okunamadı."),
            (dict(body=b"\xff\xfe{}", headers={"Content-Type": "application/json"}), 400, "Gövde okunamadı."),
            (dict(body=["liste"]), 400, "Gövde bir nesne olmalı."),
            # reddedilecek istek gövdeli de olsa yanıt istemciye ulaşır
            (dict(body={"not": "x"}, headers={"Origin": "http://evil.example"}), 403, "Geçersiz kaynak (Origin)."),
        ]
        for kw, code, msg in cases:
            with self.subTest(case=msg, kw=str(kw).replace(k, "<anahtar>")):
                st, _, raw = self.post("devam", **kw)
                self.assertEqual((st, js(raw)), (code, {"ok": False, "mesaj": msg}))
        self.assertEqual(self.queued(), [])
        st, _, _ = self.post("devam", {}, headers={"Origin": f"http://localhost:{p}",
                                                  "Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(st, 200)

    def test_body_too_large(self):
        self.runner.state["phase"] = "paused"
        for length in (str(MAX_BODY + 1), "abc", "-5"):
            with self.subTest(length=length):
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                try:
                    c.putrequest("POST", "/api/komut/devam")
                    c.putheader("X-Jev-Key", self.office.key)
                    c.putheader("Content-Type", "application/json")
                    c.putheader("Content-Length", length)
                    c.endheaders()  # gövde gönderilmez: sunucu uzunluğa bakıp hemen reddeder
                    r = c.getresponse()
                    self.assertEqual((r.status, js(r.read())["mesaj"]), (413, "İstek gövdesi çok büyük."))
                finally:
                    c.close()
        self.assertEqual(self.queued(), [])

    def test_duzelt(self):
        run = self.runner
        run.state["phase"] = "reported"
        self.write_report(1, "basarili")
        for body in (None, {}, {"not": "   "}):  # eksik yok ve not yok: açılacak tur yok, hemen söylenir
            st, _, raw = self.post("duzelt", body)
            self.assertEqual((st, js(raw)), (409, {"ok": False, "mesaj": NOTHING_TO_FIX}), body)
        self.assertEqual(self.queued(), [])

        note = "  Silmeden önce onay sor " + "ş" * 5000
        st, _, raw = self.post("duzelt", {"not": note})
        self.assertEqual((st, js(raw)), (200, {"ok": True, "mesaj": "Tamam."}))
        [(cmd, arg, source)] = self.queued()
        self.assertEqual((cmd, source, len(arg)), ("duzelt", "arayuz", 4000))  # kırpılır ve kısaltılır
        self.assertTrue(arg.startswith("Silmeden önce onay sor şş"))

        run._pending = False
        self.write_report(2, "kismen", gaps=[{"id": "G1", "criteria": ["C2"], "description": "Silme onaysız"}])
        self.assertEqual(self.post("duzelt")[0], 200)  # eksik var: not olmadan da tur açılır
        self.assertEqual(self.queued(), [("duzelt", "", "arayuz")])


class SnapshotTests(_Office):
    def test_durum(self):
        run = self.runner
        run.quota.mark("sonnet", now() + dt.timedelta(hours=1), "kullanım sınırı")
        try:
            st, _, raw = self.get("/api/durum")
        finally:
            run.quota.clear("sonnet")
        d = js(raw)
        self.assertEqual((st, d["ok"], d["last_seq"]), (200, True, run.bus.last_seq))
        self.assertEqual(d["labels"]["type"], TYPE_TR)
        agents = {a["name"]: a for a in d["agents"]}
        self.assertEqual(list(agents), ["opus", "sol", "sonnet", "luna", "jev"])
        self.assertEqual((agents["jev"]["brain_display"], agents["jev"]["brain"]), ("Opus", run.cfg.label("opus")))
        self.assertTrue(agents["jev"]["fallback"].endswith(" · xhigh"))
        # Opus planlar, kodu yalnızca takılan görevde (eskalasyon) yazar; Sol hem işçi hem son kontrolcü
        self.assertEqual(agents["opus"]["roles_tr"], ["Planlayıcı", "Beyin", "İşçi", "Eskalasyon"])
        self.assertEqual(agents["sol"]["roles_tr"], ["İşçi", "Denetçi"])
        self.assertEqual((agents["sonnet"]["state"]["state"], agents["sonnet"]["state"]["text"]),
                         ("sleeping", "Kota doldu"))
        self.assertEqual(agents["sonnet"]["quota"]["reason"], "kullanım sınırı")
        self.assertEqual([t["task_id"] for t in d["tasks"]], ["T01", "T02", "T03", "T04", "T05", "T06"])
        self.assertEqual(d["progress"], {"done": 0, "total": 6, "pct": 0})
        self.assertEqual(d["run"]["phase_tr"], "Uygulama")
        self.assertTrue(d["plan"]["success_criteria"])

    def test_controls_follow_phase(self):
        run = self.runner
        for phase, on in (("executing", set()), ("reported", {"duzelt"}), ("awaiting_approval", {"onayla", "reddet"}),
                          ("paused", {"devam"})):
            run.state["phase"] = phase
            c = snapshot.build(run)["controls"]
            self.assertEqual({k for k in ("duzelt", "onayla", "reddet", "devam") if c[k]}, on, phase)
            self.assertEqual((c["pending"], c["interactive"]), (False, False))  # testte rapordan sonra çıkar
        run._pending = True
        self.assertTrue(snapshot.build(run)["controls"]["pending"])

    def test_report_html_is_escaped_and_rounds_selectable(self):
        self.runner.state["phase"] = "reported"
        self.assertEqual(js(self.get("/api/rapor")[2]), {"ok": False, "mesaj": "Henüz rapor yok.", "reports": []})
        self.write_report(1, "kismen", md="# Tur 1\n\n<script>alert('x')</script>\n\n"
                                          "[tıkla](javascript:alert(1)) <img src=x onerror=alert(1)>\n")
        self.write_report(2, "basarili", md="# Tur 2\n\nHer şey **tamam**.\n")
        latest = js(self.get("/api/rapor")[2])
        self.assertEqual((latest["round"], latest["verdict_tr"], latest["summary"]), (2, "Başarılı", "Tur 2 özeti"))
        self.assertIn("<strong>tamam</strong>", latest["html"])
        self.assertEqual([r["round"] for r in latest["reports"]], [1, 2])
        for q in ("?tur=abc", "?tur=7", "?tur="):  # geçersiz tur: en sonuncusu
            self.assertEqual(js(self.get(f"/api/rapor{q}")[2])["round"], 2, q)
        first = js(self.get("/api/rapor?tur=1")[2])
        self.assertEqual((first["round"], first["criteria"][0]["status_tr"]), (1, "karşılandı"))
        html = first["html"]
        self.assertIn("&lt;script&gt;", html)
        for bad in ("<script", "<img", "<a ", "href="):
            self.assertNotIn(bad, html)

    def test_nothing_secret_in_responses(self):
        run = self.runner
        run.state["phase"] = "reported"
        self.write_report(1, "kismen", gaps=[{"id": "G1", "criteria": ["C1"], "description": "eksik"}])
        # gerçek çağrı kaydı gibi: yollar ve ham çıktı içerir; ajan günlüğüne yalnızca özeti geçmeli
        calls = run.rdir / "calls"
        atomic_write_json(calls / "050-worker-D1-01-luna.result.json", {
            "ok": False, "duration_s": 2.5, "error_kind": "verify_failed", "error_text": "2 test kaldı",
            "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}, "text": "HAM ÇIKTI",
            "paths": {"stdout": str(calls / "050-worker-D1-01-luna.stdout.jsonl")}})
        paths = ["/api/durum", "/api/plan", "/api/rapor"] + [f"/api/ajan/{a}/log?tail=400"
                                                             for a in snapshot.agent_names(run)]
        secrets = {"anahtar": [self.office.key], "ui_url": ["ui_url"], "koşu klasörü": path_forms(run.rdir),
                   "proje klasörü": path_forms(run.project), "ham çıktı": ["HAM ÇIKTI", ".stdout", "prompt.md"]}
        for p in paths:
            st, _, raw = self.get(p)
            body = raw.decode("utf-8")
            self.assertEqual(st, 200, p)
            for label, needles in secrets.items():
                self.assertFalse(any(n in body for n in needles), f"{p}: {label} yanıtta görünüyor")
        luna = js(self.get("/api/ajan/luna/log")[2])
        [c] = [c for c in luna["calls"] if c["seq"] == "050"]
        self.assertEqual((c["task"], c["phase_tr"], c["agent"], c["error_tr"]),
                         ("D1-01", "Görev", "luna", "doğrulama başarısız"))
        self.assertNotIn("paths", c)
        jev_calls = js(self.get("/api/ajan/jev/log")[2])["calls"]
        self.assertTrue(all(c["phase"] == "brain" for c in jev_calls))  # Jev'in günlüğünde yalnızca beyin çağrıları


class StreamTests(_Office):
    def stream(self, query: str = "", headers: dict | None = None) -> Sse:
        return Sse(self.port, f"/api/olaylar?k={self.office.key}{query}", headers)

    def emit_n(self, n: int) -> list[int]:
        return [self.runner.emit("log", {"level": "debug", "text": f"deneme {i}"})["seq"] for i in range(n)]

    def test_backlog_then_live(self):
        base = self.runner.bus.last_seq
        self.emit_n(3)
        s = self.stream(f"&from={base + 1}")
        try:
            self.assertEqual(s.resp.status, 200)
            self.assertEqual(s.resp.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            self.assertEqual(s.resp.getheader("Cache-Control"), "no-store")
            self.assertEqual(s.block(), {"retry": "3000"})
            self.assertEqual([e["seq"] for e in s.events(2)], [base + 2, base + 3])
            live = self.runner.emit("task.update", {"task_id": "T01", "status": "running", "title": "Çekirdek ağaç"})
            self.assertEqual(s.events(1), [live])
        finally:
            s.close()

    def test_last_event_id_wins_over_from(self):
        base = self.runner.bus.last_seq
        self.emit_n(4)
        s = self.stream(f"&from={base}", {"Last-Event-ID": str(base + 3)})
        try:
            self.assertEqual([e["seq"] for e in s.events(1)], [base + 4])
        finally:
            s.close()

    def test_bad_position_replays_everything(self):
        for q in ("&from=abc", "&from=-7", ""):
            s = self.stream(q)
            try:
                self.assertEqual(s.events(1)[0]["seq"], 1, q)
            finally:
                s.close()

    def test_reconnect_has_no_gap_or_duplicate(self):
        """Sayfa yenilenir ya da bağlantı kopar: EventSource son `id`yi Last-Event-ID olarak gönderir."""
        base = self.runner.bus.last_seq
        got = []
        s = self.stream(f"&from={base}")
        try:
            self.emit_n(5)
            got += s.events(5)
        finally:
            s.close()
        self.emit_n(4)  # bağlantı yokken
        s = self.stream(f"&from={base}", {"Last-Event-ID": str(got[-1]["seq"])})
        try:
            got += s.events(4)
            self.emit_n(2)
            got += s.events(2)
        finally:
            s.close()
        self.assertEqual([e["seq"] for e in got], list(range(base + 1, base + 12)))

    def test_head_is_not_a_stream(self):
        st, _, raw = call(self.port, "HEAD", f"/api/olaylar?k={self.office.key}")
        self.assertEqual((st, raw), (405, b""))

    def test_closed_clients_are_unsubscribed(self):
        bus = self.runner.bus
        s = self.stream(f"&from={bus.last_seq}")
        self.assertEqual(s.block(), {"retry": "3000"})  # sunucu başlıkları göndermeden abone olur
        self.assertGreaterEqual(len(bus._subs), 1)
        s.close()
        deadline = time.monotonic() + 10
        while bus._subs and time.monotonic() < deadline:
            self.emit_n(1)  # kopan istemci yazma hatasıyla fark edilir
            time.sleep(0.05)
        self.assertEqual(bus._subs, [])

    def test_stop_ends_the_stream(self):
        office = OfficeServer(self.runner, port=self.port)  # port dolu: sıradakine geçer
        office.start()
        s = Sse(office.port, f"/api/olaylar?k={office.key}&from={self.runner.bus.last_seq}")
        try:
            self.assertGreater(office.port, self.port)
            self.assertNotEqual(office.key, self.office.key)
            self.assertEqual(s.block(), {"retry": "3000"})
            office.stop()
            self.assertEqual(s.block(), {"event": "kapandi", "data": "{}"})  # istemci yeniden bağlanmayı bırakır
            self.assertIsNone(s.block())
        finally:
            s.close()
            office.stop()


# --- canlı koşu ---------------------------------------------------------------------------

@_ortak.slow
class LiveRun(unittest.TestCase):
    """Ofis, sorunlu bir kuru koşuyu (beyin kararı, kota, bozuk JSON) baştan sona kayıpsız izler."""

    def test_office_follows_a_dry_run(self):
        run = _ortak.make_run(speed=1000.0)
        office = OfficeServer(run, port=_ortak.free_port())
        office.start()
        run.state["ui_url"] = office.url
        k, port = office.key, office.port
        steady, flaky, errors = [], [], []

        def follow():  # baştan sona tek bağlantı
            try:
                s = Sse(port, f"/api/olaylar?k={k}&from=0", timeout=120)
                try:
                    while (b := s.block()) is not None and b.get("event") != "kapandi":
                        if "id" in b:
                            steady.append(json.loads(b["data"]))
                finally:
                    s.close()
            except Exception as e:  # noqa: BLE001 — ana iş parçacığında raporlanır
                errors.append(repr(e))

        def follow_flaky():  # her 40 olayda kopup Last-Event-ID ile yeniden bağlanan istemci
            last = 0
            try:
                while True:
                    try:
                        s = Sse(port, f"/api/olaylar?k={k}&from=0", {"Last-Event-ID": str(last)}, timeout=120)
                    except ConnectionError:  # sunucu kapandı (bağlantı reddedildi ya da kapanırken koptu)
                        return
                    try:
                        n = 0
                        while n < 40:
                            b = s.block()
                            if b is None or b.get("event") == "kapandi":
                                return
                            if "id" in b:
                                ev = json.loads(b["data"])
                                flaky.append(ev)
                                last, n = ev["seq"], n + 1
                    finally:
                        s.close()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=follow, daemon=True), threading.Thread(target=follow_flaky, daemon=True)]
        for t in threads:
            t.start()
        try:
            self.assertEqual(run.drive(), "reported")
            bodies = {p: self.fetch(port, k, p) for p in
                      ["/api/durum", "/api/plan", "/api/rapor"] + [f"/api/ajan/{a}/log?tail=400"
                                                                   for a in snapshot.agent_names(run)]}
            final = run.bus.last_seq
            deadline = time.monotonic() + 20  # iki izleyici de son olaya yetişsin, sonra sunucu kapansın
            while (not all(got and got[-1]["seq"] >= final for got in (steady, flaky))
                   and all(t.is_alive() for t in threads) and time.monotonic() < deadline):
                time.sleep(0.05)
        finally:
            office.stop()
            for t in threads:
                t.join(20)
            run.bus.close()

        self.assertEqual(errors, [])
        hist = json.loads(json.dumps(run.bus.history(), ensure_ascii=False))
        want = list(range(1, run.bus.last_seq + 1))
        self.assertEqual([e["seq"] for e in steady], want)
        self.assertEqual([e["seq"] for e in flaky], want)
        self.assertEqual(steady, hist)
        self.assertLessEqual({"quota", "jev.decision", "report.ready"}, {e["type"] for e in steady})

        d = js(bodies["/api/durum"])
        self.assertEqual((d["run"]["phase"], d["progress"]["pct"], d["last_seq"]), ("reported", 100, want[-1]))
        self.assertTrue(d["controls"]["duzelt"])
        brains = {e["data"]["brain"] for e in d["decisions"]}
        self.assertEqual(brains, {"jev"})  # sorunlara Jev modeli karar verdi; beyin çağrılmadı
        rep = js(bodies["/api/rapor"])
        self.assertEqual((rep["ok"], rep["round"]), (True, 1))
        self.assertTrue(rep["html"])
        jev_log = js(bodies["/api/ajan/jev/log?tail=400"])
        self.assertIn("jev", {x["brain"] for x in jev_log["decisions"]})

        # proje klasörü raporda yalnızca dalı birleştirme komutunda geçer (kullanıcı kopyalayıp çalıştırır)
        proj = str(run.project)
        merge = [ln for ln in rep["html"].splitlines() if proj in ln]
        self.assertEqual(len(merge), 1)
        self.assertIn("cd ", merge[0])
        rep["html"] = "\n".join(ln for ln in rep["html"].splitlines() if proj not in ln)

        secrets = {"anahtar": [k], "ui_url": ["ui_url"], "proje klasörü": path_forms(run.project),
                   "ham çıktı": [".stdout", "prompt.md"]}
        texts = {**{p: b.decode("utf-8") for p, b in bodies.items() if p != "/api/rapor"},
                 "/api/rapor": json.dumps(rep, ensure_ascii=False),
                 **{f"olay {e['seq']} ({e['type']})": json.dumps(e, ensure_ascii=False) for e in steady}}
        for where, text in texts.items():
            for label, needles in secrets.items():
                self.assertFalse(any(n in text for n in needles), f"{where}: {label} görünüyor")

    @staticmethod
    def fetch(port: int, key: str, path: str) -> bytes:
        st, _, raw = call(port, "GET", path, headers={"X-Jev-Key": key})
        if st != 200:
            raise AssertionError(f"{path}: {st}")
        return raw


if __name__ == "__main__":
    unittest.main()
