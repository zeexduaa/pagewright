"""A Markdown to HTML converter, written from scratch.

It covers the parts of CommonMark and GitHub-Flavored Markdown that sites
actually use:

* blocks: ATX and setext headings, paragraphs, block quotes, ordered and
  unordered lists (nested, tight or loose), fenced and indented code, thematic
  breaks, GFM tables with alignment, and raw HTML blocks;
* inline: emphasis and strong emphasis, strikethrough, code spans, links and
  images (with titles), autolinks, backslash escapes, hard line breaks and
  inline HTML.

Headings get unique ``id`` attributes and are collected into a table of
contents. Link and image URLs with dangerous schemes (``javascript:`` and
friends) are replaced with ``#``. Raw HTML can be turned off with
``allow_html=False``, in which case it is shown as text.

Not supported yet (see the roadmap): reference-style links, footnotes,
definition lists and task-list checkboxes.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

# ---- Results -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Heading:
    level: int
    id: str
    text: str


@dataclass
class Document:
    html: str
    headings: list[Heading] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        """Text of the first level-1 heading, if any."""
        return next((h.text for h in self.headings if h.level == 1), None)

    def toc(self, max_level: int = 3) -> list[Heading]:
        return [h for h in self.headings if h.level <= max_level]

    def toc_html(self, max_level: int = 3) -> str:
        """A nested ``<ul>`` of links to the headings (levels 2..max_level)."""
        items = [h for h in self.headings if 2 <= h.level <= max_level]
        if not items:
            return ""
        out: list[str] = []
        stack: list[int] = []
        for h in items:
            if not stack or h.level > stack[-1]:
                out.append("<ul>")
                stack.append(h.level)
            else:
                while stack and h.level < stack[-1]:
                    out.append("</li></ul>")
                    stack.pop()
                out.append("</li>")
                if not stack or h.level > stack[-1]:  # skipped a level going back up
                    out.append("<ul>")
                    stack.append(h.level)
            out.append(f'<li><a href="#{h.id}">{escape(h.text)}</a>')
        while stack:
            out.append("</li></ul>")
            stack.pop()
        return "".join(out)


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def slugify(text: str) -> str:
    """'Hello, World!' -> 'hello-world'; keeps letters from any language."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"[\s_-]+", "-", text).strip("-") or "section"


# ---- Inline --------------------------------------------------------------------------------

SAFE_URL = re.compile(r"^(?:https?:|mailto:|tel:|ftp:|#|/|\./|\.\./|[^:/?#]*(?:[/?#]|$))", re.IGNORECASE)
ESCAPABLE = r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"
INLINE_TAG = re.compile(
    r"<(?:/?[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z_:][\w:.-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?)*\s*/?"
    r"|!--.*?--)>",
    re.DOTALL,
)
ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
AUTOLINK = re.compile(r"<((?:https?|ftp)://[^\s<>]+|mailto:[^\s<>]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)>")


def safe_url(url: str) -> str:
    url = url.strip()
    compact = re.sub(r"[\x00-\x20]", "", url)
    return url if SAFE_URL.match(compact) else "#"


