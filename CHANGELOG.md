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
