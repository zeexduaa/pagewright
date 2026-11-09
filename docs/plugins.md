# Plugins

A plugin is a Python file in your site's `plugins/` folder. Turn it on in `pagewright.toml`:

```toml
plugins = ["word_count"]     # loads plugins/word_count.py
```

Plugins run in the order listed. A plugin only needs the hooks it uses. Every hook is optional.

## Hooks

| Hook | Called | Return |
| --- | --- | --- |
| `filters` | once, when the plugin loads | a dict `{"name": function}` of template filters, or a function returning one |
| `template_globals` | once | a dict `{"name": value}` of names every template can use, or a function returning one |
| `on_config(config)` | after loading `pagewright.toml` | nothing; may change `config` |
| `on_page_markdown(page, text)` | before each page is converted | new Markdown text, or `None` to keep it |
| `on_page_html(page, html)` | after conversion | new HTML, or `None` |
| `on_site(site)` | after all pages are loaded, before rendering | nothing; may change `site.pages`, `site.data` |
| `on_site_built(site, report)` | after everything is written | nothing |

`report` has `rendered`, `skipped` and `removed` (lists of output paths), `copied` (static files), `pages`, `seconds` and `summary()`.

Anything you put in `site.data` is available in templates as `site.data`.

## Example

The starter site's `plugins/word_count.py` (shortened):

```python
def words(text):
    return len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", str(text))))

filters = {"words": words}

def on_site(site):
    site.data["total_words"] = sum(words(page.content) for page in site.pages)

def on_site_built(site, report):
    print(f"word_count: {site.data['total_words']} words across {len(site.pages)} pages")
```

## Errors

An exception in a hook stops the build with `plugin 'name' failed in on_site: …`. With `pagewright serve`, the error is shown in the browser, and the server keeps watching until the plugin is fixed.

## Plugins and incremental builds

The source of every enabled plugin is part of every output's fingerprint, so editing a plugin re-renders the whole site on the next build (and `serve` rebuilds when you save). Plugins are compiled from source on every build rather than through Python's `.pyc` cache, so a quick edit is never run as the old code.

What the cache can't see is anything a plugin reads from outside the site, such as the time, the network or another folder. Use `pagewright build --force` when that changes.

## Trust

Plugins are ordinary Python running with your permissions. Only enable plugins you've read.
