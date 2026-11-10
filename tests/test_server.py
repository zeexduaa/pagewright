import http.client
import os
import socket
import threading
import time

import pytest

from conftest import write
from pagewright.server import EVENTS_PATH, DevServer, inject, watched_files


def bump(path):
    """Moves a file's mtime forward so the watcher sees the change even on coarse clocks."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


@pytest.fixture
def server(site_dir):
    srv = DevServer(site_dir, port=0, interval=0.05, log=lambda line: None)
    srv.start()
    yield srv
    srv.stop()


def get(srv, path):
    conn = http.client.HTTPConnection(srv.host, srv.port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, resp.getheader("Content-Type"), body


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_inject_places_script_before_body_end():
    assert inject(b"<html><body>hi</body></html>").index(b"EventSource") < b"<html><body>hi</body></html>".index(b"</body>") + 200
    out = inject(b"<p>no body tag</p>")
    assert out.startswith(b"<p>no body tag</p>") and b"EventSource" in out


def test_serves_pages_with_reload_script_and_local_urls(server):
    status, ctype, body = get(server, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert EVENTS_PATH in body and body.index(EVENTS_PATH) < body.index("</body>")
    assert server.url in body  # base_url points at the dev server

    status, ctype, body = get(server, "/style.css")
    assert status == 200 and "text/css" in ctype and EVENTS_PATH not in body


def test_missing_page_uses_the_sites_404(server):
    status, _, body = get(server, "/nope/")
    assert status == 404 and "Page not found" in body and EVENTS_PATH in body


def test_drafts_are_shown_by_default(site_dir):
    write(site_dir / "content" / "posts" / "2026-02-01-wip.md", '+++\ntitle = "WIP"\ndraft = true\n+++\nSoon.\n')
    srv = DevServer(site_dir, port=0, log=lambda line: None)
    srv.start()
    try:
        assert get(srv, "/posts/wip/")[0] == 200
    finally:
        srv.stop()


def test_edit_rebuilds_and_notifies_event_stream(server, site_dir):
    received = []

    def listen():
        sock = socket.create_connection((server.host, server.port), timeout=5)
        sock.sendall(f"GET {EVENTS_PATH} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        data = b""
        while b"event: reload" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        received.append(data)
        sock.close()

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    time.sleep(0.2)  # let the stream connect

    about = site_dir / "content" / "about.md"
    about.write_text(about.read_text(encoding="utf-8") + "\nA brand new sentence.\n", encoding="utf-8")
    bump(about)

    assert wait_until(lambda: "brand new sentence" in get(server, "/about/")[2])
    thread.join(timeout=5)
    assert received and b"event: reload" in received[0]


def test_build_error_is_shown_then_cleared(server, site_dir):
    about = site_dir / "content" / "about.md"
    good = about.read_text(encoding="utf-8")
    about.write_text("+++\ntitle = \n+++\n", encoding="utf-8")
    bump(about)
    assert wait_until(lambda: get(server, "/")[0] == 500)
    status, _, body = get(server, "/about/")
    assert "Build failed" in body and "about.md" in body and EVENTS_PATH in body
    assert get(server, "/style.css")[0] == 200  # assets still load

    about.write_text(good, encoding="utf-8")
    bump(about)
    assert wait_until(lambda: get(server, "/")[0] == 200)


def test_plugin_crash_keeps_the_server_watching(server, site_dir):
    config = site_dir / "pagewright.toml"
    good = config.read_text(encoding="utf-8")
    write(site_dir / "plugins" / "boom.py", "def on_site(site):\n    raise RuntimeError('kaboom')\n")
    config.write_text('plugins = ["boom"]\n' + good, encoding="utf-8")
    bump(config)
    assert wait_until(lambda: "kaboom" in get(server, "/")[2])

    config.write_text(good, encoding="utf-8")
    bump(config)
    assert wait_until(lambda: get(server, "/")[0] == 200)


def test_watched_files_skip_hidden_and_pycache(site_dir):
    write(site_dir / "content" / ".draft.swp", "x")
    write(site_dir / "plugins" / "__pycache__" / "x.pyc", "x")
    names = {p.name for p in watched_files(site_dir, None)}
    assert "pagewright.toml" in names and "about.md" in names
    assert ".draft.swp" not in names and "x.pyc" not in names


def test_change_right_after_start_is_not_missed(site_dir):
    """Regression: the watcher used to take its first snapshot when its thread started, so a save
    between start() and that moment was treated as the starting state and never rebuilt."""
    srv = DevServer(site_dir, port=0, interval=0.05, log=lambda line: None)
    real_watch = srv.watch
    gate = threading.Event()

    def slow_watch(*args):
        gate.wait(5)  # the watcher thread starts late, as on a busy machine
        real_watch(*args)

    srv.watch = slow_watch
    srv.start()
    try:
        about = site_dir / "content" / "about.md"
        about.write_text(about.read_text(encoding="utf-8") + "\nSaved straight away.\n", encoding="utf-8")
        bump(about)
        gate.set()
        assert wait_until(lambda: "Saved straight away" in get(srv, "/about/")[2])
    finally:
        srv.stop()


def request(srv, method, path):
    conn = http.client.HTTPConnection(srv.host, srv.port, timeout=5)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_folder_without_slash_redirects(server):
    resp, _ = request(server, "GET", "/about?x=1")
    assert resp.status == 301 and resp.getheader("Location") == "/about/?x=1"
    resp, _ = request(server, "GET", "/feed.xml")
    assert resp.status == 200  # files aren't redirected


def test_head_matches_get(server):
    for path in ("/about/", "/nope/", "/style.css"):
        got, body = request(server, "GET", path)
        head, empty = request(server, "HEAD", path)
        assert empty == b"" and head.status == got.status
        assert head.getheader("Content-Length") == got.getheader("Content-Length") == str(len(body))
        assert head.getheader("Content-Type") == got.getheader("Content-Type")
