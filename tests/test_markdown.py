import re
import pytest

from pagewright.markdown import Markdown, render, safe_url, slugify


def md(text, **kw):
    return render(text, **kw)


# ---- Blocks --------------------------------------------------------------------------------


def test_paragraphs():
    assert md("one\ntwo\n\nthree") == "<p>one\ntwo</p>\n<p>three</p>"


def test_atx_headings_get_ids():
    assert md("# Hello World") == '<h1 id="hello-world">Hello World</h1>'
    assert md("### Closed ###") == '<h3 id="closed">Closed</h3>'
    assert md("####### seven") == "<p>####### seven</p>"
    assert md("#hashtag") == "<p>#hashtag</p>"


def test_setext_headings():
    assert md("Title\n=====") == '<h1 id="title">Title</h1>'
    assert md("Sub\n---") == '<h2 id="sub">Sub</h2>'


def test_duplicate_heading_ids_are_unique():
    out = md("## Intro\n\n## Intro\n\n## Intro")
    assert 'id="intro"' in out and 'id="intro-1"' in out and 'id="intro-2"' in out


def test_thematic_breaks():
    for line in ("---", "***", "___", "- - -", " * * *"):
        assert md(line) == "<hr />", line


def test_fenced_code_keeps_content_and_language():
    out = md("```python\nif a < b:\n    print('**hi**')\n```")
    assert out == '<pre><code class="language-python">if a &lt; b:\n    print(&#x27;**hi**&#x27;)\n</code></pre>'
    assert md("~~~\nplain\n~~~") == "<pre><code>plain\n</code></pre>"


def test_fence_must_close_with_same_kind_and_length():
    out = md("````\n```\nstill code\n````")
    assert out == "<pre><code>```\nstill code\n</code></pre>"


def test_unclosed_fence_runs_to_the_end():
    assert md("```\na\nb") == "<pre><code>a\nb\n</code></pre>"


def test_indented_code():
    assert md("    x = 1\n    y = 2") == "<pre><code>x = 1\ny = 2\n</code></pre>"


def test_blockquotes_nest_and_continue_lazily():
    assert md("> quoted\nlazy") == "<blockquote>\n<p>quoted\nlazy</p>\n</blockquote>"
    out = md("> outer\n> > inner")
    assert out.count("<blockquote>") == 2 and "<p>inner</p>" in out


def test_tight_list():
    assert md("- a\n- b\n- c") == "<ul>\n<li>a</li>\n<li>b</li>\n<li>c</li>\n</ul>"


def test_loose_list_wraps_items_in_paragraphs():
    assert md("- a\n\n- b") == "<ul>\n<li>\n<p>a</p>\n</li>\n<li>\n<p>b</p>\n</li>\n</ul>"


def test_ordered_list_start_number():
    assert md("3. three\n4. four") == '<ol start="3">\n<li>three</li>\n<li>four</li>\n</ol>'
    assert md("1) one") == "<ol>\n<li>one</li>\n</ol>"


def test_nested_lists():
    out = md("- a\n  - b\n    - c\n- d")
    assert out == ("<ul>\n<li>a\n<ul>\n<li>b\n<ul>\n<li>c</li>\n</ul></li>\n</ul></li>\n<li>d</li>\n</ul>")


def test_list_item_with_code_block():
    out = md("1. step\n\n   ```\n   run it\n   ```\n2. next")
    assert "<pre><code>run it\n</code></pre>" in out and out.count("<li>") == 2


def test_changing_bullet_starts_a_new_list():
    assert md("- a\n* b").count("<ul>") == 2


def test_numbers_only_interrupt_a_paragraph_at_one():
    assert md("The year\n2026. was good") == "<p>The year\n2026. was good</p>"
    assert "<ol>" in md("Steps:\n1. first")


