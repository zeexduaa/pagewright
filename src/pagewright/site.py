"""Building a site: content in, HTML out.

The build runs in these steps:

1. load ``pagewright.toml`` and any plugins;
2. read every Markdown file in ``content/`` and convert it (drafts only with ``drafts=True``);
3. group pages into sections and tags;
4. render each output (pages, section lists with pagination, tag pages, the home page);
5. copy static files, write the Atom feed, sitemap and search index;
6. delete files left over from earlier builds.

**Incremental builds.** Every output gets a fingerprint made from everything that can
change it: its source text, the templates it uses (including inherited and included
ones), the config, and a site-wide digest of all pages' titles, URLs, dates, tags and
summaries. Outputs whose fingerprint matches the last build (stored in
``.pagewright-cache.json``) are not rendered again. Editing the body of one post
re-renders just that post (and the lists, if its summary changed).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from . import __version__
from .config import Config, load_config
from .content import ContentError, Page, load_pages
from .markdown import Markdown, slugify
from .templates import Environment, Markup, TemplateError, TemplateNotFound

CACHE_FILE = ".pagewright-cache.json"
CACHE_VERSION = 1
BUILTIN_THEMES = Path(__file__).parent / "scaffold" / "themes"
HOOKS = ("on_config", "on_page_markdown", "on_page_html", "on_site", "on_site_built")


class BuildError(Exception):
    pass


@dataclass
class BuildReport:
    rendered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    copied: int = 0
    pages: int = 0
    drafts_skipped: int = 0
    seconds: float = 0.0

    def summary(self) -> str:
        parts = [f"{self.pages} page(s)", f"{len(self.rendered)} written", f"{len(self.skipped)} unchanged"]
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        if self.copied:
            parts.append(f"{self.copied} static file(s)")
        if self.drafts_skipped:
            parts.append(f"{self.drafts_skipped} draft(s) left out")
        return ", ".join(parts) + f" in {self.seconds * 1000:.0f} ms"


@dataclass
class Paginator:
    items: list[Any]
    number: int
    total_pages: int
    base_url: str

    def url_for(self, number: int) -> str:
        return self.base_url if number == 1 else f"{self.base_url}page/{number}/"

    @property
    def has_prev(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.total_pages

    @property
    def prev_url(self) -> str:
        return self.url_for(self.number - 1) if self.has_prev else ""

    @property
    def next_url(self) -> str:
        return self.url_for(self.number + 1) if self.has_next else ""


def paginate(items: list[Any], per_page: int, base_url: str) -> list[Paginator]:
    chunks = [items[i:i + per_page] for i in range(0, len(items), per_page)] or [[]]
    return [Paginator(chunk, n, len(chunks), base_url) for n, chunk in enumerate(chunks, 1)]


def _sort_date(page: Page) -> tuple[str, str]:
    return ((page.date.isoformat() if page.date else ""), page.title.lower())


def _digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode())
        h.update(b"\x1f")
    return h.hexdigest()[:24]


class Site:
    def __init__(self, root: str | Path, drafts: bool = False, base_url: str | None = None):
        self.config: Config = load_config(root)
        if base_url:
            self.config.base_url = base_url
        self.root = self.config.root
        self.drafts = drafts
        self.plugins: list[ModuleType] = []
        self.pages: list[Page] = []
        self.data: dict[str, Any] = {}  # free space for plugins to share with templates
        self.markdown = Markdown(allow_html=self.config.allow_html)
        self.env = Environment(self._template_dirs())
        self.env.globals.update(absolute_url=self.config.absolute_url, now=datetime.now(timezone.utc),
                                pagewright_version=__version__)
        self._load_plugins()

    # ---- Setup ---------------------------------------------------------------------------

    def theme_path(self) -> Path:
        local = self.root / "themes" / self.config.theme
        if local.is_dir():
            return local
        builtin = BUILTIN_THEMES / self.config.theme
        if builtin.is_dir():
            return builtin
        raise BuildError(f"theme {self.config.theme!r} not found in {self.root / 'themes'} or the built-in themes")

    def _template_dirs(self) -> list[Path]:
        return [self.root / self.config.templates_dir, self.theme_path() / "templates"]

    def _load_plugins(self) -> None:
        for name in self.config.plugins:
            path = self.root / "plugins" / f"{name}.py"
            if not path.is_file():
                raise BuildError(f"plugin {name!r} not found (expected {path})")
            # Compiled from source every time, not through importlib's .pyc cache: that cache checks the
            # source's mtime in whole seconds and its size, so a quick same-size edit (common while
            # `pagewright serve` is running) would silently run the old code.
            module = ModuleType(f"pagewright_plugin_{name}")
            module.__file__ = str(path)
            try:
                code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
                exec(code, module.__dict__)
            except Exception as exc:
                raise BuildError(f"plugin {name!r} failed to load: {exc}") from exc
            for attr, target in (("filters", self.env.filters), ("template_globals", self.env.globals)):
                value = getattr(module, attr, None) or {}
                if callable(value):  # `def filters(): return {...}` works as well as `filters = {...}`
                    try:
                        value = value() or {}
                    except Exception as exc:
                        raise BuildError(f"plugin {name!r} failed in {attr}: {exc}") from exc
                if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
                    raise BuildError(f"plugin {name!r}: {attr} must be a dict of names to values")
                if attr == "filters" and not all(callable(f) for f in value.values()):
                    raise BuildError(f"plugin {name!r}: every filter must be a function")
                target.update(value)
            self.plugins.append(module)
        self._hook("on_config", self.config)

    def _hook(self, name: str, *args: Any) -> list[Any]:
        results = []
        for plugin in self.plugins:
            func: Callable[..., Any] | None = getattr(plugin, name, None)
            if func is None:
                continue
            try:
                results.append(func(*args))
            except Exception as exc:
                raise BuildError(f"plugin {plugin.__name__.removeprefix('pagewright_plugin_')!r} failed in "
                                 f"{name}: {exc}") from exc
        return results

    # ---- Content -------------------------------------------------------------------------

    def load(self) -> BuildReport:
        report = BuildReport()
        try:
            pages = load_pages(self.config.content_path)
        except ContentError as exc:
            raise BuildError(f"content/{exc}") from None
        if not self.drafts:
            report.drafts_skipped = sum(p.draft for p in pages)
            pages = [p for p in pages if not p.draft]
        seen: dict[str, str] = {}
        for page in pages:
            if page.url in seen:
                raise BuildError(f"content/{page.source} and content/{seen[page.url]} both want the URL {page.url}")
            seen[page.url] = page.source
            for result in self._hook("on_page_markdown", page, page.body):
                if isinstance(result, str):
                    page.body = result
            page.render_markdown(self.markdown)
            for result in self._hook("on_page_html", page, str(page.content)):
                if isinstance(result, str):
                    page.content = Markup(result)
        self.pages = pages
        report.pages = len(pages)
        self._hook("on_site", self)
        return report

    @property
    def posts(self) -> list[Page]:
        return sorted((p for p in self.pages if p.is_post), key=_sort_date, reverse=True)

    @property
    def sections(self) -> dict[str, list[Page]]:
        sections: dict[str, list[Page]] = {}
        for page in self.pages:
            if page.section and not page.is_section_index:
                sections.setdefault(page.section, []).append(page)
        return {name: sorted(items, key=lambda p: (p.weight, *_neg_date(p))) for name, items in sorted(sections.items())}

    @property
    def tags(self) -> dict[str, list[Page]]:
        """Pages by tag. Spellings that differ only in case ("Python", "python") are one tag, shown
        with the first spelling found; different tags that would share a URL are an error."""
        tags: dict[str, list[Page]] = {}
        by_slug: dict[str, tuple[str, Page]] = {}
        for page in self.posts + [p for p in self.pages if not p.is_post]:
            for tag in page.tags:
                slug = slugify(tag)
                if slug not in by_slug:
                    by_slug[slug] = (tag, page)
                name, first_page = by_slug[slug]
                if tag.casefold() != name.casefold():
                    raise BuildError(f"tags {name!r} (content/{first_page.source}) and {tag!r} "
                                     f"(content/{page.source}) would both be /tags/{slug}/; rename one of them")
                if page not in tags.setdefault(name, []):
                    tags[name].append(page)
        return dict(sorted(tags.items(), key=lambda kv: kv[0].lower()))

    def site_context(self) -> dict[str, Any]:
        return {
            "title": self.config.title,
            "description": self.config.description,
            "author": self.config.author,
            "language": self.config.language,
            "base_url": self.config.base_url,
            "extra": self.config.extra,
            "pages": self.pages,
            "posts": self.posts,
            "sections": self.sections,
            "tags": [{"name": t, "url": f"/tags/{slugify(t)}/", "count": len(ps), "pages": ps}
                     for t, ps in self.tags.items()],
            "menu": sorted((p for p in self.pages if not p.section and p.url != "/" and p.extra.get("menu", True)),
                           key=lambda p: (p.weight, p.title.lower())),
            "data": self.data,
        }

    # ---- Fingerprints --------------------------------------------------------------------

    def _template_fingerprint(self, name: str, cache: dict[str, str]) -> str:
        if name in cache:
            return cache[name]
        template = self.env.get_template(name)
        names = sorted({name} | template.dependencies())
        parts = []
        for n in names:
            try:
                parts.append((n, self.env.path_of(n).read_text(encoding="utf-8")))
            except TemplateNotFound:
                parts.append((n, None))
        cache[name] = _digest(parts)
        return cache[name]

    def _site_digest(self) -> str:
        return _digest(
            self.config.raw, self.config.base_url, self.drafts,
            [(p.url, p.title, p.date, p.tags, str(p.summary), p.weight, p.section, p.extra, p.description)
             for p in self.pages],
            [getattr(plugin, "__file__", "") and Path(plugin.__file__ or "").read_text(encoding="utf-8")
             for plugin in self.plugins],
        )

    # ---- Rendering -----------------------------------------------------------------------

    def _choose(self, *names: str) -> str:
        for name in names:
            try:
                self.env.find(name)
                return name
            except TemplateNotFound:
                continue
        raise BuildError(f"none of these templates exist: {', '.join(names)}")

    def outputs(self) -> list[tuple[str, str, dict[str, Any], str]]:
        """Every (output path, template, extra context, own fingerprint input) the site produces."""
        out: list[tuple[str, str, dict[str, Any], str]] = []
        per_page = self.config.posts_per_page

        for page in self.pages:
            if page.is_section_index:
                continue
            if page.url == "/":
                continue  # the home page is rendered below with the post list
            template = page.template or (
                self._choose(f"{page.section}/single.html", "post.html", "page.html") if page.is_post
                else self._choose("page.html"))
            out.append((page.output_path, template, {"page": page}, page.raw_text))

        home = next((p for p in self.pages if p.url == "/"), None)
        for paginator in paginate(self.posts, per_page, "/"):
            path = paginator.url_for(paginator.number).strip("/")
            path = f"{path}/index.html" if path else "index.html"
            template = (home.template if home and home.template else None) or self._choose("index.html", "list.html")
            out.append((path, template, {"page": home, "paginator": paginator, "posts": paginator.items},
                        home.raw_text if home else ""))

        for name, items in self.sections.items():
            index = next((p for p in self.pages if p.is_section_index and p.section == name), None)
            base = f"/{slugify(name)}/"
            for paginator in paginate(items, per_page, base):
                path = paginator.url_for(paginator.number).strip("/") + "/index.html"
                template = (index.template if index and index.template else None) or self._choose(
                    f"{name}/list.html", "list.html")
                title = index.title if index else name.replace("-", " ").title()
                out.append((path, template, {"page": index, "section": {"name": name, "title": title, "url": base},
                                             "paginator": paginator, "posts": paginator.items},
                            index.raw_text if index else ""))

        tags = self.tags
        if tags:
            out.append(("tags/index.html", self._choose("tags.html", "list.html"), {}, ""))
            for tag, items in tags.items():
                base = f"/tags/{slugify(tag)}/"
                for paginator in paginate(items, per_page, base):
                    path = paginator.url_for(paginator.number).strip("/") + "/index.html"
                    out.append((path, self._choose("tag.html", "list.html"),
                                {"tag": {"name": tag, "url": base, "count": len(items)},
                                 "paginator": paginator, "posts": paginator.items}, ""))
        try:
            out.append(("404.html", self._choose("404.html"), {}, ""))
        except BuildError:
            pass
        return out

    def build(self, clean: bool = False, force: bool = False) -> BuildReport:
        started = time.perf_counter()
        report = self.load()
        output = self.config.output_path
        cache_path = self.root / CACHE_FILE
        if clean and output.exists():
            shutil.rmtree(output)
        previous = self._read_cache(cache_path) if not (clean or force) else {}
        output.mkdir(parents=True, exist_ok=True)

        site = self.site_context()
        site_digest = self._site_digest()
        template_prints: dict[str, str] = {}
        written: dict[str, str] = {}

        for path, template, extra, own in self.outputs():
            try:
                fingerprint = _digest(site_digest, self._template_fingerprint(template, template_prints), own, path)
            except TemplateError as exc:
                raise BuildError(str(exc)) from None
            written[path] = fingerprint
            target = output / path
            if previous.get(path) == fingerprint and target.exists():
                report.skipped.append(path)
                continue
            page = extra.get("page")
            context = {"site": site, "page": page, "content": page.content if page else Markup(""),
                       "toc": page.toc if page else Markup(""), **extra}
            try:
                html = self.env.render(template, context)
            except TemplateError as exc:
                raise BuildError(f"{exc} (while rendering {path})") from None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            report.rendered.append(path)

        for path, text in self._extra_files(site).items():
            target = output / path
            written[path] = _digest(text)
            if previous.get(path) == written[path] and target.exists():
                report.skipped.append(path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            report.rendered.append(path)

        static = self._copy_static(output, previous, written)
        report.copied = static

        for path in sorted(set(previous) - set(written)):
            stale = output / path
            if stale.is_file():
                stale.unlink()
                report.removed.append(path)
                _prune_empty_dirs(stale.parent, output)

        self._write_cache(cache_path, written)
        report.seconds = time.perf_counter() - started
        self._hook("on_site_built", self, report)
        return report

    # ---- Feeds, sitemap, search, static --------------------------------------------------

    def _extra_files(self, site: dict[str, Any]) -> dict[str, str]:
        files = {"feed.xml": self._atom(), "sitemap.xml": self._sitemap(), "search-index.json": self._search_index()}
        robots_template = None
        try:
            robots_template = self.env.find("robots.txt")
        except TemplateNotFound:
            pass
        files["robots.txt"] = (self.env.render("robots.txt", {"site": site}) if robots_template else
                               f"User-agent: *\nAllow: /\n\nSitemap: {self.config.absolute_url('/sitemap.xml')}\n")
        return files

    def _atom(self) -> str:
        posts = self.posts[: self.config.feed_limit]
        cfg = self.config

        def stamp(d: date | None) -> str:
            return f"{(d or date(1970, 1, 1)).isoformat()}T00:00:00Z"

        updated = max((p.updated or p.date for p in posts if p.date), default=None)
        entries = []
        for p in posts:
            link = cfg.absolute_url(p.url)
            categories = "".join(f'<category term="{xml_escape(t)}"/>' for t in p.tags)
            entries.append(
                f"  <entry>\n    <title>{xml_escape(p.title)}</title>\n    <link href=\"{xml_escape(link)}\"/>\n"
                f"    <id>{xml_escape(link)}</id>\n    <published>{stamp(p.date)}</published>\n"
                f"    <updated>{stamp(p.updated or p.date)}</updated>\n    {categories}\n"
                f"    <summary type=\"html\">{xml_escape(str(p.summary))}</summary>\n"
                f"    <content type=\"html\">{xml_escape(str(p.content))}</content>\n  </entry>"
            )
        author = f"  <author><name>{xml_escape(cfg.author)}</name></author>\n" if cfg.author else ""
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n'
            f"  <title>{xml_escape(cfg.title)}</title>\n"
            f"  <link href=\"{xml_escape(cfg.absolute_url('/'))}\"/>\n"
            f"  <link rel=\"self\" href=\"{xml_escape(cfg.absolute_url('/feed.xml'))}\"/>\n"
            f"  <id>{xml_escape(cfg.absolute_url('/'))}</id>\n  <updated>{stamp(updated)}</updated>\n{author}"
            f"  <generator version=\"{__version__}\">pagewright</generator>\n"
            + "\n".join(entries) + ("\n" if entries else "") + "</feed>\n"
        )

    def _sitemap(self) -> str:
        urls = []
        for page in sorted(self.pages, key=lambda p: p.url):
            lastmod = page.updated or page.date
            mod = f"<lastmod>{lastmod.isoformat()}</lastmod>" if lastmod else ""
            urls.append(f"  <url><loc>{xml_escape(self.config.absolute_url(page.url))}</loc>{mod}</url>")
        for name in self.sections:
            loc = self.config.absolute_url(f"/{slugify(name)}/")
            if not any(p.url == f"/{slugify(name)}/" for p in self.pages):
                urls.append(f"  <url><loc>{xml_escape(loc)}</loc></url>")
        return ('<?xml version="1.0" encoding="utf-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>\n")

    def _search_index(self) -> str:
        from .templates import FILTERS

        striptags = FILTERS["striptags"]
        entries = [{
            "title": p.title,
            "url": p.url,
            "date": p.date.isoformat() if p.date else None,
            "tags": p.tags,
            "summary": striptags(p.summary)[:300],
            "text": striptags(p.content)[:5000],
        } for p in sorted(self.pages, key=lambda p: p.url) if p.extra.get("search", True)]
        return json.dumps({"version": 1, "pages": entries}, ensure_ascii=False, separators=(",", ":"))

    def _copy_static(self, output: Path, previous: dict[str, str], written: dict[str, str]) -> int:
        """Copies theme static files, then the site's (which win). Returns how many were copied."""
        sources: dict[str, Path] = {}
        for folder in (self.theme_path() / "static", self.root / self.config.static_dir):
            if folder.is_dir():
                for path in sorted(folder.rglob("*")):
                    if path.is_file() and not any(part.startswith(".") for part in path.relative_to(folder).parts):
                        sources[path.relative_to(folder).as_posix()] = path
        copied = 0
        for rel, path in sources.items():
            if rel in written:
                raise BuildError(f"static file {rel} would overwrite a generated page")
            stat = path.stat()
            fingerprint = _digest("static", str(path), stat.st_size, stat.st_mtime_ns)
            written[rel] = fingerprint
            target = output / rel
            if previous.get(rel) == fingerprint and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
        return copied

    # ---- Cache ---------------------------------------------------------------------------

    @staticmethod
    def _read_cache(path: Path) -> dict[str, str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION or data.get("pagewright") != __version__:
            return {}
        outputs = data.get("outputs")
        return outputs if isinstance(outputs, dict) else {}

    @staticmethod
    def _write_cache(path: Path, outputs: dict[str, str]) -> None:
        payload = {"version": CACHE_VERSION, "pagewright": __version__, "outputs": dict(sorted(outputs.items()))}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(path)


def _neg_date(page: Page) -> tuple[int, str]:
    # Newest first; undated pages after dated ones, then by title.
    if page.date is None:
        return (1, page.title.lower())
    return (0, "".join(chr(0x10FFFF - ord(c)) for c in page.date.isoformat()))


def _prune_empty_dirs(folder: Path, stop: Path) -> None:
    while folder != stop and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent
