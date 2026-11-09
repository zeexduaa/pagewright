# Content

## Files and URLs

Every `.md` file under `content/` is a page. Its URL comes from its path:

| File | URL |
| --- | --- |
| `content/index.md` | `/` |
| `content/about.md` | `/about/` |
| `content/posts/index.md` (or `_index.md`) | `/posts/`, the section's own page, used as `page` on the list |
| `content/posts/hello.md` | `/posts/hello/` |
| `content/posts/2026-01-15-hello.md` | `/posts/hello/`, dated 2026-01-15 |
| `content/notes/My Idea.md` | `/notes/my-idea/` |

The first folder is the page's **section**. A page with a date inside a section is a **post**: it goes in feeds, on the home page and in its section's list.

Files and folders starting with `.` or `_` are skipped (except `_index.md`), so `_drafts/` is a fine place for notes. Two pages that want the same URL is an error that names both files.

## Front matter

TOML between `+++` lines at the top. Every field is optional:

| Field | Type | |
| --- | --- | --- |
| `title` | text | Defaults to the first `# heading`, then to the file name. |
| `date`, `updated` | date | `2026-01-15`. A datetime is accepted, but the time is dropped for now. |
| `draft` | true/false | Left out of `build` unless you pass `--drafts`. |
| `tags` | list of text | Duplicates removed, order kept. |
| `slug` | text | Lowercase letters, digits and `-`. Replaces the last part of the URL. |
| `url` | text | The full URL, starting with `/`. Ends in `/` or `.html`. |
| `template` | text | A template name, e.g. `"landing.html"`. |
| `summary` | Markdown | Replaces the automatic summary. |
| `description` | text | For meta tags and the feed. |
| `weight` | integer | Menu order; lower comes first. |
| `toc` | true/false | Asks the theme to show the table of contents. |
| `[extra]` | table | Anything else, e.g. `extra.menu = false` or `extra.cover = "/img/a.jpg"`. |

Other top-level fields are rejected, to catch typos like `tittle`.

YAML front matter (`---`) gives a clear error for now (see the roadmap).

## Markdown

Supported:

- `#` headings and underlined (setext) headings, with automatic `id`s for linking (`## Set-up` → `id="set-up"`; repeats get `-1`, `-2`);
- paragraphs, hard line breaks (two trailing spaces or `\`);
- `*emphasis*`, `**strong**`, `***both***`, `~~strikethrough~~`, `` `code` ``;
- links `[text](url "title")`, images `![alt](src)`, autolinks `<https://…>`;
- block quotes (nested, with lazy continuation lines);
- bullet and numbered lists (nested, tight or loose, any start number);
- fenced code (```` ``` ```` or `~~~`, with a language class) and indented code;
- GFM tables with alignment;
- task lists: `- [ ] to do` and `- [x] done` (bullet lists only, as on GitHub);
- thematic breaks (`---`, `***`, `___`);
- raw HTML blocks and inline HTML (unless `markdown.allow_html = false`);
- backslash escapes and HTML entities.

`javascript:`, `vbscript:` and `data:` URLs in links and images are replaced with `#`. Put images in `static/` instead.

Not yet supported (see the roadmap): reference-style links, footnotes, definition lists, syntax highlighting and smart quotes.

## Summaries

A post's summary, shown on list pages and in the feed, is:

1. `summary` from the front matter, if set;
2. otherwise everything before `<!--more-->`;
3. otherwise the first paragraph.

## Static files

Everything in the theme's `static/` and then your `static/` is copied into the output as-is, so your files win. A static file that would overwrite a generated page (e.g. `static/about/index.html` next to `content/about.md`) is an error.
