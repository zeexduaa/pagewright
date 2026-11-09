+++
title = "A quick Markdown guide"
tags = ["meta", "writing"]
toc = true
+++

Everything you can use in posts, in one place.<!--more-->

## Text

Use *emphasis*, **strong**, ~~strikethrough~~ and `inline code`.
Links look like [this](https://example.com "A title") or, to keep long URLs
out of the way, [like this][docs]. Images look like `![alt text](/image.png)`.
Footnotes work too.[^why]

## Lists

- Bullet lists
  - can nest
1. Numbered lists
2. too

- [x] Task lists
- [ ] with boxes to tick

## Code

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

## Tables

| Feature        | Supported |
|:---------------|:---------:|
| Tables         | yes       |
| Footnotes      | yes       |
| Math           | not yet   |

## Quotes

> Simplicity is prerequisite for reliability.

---

Set `toc = true` in the front matter for a table of contents like the one above.

[docs]: https://github.com/zeexduaa/pagewright/blob/main/docs/content.md "Content docs"
[^why]: Notes collect at the end of the page, with links back to where they're used.
