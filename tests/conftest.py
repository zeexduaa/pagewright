import shutil
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).parent.parent / "src" / "pagewright" / "scaffold" / "site"


@pytest.fixture
def site_dir(tmp_path):
    """A fresh copy of the starter site."""
    target = tmp_path / "site"
    shutil.copytree(SCAFFOLD, target)
    return target


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
