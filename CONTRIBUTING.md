# Contributing

Thanks for looking. pagewright is small on purpose: **standard library only**, so please don't add runtime dependencies. Optional extras (like image processing) may be allowed later, behind an import check.

## Setup

```console
$ git clone https://github.com/zeexduaa/pagewright
$ cd pagewright
$ python -m pip install -e . pytest
$ python -m pytest
```

The tests should pass in a couple of seconds on Linux, macOS and Windows.

## Where things are

- `src/pagewright/markdown.py`: parse blocks first, then inline text with placeholders for code, links and HTML.
- `src/pagewright/templates.py`: tokenizer → parser (Pratt for expressions) → node tree → `render`.
- `src/pagewright/content.py`: one file to one `Page`.
- `src/pagewright/site.py`: the build, fingerprints, feeds and plugins.
- `src/pagewright/server.py` and `cli.py`.
- `src/pagewright/scaffold/`: the default theme and the starter site that `pagewright new` copies.

## Making a change

1. Find or open an issue first for anything bigger than a small fix. The [roadmap](ROADMAP.md) lists what's planned.
2. Add tests next to the existing ones. Every bug fix gets a test that fails without it.
3. Keep error messages specific: say which file, which field, and what would be valid.
4. Run `python -m pytest` and build the starter site: `pagewright new /tmp/s && pagewright build -s /tmp/s`.
5. Add a line to `CHANGELOG.md` under *Unreleased*.

## Style

- Type hints on public functions; `from __future__ import annotations` at the top.
- Line length about 120.
- Prefer clear, boring code over clever code. This project is partly meant to be read.
