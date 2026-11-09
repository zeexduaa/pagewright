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
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from . import __version__
from .config import Config, load_config
from .content import ContentError, Page, load_pages, output_path_for
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
    timings: dict[str, float] = field(default_factory=dict)  # seconds per rendered output and build step

    def slowest(self, count: int = 10) -> list[tuple[str, float]]:
        return sorted(self.timings.items(), key=lambda kv: -kv[1])[:count]

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


DATA_DIR = "data"


def _stable(value: Any) -> str:
    """A repeatable text form of site.data for fingerprints, even if a plugin put odd things in it."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def load_data(folder: Path) -> dict[str, Any]:
    """data/authors.toml -> site.data.authors; data/menus/main.json -> site.data.menus.main."""
    data: dict[str, Any] = {}
    if not folder.is_dir():
        return data
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or any(part.startswith((".", "_")) for part in rel.parts):
            continue
        if path.suffix not in (".toml", ".json"):
            continue
        where = f"{DATA_DIR}/{rel.as_posix()}"
        try:
            text = path.read_text(encoding="utf-8")
            value = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BuildError(f"{where}: can't be read: {exc}") from None
        target = data
        for part in rel.parent.parts:
            existing = target.setdefault(part, {})
            if not isinstance(existing, dict):
                raise BuildError(f"{where}: the folder {part!r} has the same name as a data file")
            target = existing
        if rel.stem in target:
            raise BuildError(f"{where}: site.data already has {rel.stem!r} (from another file or folder of that name)")
        target[rel.stem] = value
    return data


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
        self.markdown = Markdown(allow_html=self.config.allow_html,
                                 smart_punctuation=self.config.smart_punctuation)
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
        self.data.update(load_data(self.root / DATA_DIR))  # keeps anything a plugin set in on_config
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
        self._link_posts()
        self._hook("on_site", self)
        return report

    RELATED_LIMIT = 5

    def _link_posts(self) -> None:
        """Sets page.prev / page.next (within a section, by date) and page.related (by shared tags)."""
        for page in self.pages:
            page.prev = page.next = None
            page.related = []
        by_section: dict[str, list[Page]] = {}
        for post in self.posts:  # newest first
            by_section.setdefault(post.section, []).append(post)
        for items in by_section.values():
            for newer, older in zip(items, items[1:]):
                newer.prev, older.next = older, newer
        posts = self.posts
        for post in posts:
            if not post.tags:
                continue
            mine = {t.casefold() for t in post.tags}
            scored = [(len(mine & {t.casefold() for t in other.tags}), other) for other in posts if other is not post]
            # Most tags in common first; ties keep newest-first order (sorted is stable).
            post.related = [p for n, p in sorted((s for s in scored if s[0]), key=lambda s: -s[0])][:self.RELATED_LIMIT]

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
            _stable(self.data),  # data files, plus whatever plugins added in on_site
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
                title = self._section_title(name)
                context = {"page": index, "section": {"name": name, "title": title, "url": base},
                           "paginator": paginator, "posts": paginator.items}
                if any(p.is_post for p in items):
                    context["feed"] = {"url": f"{base}feed.xml", "title": f"{self.config.title}: {title}"}
                out.append((path, template, context, index.raw_text if index else ""))

        tags = self.tags
        if tags:
            out.append(("tags/index.html", self._choose("tags.html", "list.html"), {}, ""))
            for tag, items in tags.items():
                base = f"/tags/{slugify(tag)}/"
                for paginator in paginate(items, per_page, base):
                    path = paginator.url_for(paginator.number).strip("/") + "/index.html"
                    context = {"tag": {"name": tag, "url": base, "count": len(items)},
                               "paginator": paginator, "posts": paginator.items}
                    if any(p.is_post for p in items):
                        context["feed"] = {"url": f"{base}feed.xml", "title": f"{self.config.title}: #{tag}"}
                    out.append((path, self._choose("tag.html", "list.html"), context, ""))
        try:
            out.append(("404.html", self._choose("404.html"), {}, ""))
        except BuildError:
            pass
        return out

    def build(self, clean: bool = False, force: bool = False) -> BuildReport:
        started = time.perf_counter()
        report = self.load()
        report.timings["(reading and converting content)"] = time.perf_counter() - started
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
            began = time.perf_counter()
            try:
                html = self.env.render(template, context)
            except TemplateError as exc:
                raise BuildError(f"{exc} (while rendering {path})") from None
            report.timings[path] = time.perf_counter() - began
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            report.rendered.append(path)

        began = time.perf_counter()
        extra_files = self._extra_files(site)
        report.timings["(feeds, sitemap, search index, redirects)"] = time.perf_counter() - began
        for path, text in extra_files.items():
            target = output / path
            written[path] = _digest(text)
            if previous.get(path) == written[path] and target.exists():
                report.skipped.append(path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            report.rendered.append(path)

        began = time.perf_counter()
        static = self._copy_static(output, previous, written)
        report.copied = static
        report.timings["(copying static files)"] = time.perf_counter() - began

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
        files = {**self._feeds(), "sitemap.xml": self._sitemap(), "search-index.json": self._search_index()}
        robots_template = None
        try:
            robots_template = self.env.find("robots.txt")
        except TemplateNotFound:
            pass
        files["robots.txt"] = (self.env.render("robots.txt", {"site": site}) if robots_template else
                               f"User-agent: *\nAllow: /\n\nSitemap: {self.config.absolute_url('/sitemap.xml')}\n")
        files.update(self._redirects(set(files)))
        return files

    def _redirects(self, taken_extra: set[str]) -> dict[str, str]:
        """A small HTML page at each alias that sends visitors (and search engines) to the page's URL."""
        taken = {output[0]: f"the page {output[0]}" for output in self.outputs()}
        taken.update({path: path for path in taken_extra})
        files: dict[str, str] = {}
        for page in self.pages:
            for alias in page.aliases:
                path = output_path_for(alias)
                if alias == page.url:
                    continue
                if path in taken:
                    raise BuildError(f"content/{page.source}: alias {alias} clashes with {taken[path]}")
                taken[path] = f"an alias of content/{page.source}"
                target = xml_escape(self.config.absolute_url(page.url), {'"': "&quot;"})
                files[path] = (
                    f'<!doctype html>\n<html lang="{xml_escape(self.config.language)}">\n<head>\n'
                    f'<meta charset="utf-8">\n<title>Moved: {xml_escape(page.title)}</title>\n'
                    f'<link rel="canonical" href="{target}">\n'
                    f'<meta name="robots" content="noindex">\n'
                    f'<meta http-equiv="refresh" content="0; url={target}">\n</head>\n'
                    f'<body><p>This page has moved to <a href="{target}">{target}</a>.</p></body>\n</html>\n'
                )
        return files

    def _section_title(self, name: str) -> str:
        index = next((p for p in self.pages if p.is_section_index and p.section == name), None)
        return index.title if index else name.replace("-", " ").title()

    def _feeds(self) -> dict[str, str]:
        """The site feed, plus one per section and per tag that has posts."""
        feeds = {"feed.xml": self._atom(self.posts, self.config.title, "/", "/feed.xml")}
        for name, pages in self.sections.items():
            posts = [p for p in self.posts if p.section == name]
            if posts:
                base = f"/{slugify(name)}/"
                feeds[f"{slugify(name)}/feed.xml"] = self._atom(posts, f"{self.config.title}: {self._section_title(name)}",
                                                               base, f"{base}feed.xml")
        for tag, pages in self.tags.items():
            posts = [p for p in pages if p.is_post]
            if posts:
                base = f"/tags/{slugify(tag)}/"
                feeds[f"tags/{slugify(tag)}/feed.xml"] = self._atom(posts, f"{self.config.title}: #{tag}", base,
                                                                   f"{base}feed.xml")
        return feeds

    def _atom(self, posts: list[Page], title: str, home: str, self_path: str) -> str:
        posts = posts[: self.config.feed_limit]
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
            f"  <title>{xml_escape(title)}</title>\n"
            f"  <link href=\"{xml_escape(cfg.absolute_url(home))}\"/>\n"
            f"  <link rel=\"self\" href=\"{xml_escape(cfg.absolute_url(self_path))}\"/>\n"
            f"  <id>{xml_escape(cfg.absolute_url(self_path))}</id>\n  <updated>{stamp(updated)}</updated>\n{author}"
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
