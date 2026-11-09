"""Finds broken internal links in a built site.

``pagewright check`` builds the site, then reads every HTML file in the output
folder and checks each internal ``href`` and ``src``:

- the file it points to exists (``/about/`` means ``about/index.html``);
- a ``#fragment`` matches an ``id`` (or ``<a name>``) on the target page.

External links (``https://…`` to other hosts, ``mailto:`` and so on) aren't
fetched; checking them needs the network and is on the roadmap.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK_ATTRIBUTES = {("a", "href"), ("link", "href"), ("img", "src"), ("script", "src"), ("source", "src"),
                   ("video", "src"), ("audio", "src"), ("iframe", "src")}


@dataclass
class Problem:
    page: str      # output path of the page with the link, e.g. "posts/hi/index.html"
    link: str      # the link as written
    reason: str

    def __str__(self) -> str:
        return f"{self.page}: {self.link} ({self.reason})"


@dataclass
class _Page:
    links: list[str] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    refresh: str | None = None


class _Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = _Page()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v for k, v in attrs if v is not None}
        if "id" in values:
            self.page.ids.add(values["id"])
        if tag == "a" and "name" in values:
            self.page.ids.add(values["name"])
        for name, value in values.items():
            if (tag, name) in LINK_ATTRIBUTES:
                self.page.links.append(value)
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            # Redirect pages made by aliases: "0; url=https://example.com/new/"
            content = values.get("content", "")
            if "url=" in content.lower():
                self.page.refresh = content[content.lower().index("url=") + 4:].strip()


def _scan(path: Path) -> _Page:
    scanner = _Scanner()
    scanner.feed(path.read_text(encoding="utf-8", errors="replace"))
    scanner.close()
    return scanner.page


def _target_file(output: Path, page_rel: str, path: str) -> str | None:
    """The output-relative file a link path points to, or None if it leaves the site."""
    if path.startswith("/"):
        joined = path.lstrip("/")
    else:
        joined = posixpath.join(posixpath.dirname(page_rel), path)
    normal = posixpath.normpath(joined) if joined else "."
    if normal.startswith(".."):
        return None
    if normal == ".":
        normal = ""
    candidate = output / normal
    if path.endswith("/") or candidate.is_dir():
        return posixpath.join(normal, "index.html") if normal else "index.html"
    return normal


def check_links(output: str | Path, base_url: str = "") -> list[Problem]:
    """Checks every HTML file under ``output``. ``base_url`` lets absolute links to the site itself
    (as in redirect pages) be checked like internal ones."""
    output = Path(output)
    pages = {p.relative_to(output).as_posix(): p for p in sorted(output.rglob("*.html"))}
    scanned = {rel: _scan(path) for rel, path in pages.items()}
    base = urlsplit(base_url) if base_url else None
    problems: list[Problem] = []

    for rel, page in scanned.items():
        links = list(page.links) + ([page.refresh] if page.refresh else [])
        for link in links:
            link = link.strip()
            parts = urlsplit(link)
            if parts.scheme or parts.netloc:
                same_site = base is not None and parts.netloc == base.netloc and parts.scheme in ("http", "https")
                if not same_site:
                    continue  # external: not checked yet
                parts = parts._replace(path=parts.path[len(base.path.rstrip("/")):] or "/")
            if not parts.path and not parts.fragment:
                continue
            path = unquote(parts.path)
            target = rel if not path else _target_file(output, rel, path)
            if target is None:
                problems.append(Problem(rel, link, "points outside the site"))
                continue
            if not (output / target).is_file():
                problems.append(Problem(rel, link, "not found"))
                continue
            fragment = unquote(parts.fragment)
            if fragment and target.endswith(".html"):
                ids = scanned[target].ids if target in scanned else _scan(output / target).ids
                if fragment not in ids:
                    problems.append(Problem(rel, link, f"no element with id {fragment!r} on {target}"))
    return problems
