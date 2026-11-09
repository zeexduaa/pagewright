"""Development server: build, serve, watch, rebuild and reload the browser.

``pagewright serve`` builds the site with ``base_url`` pointing at the local server,
serves the output folder, and polls the site's files for changes. After each
rebuild, open pages reload themselves: every HTML response gets a small script
that listens on ``/__pagewright/events`` (server-sent events). When a build fails,
HTML pages show the error instead of a stale page until the next good build.

This server is for writing, not for production: it only listens on 127.0.0.1 by
default, and the built site is plain files any web server can host.
"""

from __future__ import annotations

import html
import socketserver
import sys
import threading
import time
import traceback
from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import CONFIG_FILE, ConfigError
from .site import BuildError, BuildReport, Site

EVENTS_PATH = "/__pagewright/events"
RELOAD_SCRIPT = f"""<script>
(function () {{
  if (!window.EventSource) return;
  var source = new EventSource("{EVENTS_PATH}");
  source.addEventListener("reload", function () {{ location.reload(); }});
}})();
</script>"""


class LiveState:
    """Shared between the watcher and request handlers."""

    def __init__(self) -> None:
        self.version = 0
        self.error: str | None = None
        self.condition = threading.Condition()

    def bump(self, error: str | None) -> None:
        with self.condition:
            self.version += 1
            self.error = error
            self.condition.notify_all()

    def wait_for_change(self, seen: int, timeout: float) -> int:
        with self.condition:
            self.condition.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version


def watched_files(root: Path, site: Site | None) -> dict[Path, int]:
    folders = ["content", "templates", "static", "plugins", "themes", "data"]
    if site is not None:
        cfg = site.config
        folders = [cfg.content_dir, cfg.templates_dir, cfg.static_dir, "plugins", "themes", "data"]
    stamps: dict[Path, int] = {}
    config = root / CONFIG_FILE
    if config.exists():
        stamps[config] = config.stat().st_mtime_ns
    for name in folders:
        folder = root / name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            try:
                if path.is_file() and not path.name.startswith(".") and "__pycache__" not in path.parts:
                    stamps[path] = path.stat().st_mtime_ns
            except OSError:
                continue  # deleted while we were looking
    return stamps


def error_page(message: str) -> bytes:
    return (
        "<!doctype html><meta charset='utf-8'><title>Build failed</title>"
        "<body style='font:16px/1.5 system-ui,sans-serif;max-width:50rem;margin:3rem auto;padding:0 1rem'>"
        "<h1 style='color:#b42318'>Build failed</h1>"
        f"<pre style='white-space:pre-wrap;background:#fef3f2;padding:1rem;border-radius:8px'>{html.escape(message)}</pre>"
        "<p>Fix the problem and save; this page reloads by itself.</p>"
        f"{RELOAD_SCRIPT}</body>"
    ).encode()


class Handler(SimpleHTTPRequestHandler):
    state: LiveState
    output: Path
    log: Callable[[str], None]

    def log_message(self, format: str, *args: Any) -> None:
        pass  # quiet; rebuilds are what's interesting

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == EVENTS_PATH:
            self._events()
            return
        if self.state.error is not None and (path.endswith("/") or path.endswith(".html")):
            self._send(500, error_page(self.state.error))
            return
        target = Path(self.translate_path(path))
        if target.is_dir():
            target = target / "index.html"
        if target.suffix == ".html" and target.is_file():
            body = target.read_bytes()
            self._send(200, inject(body))
            return
        if not target.exists():
            missing = self.output / "404.html"
            body = inject(missing.read_bytes()) if missing.is_file() else b"<h1>Not found</h1>" + RELOAD_SCRIPT.encode()
            self._send(404, body)
            return
        super().do_GET()

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        seen = self.state.version
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while not getattr(self.server, "stopping", False):
                version = self.state.wait_for_change(seen, timeout=15)
                if version != seen:
                    seen = version
                    self.wfile.write(f"event: reload\ndata: {version}\n\n".encode())
                else:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def inject(body: bytes) -> bytes:
    """Adds the reload script just before </body> (or at the end if there isn't one)."""
    marker = body.lower().rfind(b"</body>")
    script = RELOAD_SCRIPT.encode()
    return body[:marker] + script + body[marker:] if marker != -1 else body + script


class DevHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    stopping = False

    def server_bind(self) -> None:
        # Skip HTTPServer's reverse DNS lookup (socket.getfqdn), which is slow on some systems.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


class DevServer:
    def __init__(self, root: str | Path, host: str = "127.0.0.1", port: int = 8000, drafts: bool = True,
                 interval: float = 0.4, log: Callable[[str], None] | None = None):
        self.root = Path(root).resolve()
        self.drafts = drafts
        self.interval = interval
        self.log = log or (lambda line: print(f"{time.strftime('%H:%M:%S')} {line}", file=sys.stderr, flush=True))
        self.state = LiveState()
        self._stop = threading.Event()
        self.site: Site | None = None

        handler = type("BoundHandler", (Handler,), {})
        handler.state = self.state
        handler.log = staticmethod(self.log)  # type: ignore[assignment]
        self.httpd = DevHTTPServer((host, port), partial(handler, directory=str(self.root / "public")))
        self.host, self.port = self.httpd.server_address[:2]
        self.url = f"http://{self.host}:{self.port}"
        self._handler = handler

    def build(self) -> BuildReport | None:
        try:
            self.site = Site(self.root, drafts=self.drafts, base_url=self.url)
            output = self.site.config.output_path
            self._handler.output = output
            self._handler.directory = str(output)  # type: ignore[attr-defined]
            self.httpd.RequestHandlerClass = partial(self._handler, directory=str(output))  # type: ignore[assignment]
            report = self.site.build()
        except (BuildError, ConfigError) as exc:
            self.log(f"build failed: {exc}")
            self.state.bump(str(exc))
            return None
        except Exception:  # a bug in a plugin or in pagewright: show it, keep watching
            detail = traceback.format_exc()
            self.log(f"build crashed:\n{detail}")
            self.state.bump(f"Unexpected error (this may be a bug in a plugin or in pagewright):\n\n{detail}")
            return None
        self.log(f"built: {report.summary()}")
        self.state.bump(None)
        return report

    def watch(self, stamps: dict[Path, int] | None = None) -> None:
        """Rebuilds whenever a watched file changes. ``stamps`` is the snapshot to compare against;
        take it *before* building, so a file saved during the build or before this thread gets
        going is still noticed."""
        if stamps is None:
            stamps = watched_files(self.root, self.site)
        while not self._stop.wait(self.interval):
            current = watched_files(self.root, self.site)
            if current != stamps:
                changed = sorted({p for p in set(current) | set(stamps) if current.get(p) != stamps.get(p)})
                names = ", ".join(str(p.relative_to(self.root)) for p in changed[:3])
                more = f" and {len(changed) - 3} more" if len(changed) > 3 else ""
                self.log(f"changed: {names}{more}")
                time.sleep(0.05)  # let editors finish writing
                stamps = watched_files(self.root, self.site)  # before building, for the same reason
                self.build()

    def start(self) -> None:
        """Builds once, then serves and watches in background threads."""
        stamps = watched_files(self.root, None)
        self.build()
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        threading.Thread(target=self.watch, args=(stamps,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.httpd.stopping = True
        self.state.bump(self.state.error)  # wake event streams so they can finish
        self.httpd.shutdown()
        self.httpd.server_close()
