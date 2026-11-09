from datetime import date

import pytest

from pagewright.content import ContentError, load_pages, parse_page
from pagewright.markdown import Markdown


def test_front_matter_and_url():
    page = parse_page('+++\ntitle = "Hi"\ndate = 2026-03-04\ntags = ["a", "b", "a"]\n+++\n\nBody', "posts/hi.md")
    assert (page.title, page.date, page.tags, page.url, page.section) == ("Hi", date(2026, 3, 4), ["a", "b"], "/posts/hi/",
                                                                         "posts")
    assert page.output_path == "posts/hi/index.html" and page.is_post


@pytest.mark.parametrize(
    "source, url",
    [
        ("index.md", "/"),
        ("about.md", "/about/"),
        ("posts/index.md", "/posts/"),
        ("posts/_index.md", "/posts/"),
        ("docs/guides/setup.md", "/docs/guides/setup/"),
        ("posts/2026-01-02-new-year.md", "/posts/new-year/"),
        ("My Notes/First Draft.md", "/my-notes/first-draft/"),
    ],
)
def test_urls_follow_the_file_layout(source, url):
    assert parse_page("text", source).url == url


def test_date_from_file_name_and_explicit_overrides():
    assert parse_page("x", "posts/2026-01-02-a.md").date == date(2026, 1, 2)
    assert parse_page("+++\ndate = 2025-12-31\n+++\nx", "posts/2026-01-02-a.md").date == date(2025, 12, 31)
    assert parse_page("x", "posts/2026-13-45-bad.md").date is None  # not a real date: stays in the slug


def test_slug_and_url_overrides():
    assert parse_page('+++\nslug = "short"\n+++\nx', "posts/a-long-name.md").url == "/posts/short/"
    assert parse_page('+++\nurl = "/custom/place"\n+++\nx', "posts/a.md").url == "/custom/place/"
    assert parse_page('+++\nurl = "/feed.html"\n+++\nx', "a.md").output_path == "feed.html"


def test_title_falls_back_to_first_heading_then_file_name():
    assert parse_page("# From Heading\n\ntext", "a.md").title == "From Heading"
    assert parse_page("no heading", "my-great_page.md").title == "My great page"


def test_summary_rules():
    md = Markdown()
    page = parse_page("First para.\n\nSecond para.", "posts/a.md")
    page.render_markdown(md)
    assert page.summary == "<p>First para.</p>"
    page = parse_page("Intro *here*.<!--more-->\n\nRest.", "posts/b.md")
    page.render_markdown(md)
    assert page.summary == "<p>Intro <em>here</em>.</p>"
    page = parse_page('+++\nsummary = "Custom **one**"\n+++\nBody', "posts/c.md")
    page.render_markdown(md)
    assert page.summary == "<p>Custom <strong>one</strong></p>"


def test_toc_and_section_index():
    page = parse_page('+++\ntoc = true\n+++\n## A\n\n## B', "posts/index.md")
    page.render_markdown(Markdown())
    assert page.is_section_index and not page.is_post and page.show_toc
    assert 'href="#a"' in page.toc


@pytest.mark.parametrize(
    "text, message",
    [
        ("+++\ntitle = \n+++\n", "isn't valid TOML"),
        ("+++\ntitel = 'x'\n+++\n", "unknown front matter field(s) titel"),
        ("+++\ntags = 'one'\n+++\n", "'tags' must be a list"),
        ("+++\ndate = 'soon'\n+++\n", "'date' must be a date"),
        ("+++\ndraft = 'yes'\n+++\n", "'draft' must be true or false"),
        ("+++\nslug = 'Has Spaces'\n+++\n", "'slug' can only use"),
        ("+++\nurl = 'relative'\n+++\n", "'url' must start with /"),
        ("+++\nurl = '/../escape'\n+++\n", "stay inside the site"),
        ("---\ntitle: x\n---\n", "YAML"),
    ],
)
def test_bad_front_matter_is_explained(text, message):
    with pytest.raises(ContentError, match=message.replace("(", r"\(").replace(")", r"\)")):
        parse_page(text, "posts/x.md")


def test_load_pages_skips_hidden_and_underscore_files(tmp_path):
    (tmp_path / "posts").mkdir()
    (tmp_path / "posts" / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "posts" / "_index.md").write_text("index", encoding="utf-8")
    (tmp_path / "_drafts").mkdir()
    (tmp_path / "_drafts" / "secret.md").write_text("no", encoding="utf-8")
    (tmp_path / ".hidden.md").write_text("no", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not markdown", encoding="utf-8")
    assert [p.source for p in load_pages(tmp_path)] == ["posts/_index.md", "posts/a.md"]
    assert load_pages(tmp_path / "missing") == []


def test_bom_and_windows_newlines():
    page = parse_page('﻿+++\r\ntitle = "T"\r\n+++\r\nBody', "a.md")
    assert page.title == "T" and page.body.strip() == "Body"


def test_footnote_markers_are_left_out_of_summaries():
    page = parse_page("First paragraph[^1] here.\n\n[^1]: The note.\n", "posts/a.md")
    page.render_markdown(Markdown())
    assert "footnote" not in page.summary and "First paragraph here." in page.summary
    page = parse_page("Intro[^1].\n\n<!--more-->\n\nRest.\n\n[^1]: Note.\n", "posts/b.md")
    page.render_markdown(Markdown())
    assert "[^1]" not in page.summary and "Intro." in page.summary
