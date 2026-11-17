"""Write the Claude Code pin from ``uv.lock``.

unbork does not ship the Claude Code program (S-009). It downloads it on
first use from the ``claude-agent-sdk`` wheel that ``uv.lock`` pins, so the
program and the SDK that drives it are always the same release (D-30).
This script copies that wheel's URL and sha256 for each Linux architecture
out of the lock into ``app/ai/claude_code_pin.json``, which the installer
reads at run time; the lock itself is not shipped.

    python scripts/claude_code_pin.py            # rewrite the pin
    python scripts/claude_code_pin.py --check    # fail if it is stale

The Docker build runs it, and ``tests/ai/test_claude_code_install.py``
checks the committed copy against the lock.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LOCK = BACKEND / "uv.lock"
PIN = BACKEND / "app" / "ai" / "claude_code_pin.json"
PACKAGE = "claude-agent-sdk"

#: Wheel platform tag per ``platform.machine()`` family the installer knows.
ARCH_TAGS = {
    "x86_64": "manylinux_2_17_x86_64",
    "aarch64": "manylinux_2_17_aarch64",
}


def build_pin(lock_path: Path = LOCK) -> dict:
    """The pin for the ``claude-agent-sdk`` entry of a lock file."""
    with lock_path.open("rb") as fh:
        lock = tomllib.load(fh)
    entries = [p for p in lock.get("package", []) if p.get("name") == PACKAGE]
    if len(entries) != 1:
        raise SystemExit(f"{lock_path}: expected one {PACKAGE} entry, found {len(entries)}")
    entry = entries[0]
    wheels: dict[str, dict[str, str]] = {}
    for arch, tag in ARCH_TAGS.items():
        matches = [w for w in entry.get("wheels", []) if w["url"].endswith(f"-{tag}.whl")]
        if len(matches) != 1:
            raise SystemExit(f"{lock_path}: no single {tag} wheel for {PACKAGE}")
        algo, _, digest = matches[0]["hash"].partition(":")
        if algo != "sha256" or not digest:
            raise SystemExit(f"{lock_path}: {tag} wheel has no sha256 hash")
        wheels[arch] = {"url": matches[0]["url"], "sha256": digest}
    return {"sdk_version": entry["version"], "wheels": wheels}


def render(pin: dict) -> str:
    return json.dumps(pin, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--out", type=Path, default=PIN)
    parser.add_argument("--check", action="store_true", help="fail if --out differs")
    args = parser.parse_args(argv)

    text = render(build_pin(args.lock))
    if args.check:
        current = args.out.read_text() if args.out.exists() else ""
        if current != text:
            print(f"{args.out} does not match {args.lock}; run {__file__}", file=sys.stderr)
            return 1
        return 0
    args.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
