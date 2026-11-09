from conftest import write
from pagewright.check import check_links
from pagewright.cli import main
from pagewright.site import Site


def test_starter_site_has_no_broken_links(site_dir):
    site = Site(site_dir)
    site.build()
    assert check_links(site.config.output_path, site.config.base_url) == []


def test_finds_missing_pages_images_and_anchors(site_dir):
    write(site_dir / "content/links.md", """+++
title = "Links"
aliases = ["/old-links/"]
+++
## Here

[ok](/about/) [ok relative](../about/) [ok anchor](#here) [ok other anchor](/posts/markdown-guide/#tables)
[ok file](/feed.xml) [external](https://example.org/nope) [mail](mailto:a@b.c)

[missing page](/nope/) [missing anchor](#nowhere) [bad other anchor](/about/#missing)
![missing image](/img/none.png) [escape](../../../../etc/passwd) [space](/my%20page/)
""")
    site = Site(site_dir)
    site.build()
    problems = check_links(site.config.output_path, site.config.base_url)
    found = {(p.page, p.link): p.reason for p in problems}
    page = "links/index.html"
    assert found == {
        (page, "/nope/"): "not found",
        (page, "#nowhere"): "no element with id 'nowhere' on links/index.html",
        (page, "/about/#missing"): "no element with id 'missing' on about/index.html",
        (page, "/img/none.png"): "not found",
        (page, "../../../../etc/passwd"): "points outside the site",
        (page, "/my%20page/"): "not found",
    }


def test_redirect_targets_are_checked(site_dir):
    write(site_dir / "content/a.md", '+++\ntitle = "A"\naliases = ["/old-a/"]\n+++\nA\n')
    site = Site(site_dir)
    site.build()
    assert check_links(site.config.output_path, site.config.base_url) == []
    (site_dir / "public/a/index.html").unlink()  # the redirect now points nowhere (and so does the menu)
    problems = check_links(site.config.output_path, site.config.base_url)
    assert ("old-a/index.html", "https://example.com/a/", "not found") in [(p.page, p.link, p.reason) for p in problems]


def test_check_command(site_dir, capsys):
    assert main(["check", "-s", str(site_dir)]) == 0
    assert "No broken links" in capsys.readouterr().out
    write(site_dir / "content/bad.md", "[x](/gone/)\n")
    assert main(["check", "-s", str(site_dir)]) == 1
    out = capsys.readouterr().out
    assert "1 broken link(s)" in out and "bad/index.html: /gone/ (not found)" in out
