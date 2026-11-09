"""Content files: front matter, dates, URLs and summaries.

A content file is Markdown with optional TOML front matter between ``+++`` lines:

    +++
    title = "Hello, world"
    date = 2026-11-09
    tags = ["intro", "meta"]
    +++

    The first paragraph becomes the summary, unless you mark the end of it with
    <!--more--> or set ``summary`` in the front matter.

URLs come from the file's place in ``content/``:

    content/index.md                 ->  /
    content/about.md                 ->  /about/
    content/posts/hello.md           ->  /posts/hello/
    content/posts/2026-11-09-hi.md   ->  /posts/hi/       (date taken from the name)
    content/posts/index.md           ->  /posts/          (the section's own page)
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .markdown import Heading, Markdown, slugify
from .templates import Markup

FRONT_MATTER = re.compile(r"\A\+\+\+[ \t]*\n(.*?)\n\+\+\+[ \t]*(?:\n|\Z)", re.DOTALL)
DATED_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(.+)$")
MORE = "<!--more-->"
FRONT_MATTER_FIELDS = {"title", "date", "updated", "draft", "tags", "slug", "url", "template",
                       "summary", "description", "weight", "toc", "extra"}


class ContentError(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path, self.message = path, message


@dataclass
class Page:
    source: str                      # path relative to content/, with forward slashes
    title: str
    url: str
    body: str                        # Markdown, without front matter
    section: str = ""                # first folder ("posts"), or "" for top-level pages
    date: date | None = None
    updated: date | None = None
    draft: bool = False
    tags: list[str] = field(default_factory=list)
    template: str | None = None
    description: str = ""
    weight: int = 0
    show_toc: bool = False
    is_section_index: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    # Filled in by render_markdown():
    content: Markup = Markup("")
    summary: Markup = Markup("")
    headings: list[Heading] = field(default_factory=list)
    toc: Markup = Markup("")
    # Filled in by Site.load() for posts:
    prev: Page | None = field(default=None, repr=False, compare=False)     # the older post in this section
    next: Page | None = field(default=None, repr=False, compare=False)     # the newer one
    related: list[Page] = field(default_factory=list, repr=False, compare=False)

    @property
    def output_path(self) -> str:
        """Where the HTML goes inside the output folder, e.g. "posts/hello/index.html"."""
        path = self.url.strip("/")
        if path.endswith(".html"):
            return path
        return f"{path}/index.html" if path else "index.html"

    @property
    def is_post(self) -> bool:
        return self.date is not None and bool(self.section) and not self.is_section_index

    @property
    def tag_links(self) -> list[dict[str, str]]:
        return [{"name": t, "url": f"/tags/{slugify(t)}/"} for t in self.tags]

    def render_markdown(self, markdown: Markdown) -> None:
        document = markdown.convert(self.body)
        self.content = Markup(document.html)
        self.headings = document.headings
        self.toc = Markup(document.toc_html())
        if not self.summary:
            if MORE in self.body:
                self.summary = Markup(markdown.convert(self.body.split(MORE, 1)[0]).html)
            else:
                first = re.search(r"<p>.*?</p>", document.html, re.DOTALL)
                self.summary = Markup(first.group(0) if first else "")


def _as_date(value: Any, field_name: str, path: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()  # times are ignored for now (see the roadmap)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ContentError(path, f"'{field_name}' must be a date like 2026-11-09")


def parse_page(text: str, source: str) -> Page:
    """Builds a Page from a file's text. ``source`` is its path relative to content/."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    meta: dict[str, Any] = {}
    body = text
    m = FRONT_MATTER.match(text)
    if m:
        try:
            meta = tomllib.loads(m.group(1))
        except tomllib.TOMLDecodeError as exc:
            raise ContentError(source, f"front matter isn't valid TOML: {exc}") from None
        body = text[m.end():]
    elif text.startswith("---\n"):
        raise ContentError(source, "front matter must be TOML between +++ lines (YAML --- isn't supported yet)")

    unknown = set(meta) - FRONT_MATTER_FIELDS
    if unknown:
        raise ContentError(source, f"unknown front matter field(s) {', '.join(sorted(unknown))}; "
                                   "put custom values under [extra]")

    rel = PurePosixPath(source)
    parts = rel.with_suffix("").parts
    stem = parts[-1]
    folders = list(parts[:-1])
    section = folders[0] if folders else ""
    is_index = stem in ("index", "_index")

    file_date = None
    dated = DATED_NAME.match(stem)
    if dated and not is_index:
        try:
            file_date = date(int(dated.group(1)), int(dated.group(2)), int(dated.group(3)))
            stem = dated.group(4)
        except ValueError:
            pass

    page_date = _as_date(meta.get("date"), "date", source) or file_date
    updated = _as_date(meta.get("updated"), "updated", source)

    tags = meta.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise ContentError(source, "'tags' must be a list of words, e.g. tags = [\"python\", \"notes\"]")
    tags = list(dict.fromkeys(t.strip() for t in tags))

    for name, kind in (("draft", bool), ("toc", bool), ("weight", int)):
        if name in meta and (not isinstance(meta[name], kind) or (kind is int and isinstance(meta[name], bool))):
            raise ContentError(source, f"'{name}' must be {'true or false' if kind is bool else 'a whole number'}")
    for name in ("title", "slug", "url", "template", "summary", "description"):
        if name in meta and not isinstance(meta[name], str):
            raise ContentError(source, f"'{name}' must be text")
    if "extra" in meta and not isinstance(meta["extra"], dict):
        raise ContentError(source, "[extra] must be a table")

    if "url" in meta:
        url = meta["url"].strip()
        if not url.startswith("/") or ".." in url.split("/"):
            raise ContentError(source, "'url' must start with / and stay inside the site")
        if not url.endswith(("/", ".html")):
            url += "/"
    else:
        slug = meta.get("slug") or (stem if not is_index else "")
        if slug and slugify(slug) != slug:
            if "slug" in meta:
                raise ContentError(source, f"'slug' can only use lowercase letters, digits and -, e.g. {slugify(slug)!r}")
            slug = slugify(slug)  # file names like "My Post.md" become my-post
        segments = [slugify(f) for f in folders] + ([slug] if slug else [])
        url = "/" + "/".join(segments) + ("/" if segments else "")

    title = meta.get("title")
    if not title:
        heading = re.search(r"^#[ \t]+(.+?)[ \t#]*$", body, re.MULTILINE)
        title = heading.group(1) if heading else stem.replace("-", " ").replace("_", " ").strip().capitalize()

    page = Page(
        source=source, title=title, url=url, body=body, section=section,
        date=page_date, updated=updated, draft=meta.get("draft", False), tags=tags,
        template=meta.get("template"), description=meta.get("description", ""),
        weight=meta.get("weight", 0), show_toc=meta.get("toc", False),
        is_section_index=is_index and bool(folders), extra=meta.get("extra", {}), raw_text=text,
    )
    if meta.get("summary"):
        page.summary = Markup(Markdown().convert(meta["summary"]).html)
    return page


def load_pages(content_dir: Path) -> list[Page]:
    """Every .md file under content/, in a stable order. Files starting with . or _ (except _index) are skipped."""
    pages = []
    if not content_dir.is_dir():
        return pages
    for path in sorted(content_dir.rglob("*.md")):
        rel = path.relative_to(content_dir).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        if any(part.startswith("_") and part != "_index.md" for part in rel.split("/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ContentError(rel, "isn't UTF-8 text") from None
        pages.append(parse_page(text, rel))
    return pages
