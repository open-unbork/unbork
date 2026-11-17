"""The Claude Code program, installed on first use (S-009, D-04, D-30).

The two Claude backends ("Claude agent" and "Claude single-shot") run the
Claude Code program. unbork does not ship it: the image used to carry the
copy bundled in the ``claude-agent-sdk`` wheel, about 130 MB of a program
whose licence is not unbork's to redistribute. Instead the first time a
provider that needs it is turned on, a worker downloads that same wheel,
checks it against the sha256 ``uv.lock`` pins (copied into
``claude_code_pin.json`` by ``scripts/claude_code_pin.py``), and unpacks
only the program into the state volume::

    <state>/claude-code/<sdk version>/claude

Taking it from the pinned wheel keeps the program and the SDK that drives
it on one release, which the SDK expects. A wheel whose hash differs is
deleted unread, so nothing that was not pinned is ever executed.

A ``claude`` on ``PATH`` is honoured when nothing is installed here: that
is the route for a server without outbound access, and how package
installs have always worked. A development checkout whose SDK wheel still
holds its bundled copy uses that last.

Every failure is a :class:`ClaudeCodeUnavailable` whose text starts with
"Provider unavailable:" and names the cause. It is also written to
``last_error`` so the providers page and the Test button can show it
without retrying the download.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess  # nosec B404 - runs the verified program with a fixed argv
import tempfile
import time
import zipfile
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Literal

import httpx

from app.ai.providers.claude_cli import CLI_BINARY, DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

#: Next to ``claude-cli`` (the program's own state) under unbork's state
#: root, so a rename of that root moves both.
INSTALL_ROOT = Path(DEFAULT_CONFIG_DIR).parent / "claude-code"

PIN_FILE = Path(__file__).with_name("claude_code_pin.json")

#: The program's path inside the wheel.
WHEEL_MEMBER = "claude_agent_sdk/_bundled/claude"

#: Whole download, connect to last byte. The wheel is about 90 MB.
DOWNLOAD_TIMEOUT_SECONDS = 600

LOCK_NAME = ".lock"
ERROR_NAME = "last_error"
VERSION_NAME = "version"

#: ``platform.machine()`` spellings per pinned architecture.
_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}

State = Literal["ready", "missing", "installing", "failed"]


class ClaudeCodeUnavailable(Exception):
    """The program is not installed and could not be installed."""

    def __init__(self, cause: str):
        super().__init__(f"Provider unavailable: {cause}")


@dataclass(frozen=True)
class RuntimeStatus:
    state: State
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state, "detail": self.detail}


def load_pin() -> dict:
    with PIN_FILE.open() as fh:
        return json.load(fh)


def _version_dir(root: Path, pin: dict) -> Path:
    return root / pin["sdk_version"]


def installed_path(root: Path | None = None) -> Path | None:
    """The program this install downloaded, if it is there."""
    root = root or INSTALL_ROOT
    try:
        path = _version_dir(root, load_pin()) / CLI_BINARY
    except (OSError, ValueError, KeyError):
        return None
    return path if path.is_file() and os.access(path, os.X_OK) else None


def sdk_bundled_path() -> Path | None:
    """The copy inside an installed ``claude-agent-sdk`` wheel, if any.

    The image deletes it at build time; a development checkout or a
    package install that added the ``agent`` extra by hand still has it.
    Found without importing the SDK, which the API process never needs.
    """
    spec = find_spec("claude_agent_sdk")
    if spec is None or not spec.submodule_search_locations:
        return None
    for location in spec.submodule_search_locations:
        path = Path(location) / "_bundled" / CLI_BINARY
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def binary_path(root: Path | None = None) -> Path | None:
    """The program to run.

    In order: the one this install downloaded, ``claude`` on ``PATH`` (the
    operator's own), the SDK wheel's bundled copy where one survives.
    """
    found = installed_path(root)
    if found is not None:
        return found
    on_path = shutil.which(CLI_BINARY)
    if on_path:
        return Path(on_path)
    return sdk_bundled_path()


def _lock_held(root: Path) -> bool:
    """Whether an install is running now, in any process."""
    try:
        fd = os.open(root / LOCK_NAME, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def status(root: Path | None = None) -> RuntimeStatus:
    """What the providers page and the Test button report."""
    root = root or INSTALL_ROOT
    found = binary_path(root)
    if found is not None:
        version = ""
        with contextlib.suppress(OSError):
            version = (found.parent / VERSION_NAME).read_text().strip()
        return RuntimeStatus("ready", f"{found} ({version})" if version else str(found))
    if _lock_held(root):
        return RuntimeStatus("installing", "Downloading the Claude Code program.")
    try:
        error = (root / ERROR_NAME).read_text().strip()
    except OSError:
        error = ""
    if error:
        return RuntimeStatus("failed", error)
    return RuntimeStatus("missing", "The Claude Code program is not installed yet.")


def _pinned_wheel(pin: dict) -> tuple[str, str, str]:
    machine = platform.machine().lower()
    arch = _ARCH_ALIASES.get(machine)
    wheel = pin["wheels"].get(arch) if arch else None
    if not wheel:
        raise ClaudeCodeUnavailable(
            "there is no Claude Code build for this server's architecture "
            f"({machine or 'unknown'}). Put a `claude` program on PATH for the unbork "
            "service user instead."
        )
    return arch, wheel["url"], wheel["sha256"]


def _download(url: str, dest: Path) -> str:
    """Stream ``url`` into ``dest``; return the sha256 of what was written."""
    digest = hashlib.sha256()
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    host = httpx.URL(url).host
    try:
        # trust_env (the default) honours HTTPS_PROXY / NO_PROXY.
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes(1 << 20):
                    if time.monotonic() > deadline:
                        raise ClaudeCodeUnavailable(
                            f"downloading the Claude Code program from {host} took longer "
                            f"than {DOWNLOAD_TIMEOUT_SECONDS // 60} minutes."
                        )
                    fh.write(chunk)
                    digest.update(chunk)
    except httpx.HTTPStatusError as exc:
        raise ClaudeCodeUnavailable(
            f"downloading the Claude Code program from {host} failed with HTTP "
            f"{exc.response.status_code}."
        ) from exc
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        raise ClaudeCodeUnavailable(
            f"could not download the Claude Code program from {host} ({detail}). The server "
            "needs outbound HTTPS to it, or a `claude` program on PATH."
        ) from exc
    return digest.hexdigest()


def _check(program: Path) -> str:
    """Run ``claude --version``; return its output."""
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, program verified by hash
            [str(program), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudeCodeUnavailable(
            f"the downloaded Claude Code program does not run on this server ({exc})."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        raise ClaudeCodeUnavailable(
            f"the downloaded Claude Code program does not run on this server: {detail}"
        )
    return proc.stdout.strip()


def _install_locked(root: Path, pin: dict) -> Path:
    target_dir = _version_dir(root, pin)
    target = target_dir / CLI_BINARY
    if target.is_file() and os.access(target, os.X_OK):
        return target

    _arch, url, sha256 = _pinned_wheel(pin)
    work = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
    try:
        wheel = work / "sdk.whl"
        logger.info("claude code: downloading %s", url)
        got = _download(url, wheel)
        if got != sha256:
            wheel.unlink(missing_ok=True)
            raise ClaudeCodeUnavailable(
                "the downloaded Claude Code program does not match the checksum this "
                f"release pins (expected sha256 {sha256[:12]}…, got {got[:12]}…). It "
                "was deleted without being run."
            )
        staged_dir = work / "staged"
        staged_dir.mkdir()
        staged = staged_dir / CLI_BINARY
        try:
            with zipfile.ZipFile(wheel) as zf, zf.open(WHEEL_MEMBER) as src:
                with staged.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        except (KeyError, zipfile.BadZipFile) as exc:
            raise ClaudeCodeUnavailable(
                f"the pinned wheel holds no Claude Code program at {WHEEL_MEMBER}."
            ) from exc
        wheel.unlink()
        staged.chmod(0o755)
        version = _check(staged)
        (staged_dir / VERSION_NAME).write_text(version + "\n")

        if target_dir.exists():
            shutil.rmtree(target_dir)
        os.rename(staged_dir, target_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # Other releases' programs are dead weight once this one runs.
    for entry in root.iterdir():
        if entry.is_dir() and entry != target_dir and not entry.name.startswith("."):
            shutil.rmtree(entry, ignore_errors=True)
    logger.info("claude code: installed %s (%s)", target, version)
    return target


def install(root: Path | None = None) -> Path:
    """Install the pinned program unless it is there; return its path.

    Safe to call from several processes at once: they queue on an
    exclusive lock, and all but the first find it installed.
    """
    root = root or INSTALL_ROOT
    try:
        root.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(root / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise ClaudeCodeUnavailable(
            f"cannot write to {root} ({exc.strerror or exc}). Give the unbork service "
            "user a writable state volume there."
        ) from exc
    error_file = root / ERROR_NAME
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            path = _install_locked(root, load_pin())
        except ClaudeCodeUnavailable as exc:
            with contextlib.suppress(OSError):
                error_file.write_text(str(exc) + "\n")
            raise
        except OSError as exc:
            failure = ClaudeCodeUnavailable(
                f"installing the Claude Code program into {root} failed ({exc})."
            )
            with contextlib.suppress(OSError):
                error_file.write_text(str(failure) + "\n")
            raise failure from exc
        error_file.unlink(missing_ok=True)
        return path
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def ensure_installed(root: Path | None = None) -> Path:
    """The program's path, installing it first when there is none.

    For workers about to start a session: a split deployment whose worker
    has its own volume gets the program the first time it needs it.
    """
    found = binary_path(root)
    return found if found is not None else install(root)
