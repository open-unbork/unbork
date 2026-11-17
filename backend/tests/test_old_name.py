"""The product's old name stays out of the tree (C-051, D-29).

unbork 2.0 reads no old name, so one that reappears is either a bug (an
operator told to set a variable nothing reads) or stale prose. A line
that names the old name on purpose carries the marker ``old-name-ok``.
Released history is exempt as a whole: the changelog, the upgrade guide,
the existing migrations and the planning notes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OLD = re.compile("lab" + "dog", re.IGNORECASE)
MARKER = "old-name-ok"
EXEMPT_PREFIXES = (
    "planning/",
    "CHANGELOG.md",
    "docs/upgrade.md",
    "backend/alembic/versions/",
)


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.split("\0") if f]


def old_name_hits(files: list[str], root: Path = REPO) -> list[str]:
    hits = []
    for rel in files:
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        if OLD.search(rel):
            hits.append(f"{rel}: path")
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if OLD.search(line) and MARKER not in line:
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    return hits


def test_no_old_name_outside_the_exemptions():
    hits = old_name_hits(_tracked_files())
    assert not hits, "The old product name is back:\n" + "\n".join(hits)


def test_guard_catches_a_new_file(tmp_path):
    (tmp_path / "new.md").write_text("Set " + "LAB" + "DOG_CONFIG here.\n")
    (tmp_path / "ok.md").write_text("LAB" + "DOG_CONFIG  # " + MARKER + "\n")
    expected = "new.md:1: Set " + "LAB" + "DOG_CONFIG here."
    assert old_name_hits(["new.md", "ok.md"], tmp_path) == [expected]
