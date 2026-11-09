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
