"""Command-line interface.

    pagewright new my-site            create a site from the starter template
    pagewright build                  build into public/ (incremental)
    pagewright serve                  dev server with live reload (includes drafts)
    pagewright post "My title"        start a new post (as a draft)
    pagewright check                  build, then look for broken internal links
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import date
from pathlib import Path

from . import __version__
from .config import CONFIG_FILE, ConfigError, load_config
from .content import ContentError
from .markdown import slugify
from .site import BUILTIN_THEMES, BuildError, Site

SCAFFOLD = Path(__file__).parent / "scaffold" / "site"


def cmd_new(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"pagewright: {target} isn't empty (use --force to add the starter files anyway)", file=sys.stderr)
        return 1
    # Never overwrite: with --force, files that already exist are kept as they are.
    kept = _copy_missing(SCAFFOLD, target) + _copy_missing(BUILTIN_THEMES / "default", target / "themes" / "default")
    for folder in ("static", "templates"):
        (target / folder).mkdir(parents=True, exist_ok=True)
    gitignore = target / ".gitignore"
    if gitignore.exists():
        kept.append(gitignore)
    else:
        gitignore.write_text("public/\n.pagewright-cache.json\n__pycache__/\n", encoding="utf-8")
    if kept:
        names = ", ".join(str(p.relative_to(target)) for p in kept[:5]) + (f" and {len(kept) - 5} more" if len(kept) > 5 else "")
        print(f"Kept {len(kept)} existing file(s) unchanged: {names}")
    print(f"Created a new site in {target}.\n\nNext:\n  cd {target}\n  pagewright serve")
    return 0


def _copy_missing(source: Path, target: Path) -> list[Path]:
    """Copies files from source into target, skipping any that already exist. Returns the skipped ones."""
    kept = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep" or "__pycache__" in path.parts:
            continue
        dest = target / path.relative_to(source)
        if dest.exists():
            kept.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return kept


def cmd_build(args: argparse.Namespace) -> int:
    site = Site(args.site, drafts=args.drafts, base_url=args.base_url)
    report = site.build(clean=args.clean, force=args.force)
    print(f"Built {site.config.output_path}: {report.summary()}")
    if args.profile:
        slowest = report.slowest(args.profile)
        if slowest:
            total = report.seconds or 1e-9
            width = max(len(name) for name, _ in slowest)
            hint = "" if args.force else "; unchanged outputs aren't rendered, add --force to time everything"
            print(f"\nSlowest {len(slowest)} (of {len(report.timings)} timed{hint}):")
            for name, seconds in slowest:
                print(f"  {name:<{width}}  {seconds * 1000:7.1f} ms  {seconds / total:4.0%}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .check import check_links

    site = Site(args.site, drafts=args.drafts)
    report = site.build()
    problems = check_links(site.config.output_path, site.config.base_url)
    pages = sum(1 for _ in site.config.output_path.rglob("*.html"))
    if not problems:
        print(f"No broken links in {pages} HTML file(s). ({report.summary()})")
        return 0
    print(f"{len(problems)} broken link(s) in {pages} HTML file(s):\n")
    for problem in problems:
        print(f"  {problem}")
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import DevServer

    load_config(args.site)  # fail early with a clear message
    server = DevServer(args.site, host=args.host, port=args.port, drafts=not args.no_drafts)
    server.start()
    print(f"Serving {Path(args.site).resolve()} at {server.url}  (Ctrl+C to stop)", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.stop()
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    config = load_config(args.site)
    slug = slugify(args.title)
    today = date.today()
    folder = config.content_path / args.section
    path = folder / f"{today.isoformat()}-{slug}.md"
    if path.exists():
        print(f"pagewright: {path} already exists", file=sys.stderr)
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    title = args.title.replace("\\", "\\\\").replace('"', '\\"')
    path.write_text(f'+++\ntitle = "{title}"\ndraft = true\ntags = []\n+++\n\nWrite here.\n', encoding="utf-8")
    print(f"Created {path.relative_to(config.root)} (a draft: `pagewright serve` shows it, `build` doesn't "
          "until you remove draft = true).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pagewright", description="A static site generator with no dependencies.")
    parser.add_argument("--version", action="version", version=f"pagewright {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    site = argparse.ArgumentParser(add_help=False)
    site.add_argument("-s", "--site", default=".", help=f"site folder, the one with {CONFIG_FILE} (default: .)")

    p = sub.add_parser("new", help="create a new site")
    p.add_argument("path")
    p.add_argument("--force", action="store_true",
                   help="add the starter files even if the folder isn't empty (existing files are never overwritten)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("build", help="build the site into the output folder", parents=[site])
    p.add_argument("--drafts", action="store_true", help="include draft pages")
    p.add_argument("--clean", action="store_true", help="empty the output folder first")
    p.add_argument("--force", action="store_true", help="re-render everything, ignoring the cache")
    p.add_argument("--base-url", help="override base_url (e.g. for a preview deploy)")
    p.add_argument("--profile", type=int, nargs="?", const=10, default=0, metavar="N",
                   help="show the N slowest outputs and build steps (default 10)")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("check", help="build, then report broken internal links and anchors", parents=[site])
    p.add_argument("--drafts", action="store_true", help="include draft pages")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("serve", help="dev server with live reload", parents=[site])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-drafts", action="store_true", help="hide drafts, as in the real build")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("post", help="start a new post (as a draft)", parents=[site])
    p.add_argument("title")
    p.add_argument("--section", default="posts", help="content folder for the post (default: posts)")
    p.set_defaults(func=cmd_post)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, BuildError, ContentError) as exc:
        print(f"pagewright: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        if getattr(exc, "errno", None) in (48, 98, 10048):  # address already in use (macOS, Linux, Windows)
            print(f"pagewright: port {getattr(args, 'port', '')} is already in use; try --port 8001", file=sys.stderr)
            return 1
        raise
