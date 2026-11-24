"""Write THIRD-PARTY-NOTICES for what an unbork build ships (S-010).

Standard library only, so it runs in the image's final stage and against a
package's staged venv with nothing extra installed.

Three origins:

* **Python**: every distribution installed in the interpreter running
  this script (``importlib.metadata``), except unbork itself. Licence from
  ``License-Expression``, else ``License``, else the trove classifiers, else
  ``packaging/licence-overrides.toml``; texts from the files the
  distribution ships (``LICENSE*``, ``COPYING*``, ``NOTICE*``).
* **Frontend**: the JSON written by ``frontend/scripts/licences.mjs`` for
  the npm packages the production build bundles.
* **Debian** (``--dpkg-status``): installed packages, with their
  ``/usr/share/doc/<package>/copyright`` text.

The licence policy applies to Python and frontend components, which run in
unbork's own process or ship inside its pages: each must be permissive,
weak copyleft or GPL-compatible copyleft, else the build fails unless the
overrides file says otherwise. Debian packages are separate programs in
the same image (aggregation), so they are listed with their copyright file
but not judged.

    python third_party_notices.py --frontend fe.json \\
        --dpkg-status /var/lib/dpkg/status --out THIRD-PARTY-NOTICES
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# The checkout's overrides when run from the repository; a build copies the
# script elsewhere (/tmp in the image) and passes --overrides.
_HERE = Path(__file__).resolve()
DEFAULT_OVERRIDES = (
    _HERE.parents[2] / "packaging" / "licence-overrides.toml"
    if len(_HERE.parents) > 2
    else Path("licence-overrides.toml")
)

#: unbork's own distribution names: not third-party.
OWN = {"unbork-backend"}

PERMISSIVE = {
    "0BSD",
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "CC-BY-4.0",
    "CC0-1.0",
    "HPND",
    "ISC",
    "MIT",
    "MIT-0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}
WEAK_COPYLEFT = {
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MPL-2.0",
}
GPL_COMPATIBLE_COPYLEFT = {
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
}

#: Recognised, but not acceptable on their own (an OR may offer another).
KNOWN_OTHER = {"EPL-1.0", "EPL-2.0", "GPL-2.0-only", "SSPL-1.0", "BUSL-1.1"}

#: Category rank: lower is better. "other" fails the policy.
RANK = {"permissive": 0, "weak copyleft": 1, "GPL-compatible copyleft": 2, "other": 3}

#: Free-text ``License`` values and trove classifiers, as SPDX ids.
ALIASES = {
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache software license, version 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "bsd": "BSD",
    "bsd license": "BSD",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "mit license": "MIT",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "python software foundation license": "PSF-2.0",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0-or-later",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
}

_TEXT_NAME = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/]*$", re.I)
_SPDX_TOKEN = re.compile(r"[A-Za-z0-9.+-]+")


@dataclass
class Component:
    origin: str
    name: str
    version: str
    licence: str
    texts: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def category(self) -> str:
        return categorise(self.licence)


def _spdx(value: str) -> str | None:
    value = value.strip()
    if not value or value.upper() == "UNKNOWN":
        return None
    alias = ALIASES.get(value.lower())
    if alias:
        return alias
    tokens = [t for t in _SPDX_TOKEN.findall(value) if t not in ("AND", "OR", "WITH")]
    known = PERMISSIVE | WEAK_COPYLEFT | GPL_COMPATIBLE_COPYLEFT | KNOWN_OTHER
    if tokens and all(t in known for t in tokens):
        return value
    return None


def _category_of_id(spdx_id: str) -> str:
    if spdx_id in PERMISSIVE:
        return "permissive"
    if spdx_id in WEAK_COPYLEFT:
        return "weak copyleft"
    if spdx_id in GPL_COMPATIBLE_COPYLEFT:
        return "GPL-compatible copyleft"
    return "other"


def categorise(expression: str) -> str:
    """The category of an SPDX expression: OR takes the best, AND the worst."""
    expression = expression.strip().strip("()")
    if " OR " in expression:
        return min((categorise(p) for p in expression.split(" OR ")), key=RANK.__getitem__)
    if " AND " in expression:
        return max((categorise(p) for p in expression.split(" AND ")), key=RANK.__getitem__)
    return _category_of_id(expression.strip().strip("()"))


def load_overrides(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return {k.lower(): v for k, v in data.get("python", {}).items()} | {
        f"npm:{k.lower()}": v for k, v in data.get("npm", {}).items()
    }


def _python_licence(dist: md.Distribution) -> str | None:
    meta = dist.metadata
    for raw in (meta.get("License-Expression"), meta.get("License")):
        if raw and "\n" not in raw.strip() and len(raw) < 80:
            found = _spdx(raw)
            if found:
                return found
    ids = []
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License :: OSI Approved :: "):
            found = _spdx(classifier.rsplit(" :: ", 1)[-1])
            if found and found not in ids:
                ids.append(found)
    if ids:
        # Several classifiers mean the user may pick (dual licensing).
        return " OR ".join(ids)
    return None


def _site_dirs() -> list[Path]:
    import site
    import sysconfig

    dirs = {Path(sysconfig.get_paths()["purelib"]), Path(sysconfig.get_paths()["platlib"])}
    dirs.update(Path(p) for p in site.getsitepackages())
    return [d.resolve() for d in dirs]


def python_components(
    overrides: dict[str, dict], site: Path | None = None
) -> tuple[list[Component], list[str]]:
    """Installed distributions: this interpreter's, or those in ``site``."""
    sites = [site.resolve()] if site else _site_dirs()
    dists = md.distributions(path=[str(site)]) if site else md.distributions()
    seen: set[str] = set()
    out: list[Component] = []
    problems: list[str] = []
    for dist in dists:
        name = dist.metadata["Name"]
        key = (name or "").lower()
        if not name or key in seen or key in OWN:
            continue
        location = Path(str(dist.locate_file(""))).resolve()
        if not any(location == s or s in location.parents for s in sites):
            continue  # an egg-info in the working directory, not installed
        seen.add(key)
        override = overrides.get(key, {})
        licence = override.get("licence") or _python_licence(dist)
        texts = []
        for f in dist.files or []:
            if _TEXT_NAME.search(str(f)):
                try:
                    texts.append(Path(str(dist.locate_file(f))).read_text(errors="replace"))
                except OSError:
                    pass
        if override.get("text"):
            texts.append(override["text"])
        if not licence:
            problems.append(f"python {name} {dist.version}: no licence found")
            licence = "UNKNOWN"
        comp = Component("python", name, dist.version, licence, texts, override.get("note", ""))
        if not texts:
            comp.note = comp.note or "the distribution ships no licence file"
        out.append(comp)
    return sorted(out, key=lambda c: c.name.lower()), problems


