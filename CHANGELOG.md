# Changelog

## Unreleased (0.1.0)

First version. Everything is new:

- Markdown parser: headings with ids, lists, quotes, code, GFM tables, inline HTML, safe URLs, table of contents.
- Template engine: Jinja-like syntax, inheritance with `super()`, includes, ~40 filters, tests, autoescape, whitespace control.
- Content: TOML front matter, dated file names, sections, tags, drafts, summaries with `<!--more-->`.
- Build: pagination, Atom feed, sitemap, robots.txt, search index, static files, stale-file cleanup.
- Incremental builds with per-output fingerprints.
- Plugins with seven hooks, compiled from source on every build.
- Dev server with live reload and in-browser build errors.
- CLI: `new`, `build`, `serve`, `post`.
- Default theme with light and dark modes and client-side search.

### Added since

- Task lists in Markdown: `- [ ]` and `- [x]`.
- `{% raw %}…{% endraw %}` in templates.
- `loop.previtem`, `loop.nextitem`, `loop.revindex0` and `loop.changed()`; `dictsort` and `items` filters.
- Data files: `data/*.toml` and `data/*.json` in `site.data`.
- `page.prev`, `page.next` and `page.related`, shown at the end of posts in the default theme.
- `aliases` in front matter: redirect pages from old URLs.
- A feed for each section and tag (`/posts/feed.xml`, `/tags/python/feed.xml`), linked from their pages.
- `pagewright build --profile [N]` shows the slowest outputs and build steps.
- Reference-style links and footnotes in Markdown.

### Fixed before release

- Values plugins put in `site.data` (such as the starter's word count) weren't part of the cache fingerprint, so pages showing them could go stale.

- Headings like "Same", "Same", "Same-1" got two `same-1` ids; ids are now always unique.
- Unknown entities such as `&bogus;` were passed through; they are now escaped.
- Spaces in `<…>` link and image URLs are encoded as `%20`.
- `groupby` returned plain pairs, so `g.grouper` and `g.list` were empty; it now gives both and still unpacks.
- `batch(0)` returned empty lists; it is now an error, and the `fill` argument works.
- An error inside a filter or function call (`none | length`, a buggy plugin filter) crashed the CLI with a traceback; it is now a template error with the file and line.
- Errors inside a `{% block %}` named the parent template; they now name the template the block is in.
- Tags whose names make the same URL ("C" and "C++" are both `/tags/c/`) silently overwrote each other; that is now an error, and tags differing only in case are merged.
