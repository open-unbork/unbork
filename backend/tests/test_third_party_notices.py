"""S-010: the third-party notices are generated, complete and refuse the unknown."""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "third_party_notices.py"


@pytest.fixture(scope="module")
def tpn():
    spec = importlib.util.spec_from_file_location("third_party_notices", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


def _fake_dist(site: Path, name: str, version: str, licence: str | None, text: str | None):
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    meta = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    files = ["METADATA"]
    if licence:
        meta.append(f"License-Expression: {licence}")
    if text:
        (info / "licenses").mkdir()
        (info / "licenses" / "LICENSE").write_text(text)
        meta.append("License-File: LICENSE")
        files.append("licenses/LICENSE")
    (info / "METADATA").write_text("\n".join(meta) + "\n")
    (info / "RECORD").write_text("".join(f"{name}-{version}.dist-info/{f},,\n" for f in files))


class TestCategories:
    @pytest.mark.parametrize(
        ("expr", "category"),
        [
            ("MIT", "permissive"),
            ("Apache-2.0", "permissive"),
            ("LGPL-3.0-or-later", "weak copyleft"),
            ("GPL-3.0-or-later", "GPL-compatible copyleft"),
            ("MIT OR GPL-2.0-only", "permissive"),
            ("MIT AND GPL-3.0-or-later", "GPL-compatible copyleft"),
            ("SSPL-1.0", "other"),
        ],
    )
    def test_categorise(self, tpn, expr, category):
        assert tpn.categorise(expr) == category


class TestThisEnvironment:
    def test_it_lists_every_installed_package(self, tpn, tmp_path):
        out = tmp_path / "NOTICES"
        assert tpn.main(["--out", str(out)]) == 0
        notices = out.read_text()
        lines = notices.splitlines()
        assert any(line.startswith("ansible-core ") and "GPL-3.0" in line for line in lines)
        assert any(line.startswith("fastapi ") for line in lines)
        assert "GNU GENERAL PUBLIC LICENSE" in notices
        # Our own package is the subject, not a third party.
        assert not any(line.startswith("unbork-backend ") for line in lines)


class TestRefusals:
    def test_a_package_without_a_licence_is_refused(self, tpn, tmp_path, capsys):
        site = tmp_path / "site"
        _fake_dist(site, "mystery", "1.0", None, None)
        out = tmp_path / "NOTICES"
        assert tpn.main(["--site", str(site), "--out", str(out)]) == 1
        assert "mystery 1.0" in capsys.readouterr().err
        assert not out.exists()

    def test_an_incompatible_licence_is_refused(self, tpn, tmp_path, capsys):
        site = tmp_path / "site"
        _fake_dist(site, "closed", "2.0", "SSPL-1.0", "Server Side Public License")
        assert tpn.main(["--site", str(site), "--out", str(tmp_path / "N")]) == 1
        assert "not known to be compatible" in capsys.readouterr().err

    def test_an_override_settles_it(self, tpn, tmp_path):
        site = tmp_path / "site"
        _fake_dist(site, "mystery", "1.0", None, None)
        overrides = tmp_path / "overrides.toml"
        overrides.write_text(
            textwrap.dedent(
                """\
                [python.mystery]
                licence = "BSD-3-Clause"
                note = "checked upstream"
                """
            )
        )
        out = tmp_path / "N"
        args = ["--site", str(site), "--overrides", str(overrides), "--out", str(out)]
        assert tpn.main(args) == 0
        assert any(
            line.startswith("mystery ") and "BSD-3-Clause" in line
            for line in out.read_text().splitlines()
        )

    def test_an_npm_package_without_a_licence_is_refused(self, tpn, tmp_path, capsys):
        site = tmp_path / "site"
        _fake_dist(site, "fine", "1.0", "MIT", "MIT License")
        npm = tmp_path / "npm.json"
        npm.write_text(json.dumps([{"name": "left-pad", "version": "0.0.1", "license": ""}]))
        args = ["--site", str(site), "--frontend", str(npm), "--out", str(tmp_path / "N")]
        assert tpn.main(args) == 1
        assert "left-pad" in capsys.readouterr().err


def test_the_texts_are_listed_once(tpn, tmp_path):
    site = tmp_path / "site"
    _fake_dist(site, "a", "1.0", "MIT", "MIT License\n\nsame text")
    _fake_dist(site, "b", "1.0", "MIT", "MIT License\n\nsame text")
    out = tmp_path / "N"
    assert tpn.main(["--site", str(site), "--out", str(out)]) == 0
    assert out.read_text().count("same text") == 1
