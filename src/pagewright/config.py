"""Site configuration from ``pagewright.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = "pagewright.toml"


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    root: Path
    title: str = "My Site"
    base_url: str = "http://localhost:8000"
    description: str = ""
    author: str = ""
    language: str = "en"
    theme: str = "default"
    content_dir: str = "content"
    static_dir: str = "static"
    templates_dir: str = "templates"
    output_dir: str = "public"
    posts_per_page: int = 10
    feed_limit: int = 20
    allow_html: bool = True
    smart_punctuation: bool = False
    plugins: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def content_path(self) -> Path:
        return self.root / self.content_dir

    @property
    def output_path(self) -> Path:
        return self.root / self.output_dir

    def absolute_url(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")


STRING_FIELDS = ("title", "base_url", "description", "author", "language", "theme",
                 "content_dir", "static_dir", "templates_dir", "output_dir")


def parse_config(text: str, root: Path, where: str = CONFIG_FILE) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{where}: not valid TOML: {exc}") from None

    known = set(STRING_FIELDS) | {"posts_per_page", "feed_limit", "markdown", "plugins", "extra"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{where}: unknown setting(s) {', '.join(sorted(unknown))} (custom values go under [extra])")

    config = Config(root=root, raw=data)
    for name in STRING_FIELDS:
        if name in data:
            value = data[name]
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{where}: '{name}' must be non-empty text")
            setattr(config, name, value.strip())
    if not config.base_url.startswith(("http://", "https://")):
        raise ConfigError(f"{where}: base_url must start with http:// or https://, e.g. https://example.com")
    for name in ("content_dir", "static_dir", "templates_dir", "output_dir"):
        value = getattr(config, name)
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ConfigError(f"{where}: '{name}' must be a folder inside the site")
    if config.output_dir in (config.content_dir, config.static_dir, config.templates_dir, "."):
        raise ConfigError(f"{where}: output_dir must be its own folder (it gets cleaned on build)")

    for name in ("posts_per_page", "feed_limit"):
        if name in data:
            value = data[name]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1000:
                raise ConfigError(f"{where}: '{name}' must be a whole number from 1 to 1000")
            setattr(config, name, value)

    markdown = data.get("markdown", {})
    options = ("allow_html", "smart_punctuation")
    if not isinstance(markdown, dict) or set(markdown) - set(options):
        raise ConfigError(f"{where}: [markdown] supports {' and '.join(options)}")
    for name in options:
        if name in markdown:
            if not isinstance(markdown[name], bool):
                raise ConfigError(f"{where}: markdown.{name} must be true or false")
            setattr(config, name, markdown[name])

    plugins = data.get("plugins", [])
    if not isinstance(plugins, list) or not all(isinstance(p, str) and p.isidentifier() for p in plugins):
        raise ConfigError(f'{where}: plugins must be a list of plugin names, e.g. ["reading_stats"]')
    config.plugins = plugins

    extra = data.get("extra", {})
    if not isinstance(extra, dict):
        raise ConfigError(f"{where}: [extra] must be a table")
    config.extra = extra
    return config


def load_config(root: str | Path) -> Config:
    root = Path(root).resolve()
    path = root / CONFIG_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"no {CONFIG_FILE} in {root}. Create a site with: pagewright new my-site") from None
    return parse_config(text, root, str(path))