def frontend_components(
    path: Path, overrides: dict[str, dict]
) -> tuple[list[Component], list[str]]:
    rows = json.loads(path.read_text())
    out, problems = [], []
    for row in rows:
        override = overrides.get(f"npm:{row['name'].lower()}", {})
        licence = override.get("licence") or _spdx(row.get("license") or "")
        if not licence:
            problems.append(f"npm {row['name']} {row['version']}: licence {row.get('license')!r}")
            licence = row.get("license") or "UNKNOWN"
        out.append(Component("npm", row["name"], row["version"], licence, row.get("texts", []), ""))
    return sorted(out, key=lambda c: c.name.lower()), problems


def _dep5_licences(text: str) -> str:
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith("License:"):
            value = line.split(":", 1)[1].strip()
            if value and value not in found:
                found.append(value)
    return ", ".join(found) if found else "see copyright file"


def debian_components(status: Path, doc_root: Path) -> list[Component]:
    out = []
    for block in status.read_text(errors="replace").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line and not line[0].isspace() and ":" in line:
                k, v = line.split(":", 1)
                fields[k] = v.strip()
        if not fields.get("Package") or "installed" not in fields.get("Status", "").split():
            continue
        name = fields["Package"]
        copyright_file = doc_root / name / "copyright"
        try:
            text = copyright_file.read_text(errors="replace")
        except OSError:
            text = ""
        comp = Component(
            "debian",
            name,
            fields.get("Version", ""),
            _dep5_licences(text) if text else "see the Debian package",
            [text] if text else [],
        )
        if not text:
            comp.note = f"no {copyright_file}; see https://sources.debian.org/src/{name}/"
        out.append(comp)
    return sorted(out, key=lambda c: c.name)