def test_tables_with_alignment_and_escaped_pipes():
    out = md("| Name | Score | Note |\n|:-----|------:|:----:|\n| Ana | 10 | a \\| b |\n| Bo | 7 |")
    assert '<th style="text-align: left">Name</th>' in out
    assert '<td style="text-align: right">10</td>' in out
    assert '<td style="text-align: center">a | b</td>' in out
    assert "<tr><td style=\"text-align: left\">Bo</td><td style=\"text-align: right\">7</td>" in out


def test_table_needs_matching_delimiter_row():
    assert "<table>" not in md("| a | b |\n|---|\n| 1 | 2 |")


def test_html_blocks_pass_through_or_are_escaped():
    assert md("<div class=\"note\">\n*not md*\n</div>") == '<div class="note">\n*not md*\n</div>'
    assert md("<div>x</div>", allow_html=False) == "<p>&lt;div&gt;x&lt;/div&gt;</p>"


# ---- Inline --------------------------------------------------------------------------------


def test_emphasis():
    assert md("*em* _em_ **strong** __strong__ ***both***") == (
        "<p><em>em</em> <em>em</em> <strong>strong</strong> <strong>strong</strong> "
        "<em><strong>both</strong></em></p>"
    )


def test_intraword_underscores_stay():
    assert md("snake_case_name and a_b") == "<p>snake_case_name and a_b</p>"


def test_unmatched_stars_stay_literal():
    assert md("2 * 3 = 6") == "<p>2 * 3 = 6</p>"
    assert md("**not closed") == "<p>**not closed</p>"


def test_strikethrough():
    assert md("~~gone~~") == "<p><del>gone</del></p>"


def test_code_spans_are_literal():
    assert md("use `a * b` and ``x ` y``") == "<p>use <code>a * b</code> and <code>x ` y</code></p>"
    assert md("`<b>`") == "<p><code>&lt;b&gt;</code></p>"
    assert md("`unclosed") == "<p>`unclosed</p>"


def test_links_and_titles():
    assert md('[site](https://example.com "Home")') == '<p><a href="https://example.com" title="Home">site</a></p>'
    assert md("[**bold** link](/a)") == '<p><a href="/a"><strong>bold</strong> link</a></p>'
    assert md("[wiki](https://en.wikipedia.org/wiki/Foo_(bar))") == (
        '<p><a href="https://en.wikipedia.org/wiki/Foo_(bar)">wiki</a></p>'
    )
    assert md("[x](<with spaces.html>)") == '<p><a href="with%20spaces.html">x</a></p>'


def test_not_a_link():
    assert md("[just brackets]") == "<p>[just brackets]</p>"
    assert md("[a] (b)") == "<p>[a] (b)</p>"


def test_images():
    assert md('![a *cat*](/cat.png "Tom")') == '<p><img src="/cat.png" alt="a cat" title="Tom" /></p>'


def test_dangerous_urls_are_neutralised():
    assert md("[x](javascript:alert(1))") == '<p><a href="#">x</a></p>'
    assert md("[x](JaVaScRiPt:alert(1))") == '<p><a href="#">x</a></p>'
    assert md("![x](data:text/html;base64,AAAA)") == '<p><img src="#" alt="x" /></p>'


@pytest.mark.parametrize("url", ["https://a.b", "mailto:a@b.c", "/x", "page.html", "#top", "../up", "?q=1"])
def test_safe_urls_are_kept(url):
    assert safe_url(url) == url


def test_autolinks():
    assert md("<https://example.com/a?b=1&c=2>") == (
        '<p><a href="https://example.com/a?b=1&amp;c=2">https://example.com/a?b=1&amp;c=2</a></p>'
    )
    assert md("<me@example.com>") == '<p><a href="mailto:me@example.com">me@example.com</a></p>'


def test_escapes_and_html_characters():
    assert md(r"\*not em\* and \# and 5 < 6 & 7 > 1") == "<p>*not em* and # and 5 &lt; 6 &amp; 7 &gt; 1</p>"


def test_inline_html():
    assert md("a <kbd>Ctrl</kbd> b") == "<p>a <kbd>Ctrl</kbd> b</p>"
    assert md("a <kbd>Ctrl</kbd> b", allow_html=False) == "<p>a &lt;kbd&gt;Ctrl&lt;/kbd&gt; b</p>"
    assert md("x <script>alert(1)</script>", allow_html=False).count("&lt;script&gt;") == 1


