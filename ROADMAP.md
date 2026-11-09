# Roadmap

pagewright works end to end, but it's far from finished. This is the plan, roughly in order. Sizes are rough: **S** is an afternoon, **M** a few days, **L** a week or more, **XL** a project of its own.

Each item is (or will be) a GitHub issue. Comment there before starting something large.

## v0.2: Markdown and content

- [ ] **Reference-style links**: `[text][ref]` and `[ref]: https://…` definitions, collected in a pass before inline parsing. (M)
- [ ] **Footnotes**: `[^1]` references and `[^1]: …` definitions, rendered as a numbered list with back-links at the end of the page. (M)
- [ ] **Task lists**: `- [ ]` and `- [x]` as disabled checkboxes, GFM-style. (S)
- [ ] **Definition lists**: `Term` followed by `: definition` lines, rendered as `<dl>`. (S)
- [ ] **Smart punctuation**: optional curly quotes, en/em dashes and ellipses, never inside code. (S)
- [ ] **YAML front matter**: `---` blocks with a small, safe YAML subset (scalars, lists, one level of maps), since PyYAML is a dependency we don't want. (L)
- [ ] **Datetimes with time zones**: keep the time from `date = 2026-11-09T14:30:00+01:00`, add a `timezone` setting, and use full timestamps in the feed and sorting. (M)
- [ ] **Data files**: load `data/*.toml` and `data/*.json` into `site.data` (the dev server already watches `data/`). (S)
- [ ] **Page bundles**: `content/posts/trip/index.md` plus images in the same folder, copied next to the page. (M)

## v0.3: Templates and themes

- [ ] **Shortcodes**: `{{< youtube id="…" >}}` in Markdown rendered with `templates/shortcodes/youtube.html`, without breaking code blocks. (L)
- [ ] **Syntax highlighting**: a small tokenizer for Python, JavaScript, HTML/CSS, shell, TOML and JSON, emitting classes the default theme styles. (L)
- [ ] **Macros**: `{% macro card(post) %}…{% endmacro %}` and `{% import "macros.html" as m %}`. (M)
- [ ] **`{% raw %}` blocks** for showing template syntax on a page. (S)
- [ ] **Template allow-list for calls**: only allow calling known-safe methods (str methods, `loop.cycle`, `paginator.url_for`, plugin globals) instead of any public method. (M)
- [ ] **Better template errors**: show the offending line with a caret, and the include/extends chain that led to it. (M)
- [ ] **`loop.previtem` / `loop.nextitem`, `dictsort` and `items` filters**: common Jinja features theme authors expect. (S)
- [ ] **Theme config**: a `theme.toml` with defaults that sites can override under `[extra]`, and `pagewright theme list`. (M)

## v0.4: Site structure

- [ ] **Custom taxonomies**: `[taxonomies] categories = "categories"` alongside tags, each with list and term pages and feeds. (L)
- [ ] **Related posts**: by shared tags, available as `page.related`. (S)
- [ ] **Series**: `series = "Learning Rust"` with previous/next links and a series index. (M)
- [ ] **Previous/next post links**: `page.prev` and `page.next` within a section. (S)
- [ ] **Per-section and per-tag feeds**: `/posts/feed.xml`, `/tags/python/feed.xml`. (S)
- [ ] **Aliases and redirects**: `aliases = ["/old/url/"]` generates redirect pages. (S)
- [ ] **Multilingual sites**: `index.fr.md` files, a `[languages]` table, translated menus and `hreflang` links. (XL)

## v0.5: Assets

- [ ] **Image processing**: responsive sizes and WebP without Pillow is not realistic, so make this an optional extra (`pip install pagewright[images]`) with `srcset` generation. (L)
- [ ] **CSS and JS minification**: a conservative, dependency-free minifier, off by default. (M)
- [ ] **Fingerprinted assets**: `{{ asset("style.css") }}` → `/style.3f9a1c.css` for long cache lifetimes. (M)
- [ ] **Sass-like nesting**: maybe not; open for discussion. (L)

## v0.6: Speed and the dev server

- [ ] **Parallel rendering**: render outputs in a process pool for big sites, keeping output deterministic. (L)
- [ ] **OS file events**: use inotify / FSEvents / ReadDirectoryChangesW when available, falling back to polling. (L)
- [ ] **`now` in fingerprints**: today, templates using `now` aren't re-rendered as time passes. Track which templates read `now` and rebuild them daily (or always). (M)
- [ ] **Partial reloads**: only reload the page being viewed, or just swap CSS, when that's all that changed. (M)
- [ ] **Build profile**: `pagewright build --profile` shows the slowest templates and pages. (S)
- [ ] **Link checker**: `pagewright check` finds broken internal links and missing images. (M)

## v1.0: Release

- [ ] **Publish on PyPI** with trusted publishing from GitHub Actions. (S)
- [ ] **Deploy helpers**: `pagewright deploy` for GitHub Pages, Netlify and rsync targets. (M)
- [ ] **Documentation site** built with pagewright itself. (M)
- [ ] **Stable plugin API**: version the hooks, document guarantees, add a plugin template. (M)
- [ ] **More themes**: a docs theme and a portfolio theme. (L)