def render(components: list[Component]) -> str:
    texts: dict[str, str] = {}
    refs: dict[int, list[str]] = {}
    for i, comp in enumerate(components):
        for text in comp.texts:
            digest = hashlib.sha256(text.strip().encode()).hexdigest()[:12]
            texts.setdefault(digest, text.strip())
            refs.setdefault(i, []).append(digest)

    lines = [
        "THIRD-PARTY NOTICES",
        "",
        "unbork is free software under the GNU Affero General Public License,",
        "version 3 or later (LICENSE, next to this file). It is built from and",
        "ships with the third-party components below, each under its own",
        "licence. Where to get the source of each: SOURCE-OFFER.md.",
        "",
    ]
    titles = {
        "python": "Python packages",
        "npm": "Frontend packages (bundled into the web UI)",
        "debian": "Debian packages (separate programs in the image)",
    }
    for origin in ("python", "npm", "debian"):
        rows = [(i, c) for i, c in enumerate(components) if c.origin == origin]
        if not rows:
            continue
        lines += [f"== {titles[origin]}: {len(rows)} ==", ""]
        width = max(len(c.name) for _, c in rows)
        vwidth = max(len(c.version) for _, c in rows)
        for i, c in rows:
            ref = ", ".join(f"[{d}]" for d in refs.get(i, []))
            note = f"  ({c.note})" if c.note else ""
            lines.append(
                f"{c.name:<{width}}  {c.version:<{vwidth}}  {c.licence}  {ref}{note}".rstrip()
            )
        lines.append("")
    lines += ["== Licence texts ==", ""]
    for digest, text in texts.items():
        lines += [f"---- [{digest}] " + "-" * 50, "", text, ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write THIRD-PARTY-NOTICES.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, help="JSON from frontend/scripts/licences.mjs")
    parser.add_argument("--dpkg-status", type=Path, help="e.g. /var/lib/dpkg/status")
    parser.add_argument("--doc-root", type=Path, default=Path("/usr/share/doc"))
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument(
        "--site",
        type=Path,
        help="read Python packages from this site-packages (a staged venv) instead",
    )
    args = parser.parse_args(argv)

    overrides = load_overrides(args.overrides)
    components, problems = python_components(overrides, args.site)
    if args.frontend:
        npm, npm_problems = frontend_components(args.frontend, overrides)
        components += npm
        problems += npm_problems
    for comp in components:
        if comp.origin in ("python", "npm") and comp.licence != "UNKNOWN":
            if comp.category == "other":
                problems.append(
                    f"{comp.origin} {comp.name} {comp.version}: {comp.licence} is not known "
                    "to be compatible with AGPL-3.0-or-later"
                )
    if args.dpkg_status:
        components += debian_components(args.dpkg_status, args.doc_root)

    if problems:
        print("third_party_notices: refusing to write notices:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            f"Check each project's licence and add it to {args.overrides} "
            "(see CONTRIBUTING.md, Dependencies).",
            file=sys.stderr,
        )
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(components))
    counts = {o: sum(c.origin == o for c in components) for o in ("python", "npm", "debian")}
    print(f"third_party_notices: wrote {args.out} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