class InlineParser:
    """Turns one block's text into inline HTML.

    Code spans, autolinks, links, images, inline HTML and escapes are pulled out
    first and replaced with placeholders, so emphasis rules never look inside
    them. The remaining text is escaped, emphasis is applied, and the
    placeholders are put back.
    """

    PLACEHOLDER = "\x00{}\x00"

    def __init__(self, allow_html: bool = True):
        self.allow_html = allow_html

    def render(self, text: str) -> str:
        self._stash: list[str] = []
        protected = self._protect(text)
        escaped = escape(protected).replace("&#x27;", "'")
        formatted = self._emphasis(escaped)
        formatted = self._line_breaks(formatted)
        return self._restore(formatted)

    # -- placeholders --

    def _keep(self, html_fragment: str) -> str:
        self._stash.append(html_fragment)
        return self.PLACEHOLDER.format(len(self._stash) - 1)

    def _restore(self, text: str) -> str:
        pattern = re.compile("\x00(\\d+)\x00")
        # Placeholders can nest (a link's text holds a code span), so repeat until stable.
        for _ in range(10):
            new = pattern.sub(lambda m: self._stash[int(m.group(1))], text)
            if new == text:
                break
            text = new
        return text

    # -- protection pass --

    def _protect(self, text: str) -> str:
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n and text[i + 1] in ESCAPABLE:
                out.append(self._keep(escape(text[i + 1])))
                i += 2
                continue
            if ch == "\\" and i + 1 < n and text[i + 1] == "\n":
                out.append(self._keep("<br />\n"))
                i += 2
                continue
            if ch == "&":
                m = ENTITY.match(text, i)
                if m:
                    out.append(self._keep(m.group(0)))  # &copy; &#169; &amp; stay as written
                    i = m.end()
                    continue
            if ch == "`":
                end, html_fragment = self._code_span(text, i)
                if end:
                    out.append(self._keep(html_fragment))
                    i = end
                    continue
                run = len(text[i:]) - len(text[i:].lstrip("`"))
                out.append(text[i:i + run])
                i += run
                continue
            if ch == "<":
                m = AUTOLINK.match(text, i)
                if m:
                    target = m.group(1)
                    href = target if ":" in target else f"mailto:{target}"
                    out.append(self._keep(f'<a href="{escape(safe_url(href))}">{escape(target)}</a>'))
                    i = m.end()
                    continue
                if self.allow_html:
                    m = INLINE_TAG.match(text, i)
                    if m:
                        out.append(self._keep(m.group(0)))
                        i = m.end()
                        continue
            if ch == "!" and i + 1 < n and text[i + 1] == "[":
                parsed = self._link(text, i + 1, image=True)
                if parsed:
                    end, html_fragment = parsed
                    out.append(self._keep(html_fragment))
                    i = end
                    continue
            if ch == "[":
                parsed = self._link(text, i, image=False)
                if parsed:
                    end, html_fragment = parsed
                    out.append(self._keep(html_fragment))
                    i = end
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _code_span(text: str, start: int) -> tuple[int, str]:
        run = len(text[start:]) - len(text[start:].lstrip("`"))
        fence = "`" * run
        j = start + run
        while True:
            k = text.find(fence, j)
            if k == -1:
                return 0, ""
            # The closing run must be exactly as long as the opening one.
            if (k + run < len(text) and text[k + run] == "`") or (k > 0 and text[k - 1] == "`" and k - 1 >= start + run):
                j = k + run
                while j < len(text) and text[j] == "`":
                    j += 1
                continue
            content = text[start + run:k].replace("\n", " ")
            if len(content) > 1 and content[0] == " " and content[-1] == " " and content.strip():
                content = content[1:-1]
            return k + run, f"<code>{escape(content)}</code>"

    def _link(self, text: str, start: int, image: bool) -> tuple[int, str] | None:
        # Find the matching ] (brackets may nest; code spans and escapes don't count).
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                end, _ = self._code_span(text, i)
                if end:
                    i = end
                    continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        else:
            return None
        label = text[start + 1:i]
        rest = text[i + 1:]
        m = re.match(r"\(\s*(<[^<>\n]*>|[^\s()<>]*(?:\([^\s()<>]*\)[^\s()<>]*)*)"
                     r"(?:\s+(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?\s*\)", rest)
        if not m:
            return None
        url = m.group(1)
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        url = re.sub(r"\\([" + ESCAPABLE + r"])", r"\1", url)
        title = m.group(2)
        title_attr = ""
        if title:
            title_text = re.sub(r"\\([" + ESCAPABLE + r"])", r"\1", title[1:-1])
            title_attr = f' title="{escape(title_text)}"'
        end = i + 1 + m.end()
        href = escape(safe_url(url))
        if image:
            alt = re.sub(r"[*_`\[\]]", "", label)
            return end, f'<img src="{href}" alt="{escape(alt)}"{title_attr} />'
        inner = InlineParser(self.allow_html).render(label)
        return end, f'<a href="{href}"{title_attr}>{inner}</a>'

    # -- emphasis --

    STRONG = [
        re.compile(r"(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)", re.DOTALL),
        re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])", re.DOTALL),
    ]
    EM = [
        re.compile(r"(?<![*\\])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?!\*)", re.DOTALL),
        re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", re.DOTALL),
    ]
    STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)

    def _emphasis(self, text: str) -> str:
        text = re.sub(r"(?<![\w*])\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*(?!\*)", r"<em><strong>\1</strong></em>", text,
                      flags=re.DOTALL)
        for pattern in self.STRONG:
            text = pattern.sub(r"<strong>\1</strong>", text)
        for pattern in self.EM:
            text = pattern.sub(r"<em>\1</em>", text)
        return self.STRIKE.sub(r"<del>\1</del>", text)

    @staticmethod
    def _line_breaks(text: str) -> str:
        return re.sub(r" {2,}\n", "<br />\n", text)