def test_hard_line_breaks():
    assert md("one  \ntwo") == "<p>one<br />\ntwo</p>"
    assert md("one\\\ntwo") == "<p>one<br />\ntwo</p>"


def test_no_emphasis_inside_link_urls():
    assert md("[a](/x_y_z) and _em_") == '<p><a href="/x_y_z">a</a> and <em>em</em></p>'


# ---- Document ------------------------------------------------------------------------------


def test_headings_title_and_toc():
    doc = Markdown().convert("# My Post\n\n## Setup\n\n### Install `x`\n\n## Usage\n\n#### Deep")
    assert doc.title == "My Post"
    assert [(h.level, h.id, h.text) for h in doc.toc()] == [
        (1, "my-post", "My Post"), (2, "setup", "Setup"), (3, "install-x", "Install x"), (2, "usage", "Usage"),
    ]
    assert doc.toc_html() == (
        '<ul><li><a href="#setup">Setup</a><ul><li><a href="#install-x">Install x</a></li></ul></li>'
        '<li><a href="#usage">Usage</a></li></ul>'
    )
    assert Markdown().convert("no headings").toc_html() == ""


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  Ünïcödé   ok ") == "ünïcödé-ok"
    assert slugify("!!!") == "section"
    assert slugify("a_b--c") == "a-b-c"


def test_windows_line_endings_and_tabs():
    assert md("# T\r\n\r\npara") == '<h1 id="t">T</h1>\n<p>para</p>'
    assert md("\tcode") == "<pre><code>code\n</code></pre>"


def test_large_document_is_fast():
    import time

    text = "\n\n".join(f"## Section {i}\n\nSome *text* with a [link](/p{i}) and `code`.\n\n- a\n- b" for i in range(400))
    start = time.perf_counter()
    out = md(text)
    assert time.perf_counter() - start < 2
    assert out.count("<h2") == 400


def test_html_entities_pass_through():
    # Found while probing: these used to be escaped a second time (&amp;copy;).
    assert md("AT&amp;T &copy; &#169; &#xA9; a&b &notanentity") == (
        "<p>AT&amp;T &copy; &#169; &#xA9; a&amp;b &amp;notanentity</p>"
    )
    assert md("`&copy;`") == "<p><code>&amp;copy;</code></p>"  # literal inside code


def test_heading_ids_stay_unique_when_a_title_looks_like_a_suffix():
    out = render("## Same\n## Same\n## Same-1\n## Same")
    ids = re.findall(r'id="([^"]+)"', out)
    assert ids == ["same", "same-1", "same-1-1", "same-2"]
    assert len(set(ids)) == len(ids)


def test_unknown_entities_are_escaped_and_known_ones_kept():
    out = render("&bogus; &copy; &#169; &#x41; &#0;")
    assert out == "<p>&amp;bogus; &copy; &#169; &#x41; &amp;#0;</p>"


def test_spaces_in_angle_bracket_urls_are_encoded():
    assert 'href="my%20file.html"' in render("[a](<my file.html>)")
    assert 'src="a%20b.png"' in render("![x](<a b.png>)")


def test_task_lists():
    out = render("- [ ] todo\n- [x] **done**\n- [] not a task\n- plain")
    assert '<li class="task-list-item"><input type="checkbox" disabled /> todo</li>' in out
    assert '<li class="task-list-item"><input type="checkbox" disabled checked /> <strong>done</strong></li>' in out
    assert "<li>[] not a task</li>" in out and "<li>plain</li>" in out
    loose = render("- [ ] one\n\n- [x] two")
    assert '<p><input type="checkbox" disabled /> one</p>' in loose
    assert "<li>[ ] numbered</li>" in render("1. [ ] numbered")  # GitHub only does bullets
    assert "<li>[ ]</li>" in render("- [ ]")                      # needs text after the box
