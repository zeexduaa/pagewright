from datetime import date

import pytest

from conftest import write
from pagewright.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_new_creates_a_site_that_builds(tmp_path, capsys):
    target = tmp_path / "blog"
    code, out, _ = run(capsys, "new", str(target))
    assert code == 0 and "pagewright serve" in out
    assert (target / "pagewright.toml").is_file()
    assert (target / "themes" / "default" / "templates" / "base.html").is_file()
    assert (target / "static").is_dir() and not (target / "static" / ".gitkeep").exists()
    assert "public/" in (target / ".gitignore").read_text(encoding="utf-8")

    code, out, _ = run(capsys, "build", "-s", str(target))
    assert code == 0 and "16 written" in out
    assert (target / "public" / "index.html").is_file()


def test_new_refuses_a_folder_with_files_unless_forced(tmp_path, capsys):
    write(tmp_path / "notes.txt", "mine")
    code, _, err = run(capsys, "new", str(tmp_path))
    assert code == 1 and "isn't empty" in err
    code, _, _ = run(capsys, "new", str(tmp_path), "--force")
    assert code == 0 and (tmp_path / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_build_is_incremental_and_force_rerenders(site_dir, capsys):
    run(capsys, "build", "-s", str(site_dir))
    _, out, _ = run(capsys, "build", "-s", str(site_dir))
    assert "0 written" in out
    _, out, _ = run(capsys, "build", "-s", str(site_dir), "--force")
    assert "0 written" not in out


def test_build_base_url_override(site_dir, capsys):
    run(capsys, "build", "-s", str(site_dir), "--base-url", "https://preview.example.org")
    assert "https://preview.example.org/" in (site_dir / "public" / "sitemap.xml").read_text(encoding="utf-8")


def test_build_reports_errors_without_a_traceback(tmp_path, capsys):
    code, _, err = run(capsys, "build", "-s", str(tmp_path))
    assert code == 1 and "no pagewright.toml" in err and "pagewright new" in err


def test_post_creates_a_dated_draft(site_dir, capsys):
    code, out, _ = run(capsys, "post", 'Why "quotes" matter', "-s", str(site_dir))
    assert code == 0 and "draft" in out
    path = site_dir / "content" / "posts" / f"{date.today().isoformat()}-why-quotes-matter.md"
    text = path.read_text(encoding="utf-8")
    assert 'title = "Why \\"quotes\\" matter"' in text and "draft = true" in text

    run(capsys, "build", "-s", str(site_dir))
    assert not (site_dir / "public" / "posts" / "why-quotes-matter").exists()
    run(capsys, "build", "-s", str(site_dir), "--drafts")
    page = (site_dir / "public" / "posts" / "why-quotes-matter" / "index.html").read_text(encoding="utf-8")
    assert "Why &quot;quotes&quot; matter" in page or 'Why &#34;quotes&#34; matter' in page

    code, _, err = run(capsys, "post", 'Why "quotes" matter', "-s", str(site_dir))
    assert code == 1 and "already exists" in err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "pagewright 0.1.0" in capsys.readouterr().out


def test_build_profile_lists_slowest_outputs(site_dir, capsys):
    code, out, _ = run(capsys, "build", "-s", str(site_dir), "--profile", "3")
    assert code == 0 and "Slowest 3 (of" in out
    rows = [line for line in out.splitlines() if line.startswith("  ")]
    assert len(rows) == 3 and all(" ms " in row for row in rows)
    _, out, _ = run(capsys, "build", "-s", str(site_dir), "--profile")
    assert "posts/" not in out.split("Slowest")[1]  # nothing re-rendered, so no page timings
    _, out, _ = run(capsys, "build", "-s", str(site_dir))
    assert "Slowest" not in out
