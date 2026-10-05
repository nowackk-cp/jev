"""Ofis arayüzü sunucusu (belirtim §7): stdlib HTTP + Server-Sent Events.

Orkestratörle aynı süreçte, ayrı bir iş parçacığında çalışır. Yalnızca 127.0.0.1'e bağlanır.
Her istek rastgele erişim anahtarı ister (`?k=` ya da `X-Jev-Key`); düğmeler (POST) yalnızca başlıkla kabul edilir.
Canlı akış `/api/olaylar` — sayfa yenilenirse `Last-Event-ID` / `?from=` ile kaldığı sıradan devam eder.
"""
from __future__ import annotations

import hmac
import json
import queue
import re
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import snapshot

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/office.css": ("office.css", "text/css; charset=utf-8"),
    "/office.js": ("office.js", "text/javascript; charset=utf-8"),
    "/characters.js": ("characters.js", "text/javascript; charset=utf-8"),
    "/scene.js": ("scene.js", "text/javascript; charset=utf-8"),
}
COMMANDS = {"duzelt", "onayla", "reddet", "devam"}
PORT_TRIES = 20
PING_S = 15.0
MAX_BODY = 64_000
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_AGENT_LOG = re.compile(r"^/api/ajan/([a-z0-9_-]{1,32})/log$")


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # Windows'ta SO_REUSEADDR aynı porta ikinci bağlanmaya izin verir
    daemon_threads = True
    block_on_close = False  # kapanışta açık SSE bağlantılarını beklemesin

    def __init__(self, addr, handler, office: "OfficeServer"):
        self.office = office
        super().__init__(addr, handler)