# ---- Blocks --------------------------------------------------------------------------------

ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
THEMATIC = re.compile(r"^ {0,3}((?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`]*?)[ \t]*$")
BULLET = re.compile(r"^( {0,3})([-*+])([ \t]+|$)(.*)$")
ORDERED = re.compile(r"^( {0,3})(\d{1,9})([.)])([ \t]+|$)(.*)$")
QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
TABLE_DELIM = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
HTML_BLOCK_TAGS = (
    "address|article|aside|blockquote|details|dialog|div|dl|fieldset|figcaption|figure|footer|form|h[1-6]|"
    "header|hr|iframe|main|nav|ol|p|pre|section|summary|table|ul|video|audio|picture|script|style|svg|!--"
)
HTML_BLOCK = re.compile(rf"^ {{0,3}}<(?:/?(?:{HTML_BLOCK_TAGS})(?:[\s/>]|$))", re.IGNORECASE)


class Markdown:
    """Reusable converter. ``Markdown().convert(text)`` returns a :class:`Document`."""

    def __init__(self, allow_html: bool = True, heading_ids: bool = True):
        self.allow_html = allow_html
        self.heading_ids = heading_ids

    def convert(self, text: str) -> Document:
        self._headings: list[Heading] = []
        self._used_ids: dict[str, int] = {}
        self._inline = InlineParser(self.allow_html)
        lines = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
        body = self._blocks(lines)
        return Document(body, self._headings)

    # -- helpers --

    def _unique_id(self, text: str) -> str:
        base = slugify(text)
        count = self._used_ids.get(base, 0)
        self._used_ids[base] = count + 1
        return base if count == 0 else f"{base}-{count}"

    def _heading(self, level: int, raw: str) -> str:
        inner = self._inline.render(raw.strip())
        plain = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if not self.heading_ids:
            return f"<h{level}>{inner}</h{level}>"
        hid = self._unique_id(plain)
        self._headings.append(Heading(level, hid, plain))
        return f'<h{level} id="{hid}">{inner}</h{level}>'

    @staticmethod
    def _starts_block(line: str) -> bool:
        return bool(
            ATX.match(line) or THEMATIC.match(line) or FENCE.match(line) or QUOTE.match(line)
            or BULLET.match(line) or ORDERED.match(line)
        )

    # -- block parser --

    def _blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            m = FENCE.match(line)
            if m:
                i = self._fenced(lines, i, m, out)
                continue

            m = ATX.match(line)
            if m:
                out.append(self._heading(len(m.group(1)), m.group(2) or ""))
                i += 1
                continue

            if THEMATIC.match(line):
                out.append("<hr />")
                i += 1
                continue

            if QUOTE.match(line):
                i = self._quote(lines, i, out)
                continue

            if BULLET.match(line) or ORDERED.match(line):
                i = self._list(lines, i, out)
                continue

            if line.startswith("    "):
                i = self._indented_code(lines, i, out)
                continue

            if self.allow_html and HTML_BLOCK.match(line):
                start = i
                while i < n and lines[i].strip():
                    i += 1
                out.append("\n".join(lines[start:i]))
                continue

            if "|" in line and i + 1 < n and TABLE_DELIM.match(lines[i + 1]) and "-" in lines[i + 1]:
                table = self._table(lines, i)
                if table:
                    html_table, i = table
                    out.append(html_table)
                    continue

            i = self._paragraph(lines, i, out)
        return "\n".join(out)

    def _fenced(self, lines: list[str], i: int, m: re.Match[str], out: list[str]) -> int:
        indent, fence, info = len(m.group(1)), m.group(2), m.group(3)
        if fence[0] == "`" and "`" in info:
            return self._paragraph(lines, i, out)
        body: list[str] = []
        i += 1
        while i < len(lines):
            close = re.match(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$", lines[i])
            if close:
                i += 1
                break
            line = lines[i]
            strip = min(indent, len(line) - len(line.lstrip(" ")))
            body.append(line[strip:])
            i += 1
        language = info.split()[0] if info else ""
        cls = f' class="language-{escape(language)}"' if language else ""
        code = escape("\n".join(body)) + ("\n" if body else "")
        out.append(f"<pre><code{cls}>{code}</code></pre>")
        return i

    def _indented_code(self, lines: list[str], i: int, out: list[str]) -> int:
        body: list[str] = []
        while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
            body.append(lines[i][4:])
            i += 1
        while body and not body[-1].strip():
            body.pop()
        out.append(f"<pre><code>{escape(chr(10).join(body))}\n</code></pre>")
        return i

    def _quote(self, lines: list[str], i: int, out: list[str]) -> int:
        inner: list[str] = []
        while i < len(lines):
            m = QUOTE.match(lines[i])
            if m:
                inner.append(m.group(1))
                i += 1
            elif lines[i].strip() and inner and inner[-1].strip() and not self._starts_block(lines[i]):
                inner.append(lines[i])  # lazy continuation of a quoted paragraph
                i += 1
            else:
                break
        out.append(f"<blockquote>\n{self._blocks(inner)}\n</blockquote>")
        return i

    def _list(self, lines: list[str], i: int, out: list[str]) -> int:
        first = BULLET.match(lines[i]) or ORDERED.match(lines[i])
        assert first is not None
        ordered = first.re is ORDERED
        marker = first.group(3) if ordered else first.group(2)
        start_number = int(first.group(2)) if ordered else 1

        items: list[list[str]] = []
        loose = False
        n = len(lines)
        while i < n:
            m = (ORDERED if ordered else BULLET).match(lines[i])
            if not m or (m.group(3) if ordered else m.group(2)) != marker:
                break
            lead = len(m.group(1))
            marker_width = len(m.group(2)) + (1 if ordered else 0)
            gap = m.group(4) if ordered else m.group(3)
            text = m.group(5) if ordered else m.group(4)
            gap_width = len(gap) if 1 <= len(gap) <= 4 else 1
            content_indent = lead + marker_width + gap_width
            item = [text if len(gap) <= 4 else " " * (len(gap) - 1) + text]
            i += 1
            while i < n:
                line = lines[i]
                if not line.strip():
                    # A blank line ends the item unless indented content follows.
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and len(lines[j]) - len(lines[j].lstrip(" ")) >= content_indent:
                        item.extend([""] * (j - i))
                        i = j
                        continue
                    break
                indent = len(line) - len(line.lstrip(" "))
                if indent >= content_indent:
                    item.append(line[content_indent:])
                    i += 1
                elif not self._starts_block(line) and item and item[-1].strip():
                    item.append(line.lstrip(" "))  # lazy continuation
                    i += 1
                else:
                    break
            items.append(item)
            # Blank lines between items make the list loose.
            if i < n and not lines[i].strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                nxt = (ORDERED if ordered else BULLET).match(lines[j]) if j < n else None
                if nxt and (nxt.group(3) if ordered else nxt.group(2)) == marker:
                    loose = True
                    i = j
                    continue
                break
        if any("" in item[:-1] and any(x.strip() for x in item[item.index(""):]) for item in items):
            loose = True

        tag = "ol" if ordered else "ul"
        start_attr = f' start="{start_number}"' if ordered and start_number != 1 else ""
        rendered = []
        for item in items:
            inner = self._blocks(item)
            if not loose:
                inner = re.sub(r"<p>(.*?)</p>", r"\1", inner, flags=re.DOTALL)
                rendered.append(f"<li>{inner}</li>")
            else:
                rendered.append(f"<li>\n{inner}\n</li>")
        out.append(f"<{tag}{start_attr}>\n" + "\n".join(rendered) + f"\n</{tag}>")
        return i

    def _table(self, lines: list[str], i: int) -> tuple[str, int] | None:
        def cells(row: str) -> list[str]:
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|") and not row.endswith("\\|"):
                row = row[:-1]
            parts, current, k = [], "", 0
            while k < len(row):
                if row[k] == "\\" and k + 1 < len(row) and row[k + 1] == "|":
                    current += "|"
                    k += 2
                    continue
                if row[k] == "|":
                    parts.append(current.strip())
                    current = ""
                else:
                    current += row[k]
                k += 1
            parts.append(current.strip())
            return parts

        header = cells(lines[i])
        delims = cells(lines[i + 1])
        if len(header) != len(delims):
            return None
        aligns = []
        for d in delims:
            left, right = d.startswith(":"), d.endswith(":")
            aligns.append("center" if left and right else "right" if right else "left" if left else "")

        def row_html(values: list[str], tag: str) -> str:
            values = (values + [""] * len(aligns))[:len(aligns)]
            parts = []
            for value, align in zip(values, aligns):
                attr = f' style="text-align: {align}"' if align else ""
                parts.append(f"<{tag}{attr}>{self._inline.render(value)}</{tag}>")
            return "<tr>" + "".join(parts) + "</tr>"

        body_rows = []
        j = i + 2
        while j < len(lines) and lines[j].strip() and "|" in lines[j] and not self._starts_block(lines[j]):
            body_rows.append(row_html(cells(lines[j]), "td"))
            j += 1
        html_out = f"<table>\n<thead>\n{row_html(header, 'th')}\n</thead>"
        if body_rows:
            html_out += "\n<tbody>\n" + "\n".join(body_rows) + "\n</tbody>"
        return html_out + "\n</table>", j

    def _paragraph(self, lines: list[str], i: int, out: list[str]) -> int:
        para: list[str] = [lines[i].lstrip()]
        i += 1
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                break
            m = SETEXT.match(line)
            if m and len(para) >= 1:
                level = 1 if m.group(1)[0] == "=" else 2
                out.append(self._heading(level, " ".join(para)))
                return i + 1
            if self._starts_block(line) or (self.allow_html and HTML_BLOCK.match(line)):
                # A "1." only interrupts a paragraph when it starts at 1 (as in CommonMark).
                om = ORDERED.match(line)
                if not (om and om.group(2) != "1"):
                    break
            para.append(line.lstrip())
            i += 1
        out.append(f"<p>{self._inline.render(chr(10).join(para).rstrip())}</p>")
        return i


def render(text: str, allow_html: bool = True) -> str:
    """Shortcut: Markdown text to an HTML string."""
    return Markdown(allow_html=allow_html).convert(text).html
