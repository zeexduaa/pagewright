# Templates

pagewright's template language looks like Jinja. It's a separate, smaller implementation: no Python `eval`, a fixed set of filters and tests, and attribute access that refuses anything private.

## Where templates come from

When pagewright looks for `post.html`, it checks, in order:

1. `templates/post.html` in your site;
2. `themes/<theme>/templates/post.html` in your site;
3. the built-in theme of that name.

So you can override one template of a theme without copying the rest.

### Which template renders what

| Output | Templates tried (first that exists) |
| --- | --- |
| A page | its `template` front matter → `<section>/single.html` → `post.html` (dated pages in a section) → `page.html` |
| Home page | `index.html` → `list.html` |
| Section list | `<section>/list.html` → `list.html` |
| Tag page | `tag.html` → `list.html` |
| All tags | `tags.html` |
| Not found | `404.html` |
| `robots.txt` | `robots.txt` if you have one, otherwise a built-in one |

## Syntax

```jinja
{{ expression }}                 output (escaped)
{% tag %}                        logic
{# comment #}                    ignored, may contain }} or %}
{{- x -}}  {%- if -%}            the dash trims whitespace on that side
```

### Expressions

- Literals: `"text"`, `'text'`, `42`, `3.5`, `true`, `false`, `none`, `[1, 2]`, `{"a": 1}`.
- Variables and attributes: `page.title`, `page["title"]`, `site.tags[0].name`. A missing name or attribute is *undefined*. It prints as nothing, is falsy, and is an error with `strict=True`.
- Maths: `+ - * / // % **`. Text: `~` concatenates as strings.
- Comparison: `== != < <= > >=`, `in`, `not in`.
- Logic: `and`, `or`, `not`, and `a if cond else b`.
- Filters: `value | filter`, `value | filter(arg, …)`.
- Tests: `value is defined`, `is not none`, `is odd`, and so on.
- Calls: `range(5)`, `super()`, `loop.cycle("odd", "even")`, `paginator.url_for(3)`, plugin globals, and public methods such as `title.upper()`. On a dict, `d.keys` looks up the key `"keys"`; use filters like `list` instead.

### Tags

```jinja
{% if x %}…{% elif y %}…{% else %}…{% endif %}

{% for post in posts %}
  {{ loop.index }}/{{ loop.length }}   {# also loop.index0, revindex, revindex0, first, last, previtem, nextitem #}
  {{ loop.cycle("odd", "even") }}
  {% if loop.changed(post.date.year) %}<h2>{{ post.date.year }}</h2>{% endif %}
{% else %}
  No posts yet.
{% endfor %}

{% for key, value in page.extra | dictsort %}…{% endfor %}   {# unpacking pairs #}

{% set title = page.title | upper %}

{% extends "base.html" %}
{% block content %}{{ super() }} and more{% endblock %}

{% include "partials/footer.html" %}

{% raw %}{{ shown as-is }}{% endraw %}     {# for writing about templates #}
```

`{% extends %}` must come first. Anything outside blocks in a child template is ignored. Blocks can nest. `super()` renders the parent's version of the block.

## Variables

Every template gets:

