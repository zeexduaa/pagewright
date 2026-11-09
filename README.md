# pagewright

A static site generator in pure Python, with **no dependencies**. Markdown parser, template engine, incremental builds, dev server with live reload, feeds, sitemap, client-side search and plugins are all written from scratch on the standard library.

> **Status: early and unfinished.** The core works end to end and is tested on Linux, macOS and Windows, but a lot is still missing (footnotes, syntax highlighting, YAML front matter, image processing, taxonomies beyond tags…). See [ROADMAP.md](ROADMAP.md). Expect breaking changes before 1.0.

```console
$ pagewright new my-site
$ cd my-site
$ pagewright serve
Serving /home/me/my-site at http://127.0.0.1:8000  (Ctrl+C to stop)
12:01:44 built: 4 page(s), 16 written, 0 unchanged, 2 static file(s) in 14 ms
```

## Why another one?

Mostly to show how the pieces fit together, in code small enough to read in an afternoon:

| Part | File | What it does |
| --- | --- | --- |
| Markdown | [`markdown.py`](src/pagewright/markdown.py) | CommonMark-ish block parser + inline parser; GFM tables; heading ids and a table of contents; safe URLs |
| Templates | [`templates.py`](src/pagewright/templates.py) | Jinja-like syntax, Pratt expression parser, inheritance with `super()`, includes, ~40 filters, autoescape, sandboxed attribute access |
| Content | [`content.py`](src/pagewright/content.py) | TOML front matter, URLs from file paths, dated file names, summaries |
| Build | [`site.py`](src/pagewright/site.py) | Sections, tags, pagination, Atom feed, sitemap, search index, static files, **incremental builds**, plugins |
| Dev server | [`server.py`](src/pagewright/server.py) | Polling watcher, rebuild on save, live reload over server-sent events, errors shown in the browser |
| Link checker | [`check.py`](src/pagewright/check.py) | Broken internal links, images and anchors in the built site |
| CLI | [`cli.py`](src/pagewright/cli.py) | `new`, `build`, `serve`, `post`, `check` |

## Install

Requires Python 3.11 or newer. Not on PyPI yet, so install from a clone:

```console
$ git clone https://github.com/zeexduaa/pagewright
$ cd pagewright
$ python -m pip install -e .
```

## Commands

| Command | What it does |
| --- | --- |
| `pagewright new PATH [--force]` | Creates a site with sample posts, a plugin and an editable copy of the default theme in `themes/default`. |
| `pagewright build [-s SITE] [--drafts] [--clean] [--force] [--base-url URL] [--profile [N]]` | Builds into `public/`. Only outputs whose inputs changed are rendered again; `--force` renders everything, `--clean` empties the folder first, `--profile` lists the slowest outputs and steps. |
| `pagewright serve [-s SITE] [--port 8000] [--host 127.0.0.1] [--no-drafts]` | Builds, serves and rebuilds on every save. Open pages reload themselves. Shows drafts unless `--no-drafts`. |
| `pagewright post "Title" [-s SITE] [--section posts]` | Creates `content/posts/YYYY-MM-DD-title.md` as a draft. |
| `pagewright check [-s SITE] [--drafts]` | Builds, then lists broken internal links, missing images and `#anchors` that don't exist. Exits with 1 if it finds any, so it can run in CI. External links aren't checked yet. |

## A site

```
my-site/
├── pagewright.toml        settings
├── content/               Markdown; folders become sections
│   ├── index.md           → /
│   ├── about.md           → /about/
│   └── posts/
│       └── 2026-01-15-hello-world.md   → /posts/hello-world/
├── templates/             your templates; override the theme's by name
├── static/                copied as-is (overrides theme static files)
├── themes/default/        templates/ + static/
├── data/                  TOML/JSON for templates: site.data.<name>
└── plugins/               Python files, enabled in pagewright.toml
```

Every build writes:

- one page per Markdown file (drafts only with `--drafts` / `serve`);
- the home page, paginated, listing posts (pages with a date inside a section);
- a list page per section (`/posts/`, `/posts/page/2/`, …);
- `/tags/` and a page per tag;
- `feed.xml` (Atom), plus `feed.xml` in each section and tag that has posts;
- `sitemap.xml`, `robots.txt`, `search-index.json` and `404.html`.

### Settings

```toml
title = "My Site"
description = "Notes, posts and projects."
author = "Your Name"
base_url = "https://example.com"
language = "en"
theme = "default"
posts_per_page = 10        # 1–1000
feed_limit = 20            # entries in feed.xml
plugins = ["word_count"]   # files in plugins/
# content_dir, static_dir, templates_dir, output_dir can be renamed too

[markdown]
allow_html = true          # false escapes raw HTML in Markdown
smart_punctuation = false  # true: “curly quotes”, it’s, – and —, …

[extra]
github = "zeexduaa"        # anything; templates see site.extra.github
```

Unknown settings are an error, so typos don't go unnoticed.

### Front matter

```toml
+++
title = "Hello, world"
date = 2026-01-15          # or take it from the file name
updated = 2026-02-01
tags = ["intro", "meta"]
draft = true
description = "For <meta name=description> and the feed."
summary = "Overrides the automatic summary."
slug = "hi"                # /posts/hi/
url = "/custom/path/"      # overrides everything
aliases = ["/2019/old/"]   # old URLs that redirect here
template = "landing.html"
weight = 10                # order in the menu (lower first)
toc = true                 # show the table of contents

[extra]
menu = false               # keep a top-level page out of the menu
+++
```

The summary is everything before `<!--more-->`, or else the first paragraph. More in [docs/content.md](docs/content.md).

### Templates

```jinja
{% extends "base.html" %}
{% block content %}
  <h1>{{ page.title }}</h1>
  {% if page.date %}<time>{{ page.date | date("%-d %B %Y") }}</time>{% endif %}
  {{ content }}
  {% for post in site.posts | selectattr("section", "==", "posts") | slice(0, 3) %}
    <a href="{{ post.url }}">{{ post.title }}</a>{% if not loop.last %}, {% endif %}
  {% endfor %}
{% endblock %}
```

Output is HTML-escaped unless it's marked safe (`content`, `toc`, `| safe`). Templates can't reach private attributes or call `str.format`. Full reference: [docs/templates.md](docs/templates.md).

### Plugins

```python
# plugins/reading_stats.py
filters = {"shout": lambda s: str(s).upper() + "!"}   # or a function returning a dict

def on_page_html(page, html):
    return html.replace("TODO", "<mark>TODO</mark>")

def on_site_built(site, report):
    print(f"built {len(site.pages)} pages")
```

Hooks: `filters`, `template_globals`, `on_config`, `on_page_markdown`, `on_page_html`, `on_site`, `on_site_built`. See [docs/plugins.md](docs/plugins.md).

## Incremental builds

Each output (a page, a list page, the feed…) gets a SHA-256 fingerprint of everything that can change it: the source text, every template it uses (including the ones it extends and includes), the config, and a digest of the site's page list (titles, URLs, dates, tags, summaries). Fingerprints from the last build live in `.pagewright-cache.json`. If a fingerprint hasn't changed and the file is still there, it isn't rendered again.

So editing the body of one post renders that post, the feeds it appears in and the search index, and nothing else. Changing its title also renders the lists that show it.

Known gap: templates that use `now` aren't re-rendered just because time has passed. Use `--force` for that (see the roadmap).

## Development

```console
$ python -m pip install -e . pytest
$ python -m pytest
```

There are about 165 tests, and they run in a couple of seconds. CI runs them on Linux, macOS and Windows with Python 3.11 and 3.13, then builds a fresh site with the CLI. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
