import json
import os
import xml.etree.ElementTree as ET

import pytest

from conftest import write
from pagewright.config import ConfigError, parse_config
from pagewright.site import BuildError, Site, paginate


def build(site_dir, **kw):
    return Site(site_dir, **kw).build()


def read(site_dir, path):
    return (site_dir / "public" / path).read_text(encoding="utf-8")


def touch_later(path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


# ---- Full build ----------------------------------------------------------------------------


def test_starter_site_builds_every_kind_of_page(site_dir):
    report = build(site_dir)
    expected = {
        "index.html", "about/index.html", "posts/index.html", "posts/hello-world/index.html",
        "posts/markdown-guide/index.html", "tags/index.html", "tags/meta/index.html", "tags/writing/index.html",
        "404.html", "feed.xml", "sitemap.xml", "search-index.json", "robots.txt",
    }
    assert set(report.rendered) == expected
    assert (site_dir / "public" / "style.css").exists() and (site_dir / "public" / "search.js").exists()

    home = read(site_dir, "index.html")
    assert "Welcome!" in home and home.index("A quick Markdown guide") < home.index("Hello, world")  # newest first
    post = read(site_dir, "posts/markdown-guide/index.html")
    assert '<nav class="toc"' in post and '<pre><code class="language-python">' in post
    assert "<title>A quick Markdown guide · My Site</title>" in post
    assert 'href="/tags/writing/"' in post
    assert '<a href="/about/">About</a>' in home  # top-level pages are in the menu


def test_feed_and_sitemap_are_valid_xml(site_dir):
    build(site_dir)
    feed = ET.fromstring(read(site_dir, "feed.xml"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    titles = [e.findtext("a:title", namespaces=ns) for e in feed.findall("a:entry", ns)]
    assert titles == ["A quick Markdown guide", "Hello, world"]
    assert feed.find("a:entry/a:link", ns).get("href") == "https://example.com/posts/markdown-guide/"
    sitemap = ET.fromstring(read(site_dir, "sitemap.xml"))
    locs = {u.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}loc") for u in sitemap}
    assert "https://example.com/about/" in locs and "https://example.com/posts/" in locs


def test_search_index(site_dir):
    build(site_dir)
    index = json.loads(read(site_dir, "search-index.json"))
    guide = next(p for p in index["pages"] if p["url"] == "/posts/markdown-guide/")
    assert guide["tags"] == ["meta", "writing"] and "Simplicity" in guide["text"] and "<" not in guide["text"]


def test_drafts_are_left_out_unless_asked(site_dir):
    write(site_dir / "content/posts/secret.md", '+++\ntitle = "Secret"\ndraft = true\ndate = 2026-02-01\n+++\nshh')
    report = build(site_dir)
    assert report.drafts_skipped == 1 and not (site_dir / "public/posts/secret").exists()
    assert "Secret" not in read(site_dir, "index.html")
    build(site_dir, drafts=True)
    assert (site_dir / "public/posts/secret/index.html").exists()


def test_pagination(site_dir):
    write(site_dir / "pagewright.toml",
          (site_dir / "pagewright.toml").read_text().replace("posts_per_page = 10", "posts_per_page = 2"))
    for i in range(1, 6):
        write(site_dir / f"content/posts/2026-03-0{i}-post-{i}.md", f"Post {i}")
    report = build(site_dir)
    assert {"page/2/index.html", "page/3/index.html", "posts/page/2/index.html"} <= set(report.rendered)
    page2 = read(site_dir, "page/2/index.html")
    assert 'rel="prev" href="/"' in page2 and 'rel="next" href="/page/3/"' in page2 and "Page 2 of 4" in page2


def test_paginate_helper():
    pages = paginate(list(range(5)), 2, "/blog/")
    assert [p.items for p in pages] == [[0, 1], [2, 3], [4]]
    assert (pages[0].prev_url, pages[0].next_url, pages[2].url_for(3)) == ("", "/blog/page/2/", "/blog/page/3/")
    assert paginate([], 10, "/")[0].items == []


def test_site_templates_override_the_theme(site_dir):
    write(site_dir / "templates/partials/footer.html", "<footer>my own footer {{ site.extra.github }}</footer>")
    build(site_dir)
    assert "<footer>my own footer </footer>" in read(site_dir, "about/index.html")


def test_site_static_overrides_theme_static(site_dir):
    write(site_dir / "static/style.css", "body { color: red }")
    write(site_dir / "static/img/logo.svg", "<svg/>")
    build(site_dir)
    assert read(site_dir, "style.css") == "body { color: red }"
    assert read(site_dir, "img/logo.svg") == "<svg/>"


def test_custom_template_from_front_matter(site_dir):
    write(site_dir / "templates/landing.html", "LANDING {{ page.title }} {{ content }}")
    write(site_dir / "content/launch.md", '+++\ntitle = "Launch"\ntemplate = "landing.html"\n+++\n**Soon**')
    build(site_dir)
    assert read(site_dir, "launch/index.html") == "LANDING Launch <p><strong>Soon</strong></p>"


# ---- Incremental builds --------------------------------------------------------------------


def test_second_build_renders_nothing(site_dir):
    build(site_dir)
    report = build(site_dir)
    assert report.rendered == [] and len(report.skipped) >= 13


def test_editing_a_post_body_rerenders_only_that_post(site_dir):
    build(site_dir)
    post = site_dir / "content/posts/2026-01-20-markdown-guide.md"
    post.write_text(post.read_text().replace("## Quotes", "## Quotations"))
    report = build(site_dir)
    # The post itself, plus the feed and search index, which include full text. No list pages.
    assert sorted(report.rendered) == ["feed.xml", "posts/markdown-guide/index.html", "search-index.json"]
    assert "Quotations" in read(site_dir, "posts/markdown-guide/index.html")


def test_changing_a_title_rerenders_lists(site_dir):
    build(site_dir)
    post = site_dir / "content/posts/2026-01-15-hello-world.md"
    post.write_text(post.read_text().replace('title = "Hello, world"', 'title = "Hello again"'))
    report = build(site_dir)
    assert {"index.html", "posts/index.html", "tags/meta/index.html"} <= set(report.rendered)
    assert "Hello again" in read(site_dir, "index.html")


def test_changing_a_template_rerenders_its_users(site_dir):
    build(site_dir)
    footer = site_dir / "templates/partials/footer.html"
    write(footer, "<footer>v2</footer>")
    report = build(site_dir)
    assert {"index.html", "about/index.html", "posts/markdown-guide/index.html"} <= set(report.rendered)
    assert "<footer>v2</footer>" in read(site_dir, "about/index.html")
    write(footer, "<footer>v3</footer>")
    touch_later(footer)
    build(site_dir)
    assert "<footer>v3</footer>" in read(site_dir, "about/index.html")


def test_deleted_content_is_removed_from_the_output(site_dir):
    build(site_dir)
    (site_dir / "content/about.md").unlink()
    report = build(site_dir)
    assert "about/index.html" in report.removed
    assert not (site_dir / "public/about").exists()


def test_deleted_output_file_is_rebuilt(site_dir):
    build(site_dir)
    (site_dir / "public/about/index.html").unlink()
    assert build(site_dir).rendered == ["about/index.html"]


def test_clean_and_force(site_dir):
    build(site_dir)
    write(site_dir / "public/stray.txt", "left over")
    report = Site(site_dir).build(clean=True)
    assert not (site_dir / "public/stray.txt").exists() and len(report.rendered) >= 13
    assert len(Site(site_dir).build(force=True).rendered) >= 13


def test_corrupt_cache_means_a_full_rebuild(site_dir):
    build(site_dir)
    (site_dir / ".pagewright-cache.json").write_text("{nope")
    assert len(build(site_dir).rendered) >= 13


# ---- Plugins -------------------------------------------------------------------------------


def test_example_plugin_adds_filters_data_and_hooks(site_dir, capsys):
    toml = site_dir / "pagewright.toml"
    toml.write_text(toml.read_text().replace('# plugins = ["word_count"]', 'plugins = ["word_count"]'))
    write(site_dir / "templates/partials/footer.html", "<footer>{{ site.data.total_words }} words</footer>")
    build(site_dir)
    assert "word_count:" in capsys.readouterr().out
    footer = read(site_dir, "about/index.html")
    assert int(footer.split("<footer>")[1].split(" words")[0]) > 50


def test_plugin_can_rewrite_markdown_and_html(site_dir):
    write(site_dir / "plugins/shout.py",
          "def on_page_markdown(page, md):\n    return md.replace('Welcome', 'WELCOME')\n\n"
          "def on_page_html(page, html):\n    return html + '<!-- shouted -->'\n")
    toml = site_dir / "pagewright.toml"
    toml.write_text(toml.read_text().replace('# plugins = ["word_count"]', 'plugins = ["shout"]'))
    build(site_dir)
    home = read(site_dir, "index.html")
    assert "WELCOME" in home and "<!-- shouted -->" in home


def test_plugin_errors_name_the_plugin(site_dir):
    write(site_dir / "plugins/broken.py", "def on_site(site):\n    raise RuntimeError('oops')\n")
    toml = site_dir / "pagewright.toml"
    toml.write_text(toml.read_text().replace('# plugins = ["word_count"]', 'plugins = ["broken"]'))
    with pytest.raises(BuildError, match="plugin 'broken' failed in on_site: oops"):
        build(site_dir)
    toml.write_text(toml.read_text().replace('plugins = ["broken"]', 'plugins = ["missing"]'))
    with pytest.raises(BuildError, match="plugin 'missing' not found"):
        Site(site_dir)


# ---- Errors --------------------------------------------------------------------------------


def test_duplicate_urls_are_reported(site_dir):
    write(site_dir / "content/about/index.md", "dupe? no, this is a section index")
    write(site_dir / "content/x.md", '+++\nurl = "/about/"\n+++\nclash')
    with pytest.raises(BuildError, match="both want the URL /about/"):
        build(site_dir)


def test_template_errors_name_the_output(site_dir):
    write(site_dir / "templates/page.html", "{{ page.title | nope }}")
    with pytest.raises(BuildError, match=r"page\.html.*unknown filter 'nope'.*while rendering about/index\.html"):
        build(site_dir)


def test_content_errors_name_the_file(site_dir):
    write(site_dir / "content/posts/bad.md", "+++\ntags = 'x'\n+++\n")
    with pytest.raises(BuildError, match="content/posts/bad.md: 'tags' must be a list"):
        build(site_dir)


def test_static_file_cannot_overwrite_a_page(site_dir):
    write(site_dir / "static/feed.xml", "<x/>")
    with pytest.raises(BuildError, match="would overwrite"):
        build(site_dir)


@pytest.mark.parametrize(
    "toml, message",
    [
        ('title = ""', "non-empty"),
        ('base_url = "example.com"', "http"),
        ('output_dir = "content"', "its own folder"),
        ('output_dir = "../outside"', "inside the site"),
        ("posts_per_page = 0", "1 to 1000"),
        ('colour = "red"', "unknown setting"),
        ('plugins = ["not valid"]', "plugin names"),
        ("[markdown]\nsmart = true", "allow_html"),
    ],
)
def test_config_errors(tmp_path, toml, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(toml, tmp_path)


def test_base_url_override_for_previews(site_dir):
    Site(site_dir, base_url="http://localhost:9000").build()
    assert "http://localhost:9000/posts/" in read(site_dir, "sitemap.xml")


def test_plugin_filters_can_be_a_dict_or_a_function(site_dir):
    write(site_dir / "plugins/fn.py", "def filters():\n    return {'shout': lambda s: str(s).upper()}\n\n"
                                      "def template_globals():\n    return {'answer': 42}\n")
    write(site_dir / "templates/partials/footer.html", "<footer>{{ 'hi' | shout }} {{ answer }}</footer>")
    toml = site_dir / "pagewright.toml"
    toml.write_text(toml.read_text().replace('# plugins = ["word_count"]', 'plugins = ["fn"]'))
    build(site_dir)
    assert "<footer>HI 42</footer>" in read(site_dir, "about/index.html")

    write(site_dir / "plugins/fn.py", "filters = {'shout': 'not a function'}\n")
    with pytest.raises(BuildError, match="every filter must be a function"):
        build(site_dir)


def test_editing_a_plugin_rerenders_pages(site_dir):
    write(site_dir / "plugins/tag.py", "def on_page_html(page, html):\n    return html + '<!-- v1 -->'\n")
    toml = site_dir / "pagewright.toml"
    toml.write_text(toml.read_text().replace('# plugins = ["word_count"]', 'plugins = ["tag"]'))
    build(site_dir)
    write(site_dir / "plugins/tag.py", "def on_page_html(page, html):\n    return html + '<!-- v2 -->'\n")
    report = build(site_dir)
    assert "about/index.html" in report.rendered
    assert "<!-- v2 -->" in read(site_dir, "about/index.html")


def test_lower_weight_comes_first_in_menu_and_sections(site_dir):
    write(site_dir / "content/contact.md", '+++\ntitle = "Contact"\nweight = 1\n+++\nHi.\n')
    write(site_dir / "content/zebra.md", '+++\ntitle = "Zebra"\nweight = 1\n+++\nHi.\n')
    write(site_dir / "content/posts/pinned.md", '+++\ntitle = "Pinned"\ndate = 2020-01-01\nweight = -1\n+++\nOld but first.\n')
    site = Site(site_dir)
    site.load()
    menu = [p.title for p in site.site_context()["menu"]]
    assert menu == ["Contact", "Zebra", "About"]  # About has weight 10
    assert site.sections["posts"][0].title == "Pinned"


def test_tags_differing_only_in_case_are_merged(site_dir):
    write(site_dir / "content/posts/a.md", '+++\ntitle = "A"\ndate = 2026-03-01\ntags = ["Python"]\n+++\na\n')
    write(site_dir / "content/posts/b.md", '+++\ntitle = "B"\ndate = 2026-03-02\ntags = ["python"]\n+++\nb\n')
    site = Site(site_dir)
    site.load()
    names = [t for t in site.tags if t.lower() == "python"]
    assert len(names) == 1 and {p.title for p in site.tags[names[0]]} == {"A", "B"}
    build(site_dir)
    page = read(site_dir, "tags/python/index.html")
    assert ">A<" in page and ">B<" in page


def test_different_tags_with_the_same_url_are_an_error(site_dir):
    write(site_dir / "content/posts/c.md", '+++\ntitle = "C"\ndate = 2026-03-01\ntags = ["C"]\n+++\nc\n')
    write(site_dir / "content/posts/cpp.md", '+++\ntitle = "C++"\ndate = 2026-03-02\ntags = ["C++"]\n+++\ncpp\n')
    with pytest.raises(BuildError, match=r"would both be /tags/c/"):
        build(site_dir)


def test_data_files_are_available_to_templates(site_dir):
    write(site_dir / "data/authors.toml", '[ana]\nname = "Ana"\n')
    write(site_dir / "data/links/social.json", '[{"name": "GitHub", "url": "https://github.com"}]')
    write(site_dir / "data/_notes.toml", "ignored = true")
    write(site_dir / "templates/partials/footer.html",
          "<footer>{{ site.data.authors.ana.name }} {% for l in site.data.links.social %}{{ l.name }}{% endfor %}"
          "</footer>")
    build(site_dir)
    assert "<footer>Ana GitHub</footer>" in read(site_dir, "about/index.html")
    site = Site(site_dir)
    site.load()
    assert set(site.data) == {"authors", "links"}  # _notes.toml is skipped

    write(site_dir / "data/authors.toml", '[ana]\nname = "Ana María"\n')
    report = build(site_dir)
    assert "about/index.html" in report.rendered  # data is part of every fingerprint
    assert "Ana María" in read(site_dir, "about/index.html")


def test_bad_or_clashing_data_files_are_errors(site_dir):
    write(site_dir / "data/a.toml", "x = ")
    with pytest.raises(BuildError, match="data/a.toml: can't be read"):
        build(site_dir)
    write(site_dir / "data/a.toml", "x = 1")
    write(site_dir / "data/a.json", "{}")
    with pytest.raises(BuildError, match="already has 'a'"):
        build(site_dir)


def test_plugin_data_changes_rerender_pages_that_show_it(site_dir):
    toml = site_dir / "pagewright.toml"
    toml.write_text(toml.read_text().replace('# plugins = ["word_count"]', 'plugins = ["word_count"]'))
    write(site_dir / "templates/partials/footer.html", "<footer>{{ site.data.total_words }}</footer>")
    build(site_dir)
    before = read(site_dir, "about/index.html")
    post = site_dir / "content/posts/2026-01-15-hello-world.md"
    post.write_text(post.read_text() + "\nThree more words.\n")
    build(site_dir)
    assert read(site_dir, "about/index.html") != before


def test_prev_next_and_related_posts(site_dir):
    write(site_dir / "content/posts/2026-02-01-third.md", '+++\ntitle = "Third"\ntags = ["intro", "x"]\n+++\nt\n')
    write(site_dir / "content/notes/2026-02-02-note.md", '+++\ntitle = "A note"\ntags = ["intro"]\n+++\nn\n')
    site = Site(site_dir)
    site.load()
    posts = {p.title: p for p in site.posts}
    hello, guide, third = posts["Hello, world"], posts["A quick Markdown guide"], posts["Third"]
    assert hello.prev is None and hello.next is guide
    assert guide.prev is hello and guide.next is third
    assert third.next is None
    assert posts["A note"].prev is None and posts["A note"].next is None  # its own section
    assert [p.title for p in third.related] == ["A note"]  # shares "intro", across sections
    assert [p.title for p in hello.related] == ["A quick Markdown guide"]

    build(site_dir)
    page = read(site_dir, "posts/markdown-guide/index.html")
    assert 'rel="prev"' in page and "Hello, world" in page and 'rel="next"' in page and "Third" in page


def test_related_ranks_by_shared_tags(site_dir):
    for name, tags in (("a", '["p", "q", "r"]'), ("b", '["p"]'), ("c", '["p", "q"]'), ("d", '["z"]')):
        write(site_dir / f"content/blog/2026-03-0{'abcd'.index(name) + 1}-{name}.md",
              f'+++\ntitle = "{name}"\ntags = {tags}\n+++\n.\n')
    site = Site(site_dir)
    site.load()
    a = next(p for p in site.posts if p.title == "a")
    assert [p.title for p in a.related] == ["c", "b"]


def test_aliases_write_redirect_pages(site_dir):
    write(site_dir / "content/posts/2026-03-01-new-name.md",
          '+++\ntitle = "New & improved"\naliases = ["/2019/old-name/", "/old.html", "/posts/older"]\n+++\nHi.\n')
    build(site_dir)
    page = read(site_dir, "2019/old-name/index.html")
    assert 'http-equiv="refresh" content="0; url=https://example.com/posts/new-name/"' in page
    assert 'rel="canonical"' in page and "noindex" in page and "New &amp; improved" in page
    assert (site_dir / "public/old.html").is_file()
    assert (site_dir / "public/posts/older/index.html").is_file()
    assert "old-name" not in read(site_dir, "sitemap.xml")

    # Removing an alias removes its redirect page on the next build.
    write(site_dir / "content/posts/2026-03-01-new-name.md", '+++\ntitle = "New"\n+++\nHi.\n')
    report = build(site_dir)
    assert "2019/old-name/index.html" in report.removed and not (site_dir / "public/2019").exists()


def test_alias_clashes_and_bad_aliases(site_dir):
    write(site_dir / "content/posts/x.md", '+++\naliases = ["/about/"]\n+++\nx\n')
    with pytest.raises(BuildError, match=r"alias /about/ clashes with the page about/index.html"):
        build(site_dir)
    write(site_dir / "content/posts/x.md", '+++\naliases = ["old/"]\n+++\nx\n')
    with pytest.raises(BuildError, match="must start with / and stay inside the site"):
        build(site_dir)
    write(site_dir / "content/posts/x.md", '+++\naliases = ["/a/"]\n+++\nx\n')
    write(site_dir / "content/posts/y.md", '+++\naliases = ["/a"]\n+++\ny\n')
    with pytest.raises(BuildError, match="clashes with an alias of content/posts/x.md"):
        build(site_dir)