| Name | What |
| --- | --- |
| `site.title`, `site.description`, `site.author`, `site.language`, `site.base_url` | from `pagewright.toml` |
| `site.extra` | the `[extra]` table |
| `site.pages` | every page, published or (with drafts on) not |
| `site.posts` | pages with a date in a section, newest first |
| `site.sections` | `{name, title, url, pages}` for each content folder |
| `site.tags` | `{name, url, count, pages}`, A–Z |
| `site.menu` | top-level pages except `/`, by `weight` then title; hide one with `extra.menu = false` |
| `site.data` | files from `data/` (`data/authors.toml` → `site.data.authors`, `data/links/social.json` → `site.data.links.social`), plus whatever plugins add |
| `page` | the page being rendered (or a section's `index.md`), see below |
| `content` | the page's HTML (safe) |
| `toc` | its table of contents as a nested `<ul>` (safe; empty if fewer than two headings) |
| `now` | the build time (a `datetime`) |

List pages (home, sections, tags) also get `posts` (this page's items) and `paginator`, with `number`, `total_pages`, `has_prev`, `has_next`, `prev_url`, `next_url` and `url_for(n)`. Section pages get `section`; tag pages get `tag`. Both get `feed` (`url`, `title`) when they have posts, for a `<link rel="alternate">`.

### `page`

`title`, `url`, `date`, `updated`, `tags`, `tag_links` (`{name, url}`), `section`, `summary` (safe HTML), `description`, `draft`, `weight`, `show_toc`, `headings` (`{level, text, id}`), `extra`, `source` (path in `content/`) and `is_post`.

For posts there's also `prev` (the older post in the same section), `next` (the newer one) and `related` (up to 5 posts from any section sharing the most tags, newest first among ties). Each is a page, or empty.

## Filters

| Filter | Example | Notes |
| --- | --- | --- |
| `abs`, `int(default=0)`, `float(default=0.0)`, `round(n)` | `"3.7" | int` → 3 | text that isn't a number gives the default |
| `batch(n, fill=none)` | `items | batch(3)` | lists of n |
| `capitalize`, `lower`, `upper`, `title` | | |
| `count`, `length` | `posts | length` | |
| `date(fmt="%Y-%m-%d")` | `page.date | date("%-d %b %Y")` | `%-d` works on Windows too |
| `dictsort(by="key", reverse=false, case_sensitive=false)` | `page.extra | dictsort` | sorted `(key, value)` pairs |
| `default(value, boolean=false)` | `page.description | default(site.description)` | |
| `escape` / `e` | | escapes text; safe text is left as it is |
| `first`, `last` | | |
| `groupby(attr)` | `posts | groupby("section")` | `{grouper, list}` items |
| `indent(n=4)` | | |
| `items` | `site.extra | items` | `(key, value)` pairs in their own order |
| `join(sep="", attr=none)` | `tags | join(", ", "name")` | |
| `list`, `string` | | |
| `map(attr)` | `posts | map("title")` | |
| `max`, `min`, `sum` | | |
| `reading_time(wpm=200)` | `content | reading_time` | minutes, at least 1 |
| `rejectattr`, `selectattr` | `pages | selectattr("draft")`, `posts | selectattr("section", "posts")`, `pages | selectattr("weight", ">", 5)` | one argument: truthy; two: equal; three: operator `== != < <= > >= in` `not in` |
| `replace(old, new)` | | |
| `reverse`, `sort(attribute=none, reverse=false, case_sensitive=false)`, `unique` | `pages | sort("title")`, `posts | sort("date", reverse=true)` | text ignores case by default; missing values come first; Jinja's `sort(true, attribute="date")` also works |
| `safe` | | marks text as HTML; use only on trusted text |
| `slice(start, stop)` | `posts | slice(0, 5)` | |
| `slugify` | | `"Hello World!"` → `hello-world` |
| `striptags` | | removes HTML, collapses whitespace |
| `tojson` | `<script>var d = {{ data | tojson }}</script>` | safe inside `<script>` |
| `trim` | | |
| `truncate(n=255, end="…")` | | cuts at a word boundary |
| `urlencode` | | |
| `wordcount` | | ignores HTML |

Plugins can add filters. See [plugins.md](plugins.md).

## Tests

`defined`, `undefined`, `none`, `string`, `number`, `iterable`, `odd`, `even`. Any of them can be negated with `is not`.

## Safety

- Output is escaped unless the value is safe (`content`, `toc`, `summary`, `| safe`, the result of `tojson`).
- Attributes starting with `_`, and `format`, `format_map`, `mro` and similar, can't be accessed. Other public methods can be called, as in Jinja's default (non-sandboxed) mode, so a template could, for example, call a method on a page object. An allow-list for calls is on the roadmap.
- `range()` is limited to 100,000 items.
- Template names can't use `..`, absolute paths or drive letters.

Templates are still code you trust: treat a theme like a plugin and read it before using it.