class OfficeServer:
    def __init__(self, runner, port: int = 8765, host: str = "127.0.0.1"):
        self.runner = runner
        self.host = host
        self.port = int(port)
        self.key = secrets.token_urlsafe(24)
        self.stopping = threading.Event()
        self.httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?k={self.key}"

    def start(self) -> str:
        last: OSError | None = None
        for p in range(self.port, self.port + PORT_TRIES):
            try:
                self.httpd = _Server((self.host, p), _Handler, self)
            except OSError as e:  # port dolu
                last = e
                continue
            self.port = p
            break
        else:
            raise OSError(f"{self.port}-{self.port + PORT_TRIES - 1} arasında boş port yok ({last})")
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.25},
                                        name="jev-ofis", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        self.stopping.set()
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            finally:
                self.httpd.server_close()
            self.httpd = None

    def check_key(self, given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(given.encode("utf-8"), self.key.encode("utf-8"))


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.0"
    server_version = "JevOfis/1"
    sys_version = ""

    # --- yardımcılar -------------------------------------------------------
    @property
    def office(self) -> OfficeServer:
        return self.server.office

    def log_message(self, format, *args) -> None:  # noqa: A002 — terminali kirletmesin
        pass

    def _host_ok(self) -> bool:
        """DNS rebinding'e karşı: Host başlığı yalnızca 127.0.0.1 / localhost olabilir."""
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.office.port
        return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return origin is None
        port = self.office.port
        return origin.lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _common_headers(self, ctype: str, length: int | None, cache: bool = False) -> None:
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")

    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(code)
        self._common_headers(ctype, len(body), cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, text: str) -> None:
        self._json(code, {"ok": False, "mesaj": text})

    def _query(self) -> dict[str, str]:
        q = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        return {k: v[-1] for k, v in q.items()}

    # --- GET ---------------------------------------------------------------
    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass

    def _route_get(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Geçersiz Host başlığı.")
        path = urlsplit(self.path).path
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return None
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            f = STATIC_DIR / name
            if not f.is_file():
                return self._error(HTTPStatus.NOT_FOUND, f"{name} bulunamadı.")
            return self._send(HTTPStatus.OK, f.read_bytes(), ctype, cache=True)
        if not path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "Böyle bir sayfa yok.")
        q = self._query()
        if not self.office.check_key(self.headers.get("X-Jev-Key") or q.get("k")):
            return self._error(HTTPStatus.FORBIDDEN, "Erişim anahtarı geçersiz. Terminaldeki ofis adresini aç.")
        run = self.office.runner
        if path == "/api/durum":
            return self._json(HTTPStatus.OK, snapshot.build(run))
        if path == "/api/olaylar":
            if self.command == "HEAD":  # akış HEAD ile açılmaz
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Akış yalnızca GET ile açılır.")
            return self._stream(q)
        if path == "/api/rapor":
            return self._json(HTTPStatus.OK, snapshot.report_view(run, q.get("tur")))
        if path == "/api/plan":
            return self._json(HTTPStatus.OK, snapshot.plan_view(run))
        m = _AGENT_LOG.match(path)
        if m:
            name = m.group(1)
            if name not in snapshot.agent_names(run):
                return self._error(HTTPStatus.NOT_FOUND, "Böyle bir ajan yok.")
            try:
                tail = max(1, min(int(q.get("tail") or 60), 400))
            except ValueError:
                tail = 60
            return self._json(HTTPStatus.OK, snapshot.agent_log(run, name, tail))
        return self._error(HTTPStatus.NOT_FOUND, "Böyle bir adres yok.")

    def _stream(self, q: dict[str, str]) -> None:
        """SSE: önce kaçırılan olaylar (seq > from), sonra canlı akış. Her olayın `id`si seq'tir."""
        raw = self.headers.get("Last-Event-ID") or q.get("from") or "0"
        try:
            since = max(0, int(raw))
        except ValueError:
            since = 0
        bus = self.office.runner.bus
        sub, backlog = bus.subscribe(since)
        try:
            self.send_response(HTTPStatus.OK)
            self._common_headers("text/event-stream; charset=utf-8", None)
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            for ev in backlog:
                self._write_event(ev)
            self.wfile.flush()
            last_write = time.monotonic()
            stopping = self.office.stopping
            while not stopping.is_set():
                try:
                    ev = sub.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() - last_write >= PING_S:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_write = time.monotonic()
                    continue
                self._write_event(ev)
                # kısa bir patlamada gelenleri tek seferde yaz
                while True:
                    try:
                        self._write_event(sub.get_nowait())
                    except queue.Empty:
                        break
                self.wfile.flush()
                last_write = time.monotonic()
            # sunucu kapanıyor: kuyrukta kalan son olayları (rapor, son aşama) gönder, sonra istemciye söyle,
            # yeniden bağlanmayı bıraksın
            while True:
                try:
                    self._write_event(sub.get_nowait())
                except queue.Empty:
                    break
            self.wfile.write(b"event: kapandi\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, OSError):
            pass
        finally:
            bus.unsubscribe(sub)

    def _write_event(self, ev: dict) -> None:
        data = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"id: {ev.get('seq', 0)}\ndata: {data}\n\n".encode("utf-8"))

    # --- POST --------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        try:
            self._route_post()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass

    def _route_post(self) -> None:
        self.connection.settimeout(10)  # gövdesini göndermeyen istemci iş parçacığını sonsuza dek tutmasın
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "İstek gövdesi çok büyük.")
        # Gövde, istek reddedilecek olsa da okunur: okunmamış veriyle kapanan bağlantıda (Windows) istemci
        # hata yanıtını göremeden "bağlantı sıfırlandı" alabilir.
        raw = self.rfile.read(length) if length else b""
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Geçersiz Host başlığı.")
        if not self._origin_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Geçersiz kaynak (Origin).")
        if not self.office.check_key(self.headers.get("X-Jev-Key")):
            return self._error(HTTPStatus.FORBIDDEN, "Erişim anahtarı geçersiz.")
        path = urlsplit(self.path).path
        if not path.startswith("/api/komut/"):
            return self._error(HTTPStatus.NOT_FOUND, "Böyle bir adres yok.")
        cmd = path.rsplit("/", 1)[-1]
        if cmd not in COMMANDS:
            return self._error(HTTPStatus.NOT_FOUND, "Bilinmeyen komut.")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body: dict = {}
        if length:
            if ctype != "application/json":
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Gövde JSON olmalı.")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._error(HTTPStatus.BAD_REQUEST, "Gövde okunamadı.")
            if not isinstance(body, dict):
                return self._error(HTTPStatus.BAD_REQUEST, "Gövde bir nesne olmalı.")
        arg = ""
        if cmd == "duzelt":
            arg = str(body.get("not") or "").strip()[:4000]
        ok, msg = self.office.runner.submit(cmd, arg, "arayuz")
        return self._json(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"ok": ok, "mesaj": msg})
