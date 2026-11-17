"""S-009: the Claude Code program is downloaded on first use, not shipped.

The installer is exercised against a local HTTP server that serves a tiny
fake wheel whose "program" is a shell script. Nothing here reaches PyPI.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import stat
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from app.ai import claude_code
from app.ai.claude_code import ClaudeCodeUnavailable

BACKEND = Path(__file__).resolve().parents[2]


def _fake_wheel(marker: Path) -> bytes:
    """A wheel whose program records that it ran and prints a version."""
    script = f'#!/bin/sh\necho ran >> "{marker}"\necho "9.9.9 (Claude Code)"\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(claude_code.WHEEL_MEMBER)
        info.external_attr = 0o755 << 16
        zf.writestr(info, script)
        zf.writestr("claude_agent_sdk/__init__.py", "")
    return buf.getvalue()


class _Server:
    def __init__(self, body: bytes):
        self.body = body
        self.requests = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's name
                server.requests += 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/sdk.whl"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No program anywhere: not installed, not on PATH, not in the SDK."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(claude_code, "sdk_bundled_path", lambda: None)
    monkeypatch.setattr(claude_code.platform, "machine", lambda: "x86_64")
    root = tmp_path / "claude-code"
    monkeypatch.setattr(claude_code, "INSTALL_ROOT", root)
    return root


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "ran"


@pytest.fixture
def server(marker):
    srv = _Server(_fake_wheel(marker))
    yield srv
    srv.close()


def _pin(url: str, sha256: str, version: str = "0.0.1") -> dict:
    return {"sdk_version": version, "wheels": {"x86_64": {"url": url, "sha256": sha256}}}


@pytest.fixture
def pinned(server, monkeypatch):
    pin = _pin(server.url, hashlib.sha256(server.body).hexdigest())
    monkeypatch.setattr(claude_code, "load_pin", lambda: pin)
    return pin


def test_install_puts_the_program_on_the_volume(isolated, server, pinned, marker):
    assert claude_code.status().state == "missing"
    assert claude_code.binary_path() is None

    path = claude_code.install()

    assert path == isolated / "0.0.1" / "claude"
    assert path.stat().st_mode & stat.S_IXUSR
    assert claude_code.binary_path() == path
    ready = claude_code.status()
    assert ready.state == "ready"
    assert "9.9.9 (Claude Code)" in ready.detail
    assert not (isolated / claude_code.ERROR_NAME).exists()
    # Only the program is kept, not the wheel or a staging directory.
    assert sorted(p.name for p in isolated.iterdir()) == [".lock", "0.0.1"]


def test_a_second_install_does_not_download_again(isolated, server, pinned):
    claude_code.install()
    claude_code.install()
    claude_code.ensure_installed()
    assert server.requests == 1


def test_a_wheel_with_the_wrong_hash_is_never_run(isolated, server, monkeypatch, marker):
    monkeypatch.setattr(claude_code, "load_pin", lambda: _pin(server.url, "0" * 64))

    with pytest.raises(ClaudeCodeUnavailable) as err:
        claude_code.install()

    assert str(err.value).startswith("Provider unavailable:")
    assert "checksum" in str(err.value)
    assert not marker.exists(), "the unverified program was executed"
    assert claude_code.binary_path() is None
    failed = claude_code.status()
    assert failed.state == "failed"
    assert "checksum" in failed.detail
    assert sorted(p.name for p in isolated.iterdir()) == [".lock", "last_error"]


def test_a_network_failure_names_the_download(isolated, monkeypatch):
    # A port nothing listens on: bind, read the port, close.
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(
        claude_code, "load_pin", lambda: _pin(f"http://127.0.0.1:{port}/sdk.whl", "0" * 64)
    )

    with pytest.raises(ClaudeCodeUnavailable) as err:
        claude_code.install()

    assert "could not download the Claude Code program from 127.0.0.1" in str(err.value)
    assert claude_code.status().state == "failed"
    assert "outbound HTTPS" in claude_code.status().detail


def test_a_successful_install_clears_the_last_error(isolated, server, pinned):
    isolated.mkdir()
    (isolated / claude_code.ERROR_NAME).write_text("Provider unavailable: earlier\n")
    assert claude_code.status().state == "failed"
    claude_code.install()
    assert claude_code.status().state == "ready"


def test_concurrent_installs_download_once(isolated, server, pinned):
    results: list[Path | Exception] = []

    def run():
        try:
            results.append(claude_code.install())
        except Exception as exc:  # pragma: no cover - reported below
            results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert server.requests == 1
    assert len(results) == 4
    assert all(r == isolated / "0.0.1" / "claude" for r in results), results


def test_an_unsupported_architecture_is_refused(isolated, server, pinned, monkeypatch):
    monkeypatch.setattr(claude_code.platform, "machine", lambda: "riscv64")
    with pytest.raises(ClaudeCodeUnavailable) as err:
        claude_code.install()
    assert "architecture (riscv64)" in str(err.value)
    assert server.requests == 0


def test_an_unwritable_volume_is_named(tmp_path, monkeypatch, pinned):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    with pytest.raises(ClaudeCodeUnavailable) as err:
        claude_code.install(blocker / "claude-code")
    assert "writable state volume" in str(err.value)


def test_the_operators_program_on_path_is_used_without_a_download(
    isolated, server, pinned, tmp_path, monkeypatch
):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    program = bindir / "claude"
    program.write_text("#!/bin/sh\necho 1.0.0\n")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))

    assert claude_code.binary_path() == program
    assert claude_code.ensure_installed() == program
    assert claude_code.status().state == "ready"
    assert server.requests == 0


def test_an_older_release_is_removed(isolated, server, pinned):
    old = isolated / "0.0.0"
    old.mkdir(parents=True)
    (old / "claude").write_text("old")
    claude_code.install()
    assert not old.exists()


def test_status_reports_an_install_in_progress(isolated):
    import fcntl

    isolated.mkdir()
    fd = os.open(isolated / claude_code.LOCK_NAME, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert claude_code.status().state == "installing"
    finally:
        os.close(fd)
    assert claude_code.status().state == "missing"


def test_the_committed_pin_matches_uv_lock():
    spec = importlib.util.spec_from_file_location(
        "claude_code_pin", BACKEND / "scripts" / "claude_code_pin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build_pin(BACKEND / "uv.lock") == claude_code.load_pin(), (
        "app/ai/claude_code_pin.json is stale: run backend/scripts/claude_code_pin.py"
    )


def test_the_pin_covers_both_linux_architectures():
    pin = claude_code.load_pin()
    assert set(pin["wheels"]) == {"x86_64", "aarch64"}
    for wheel in pin["wheels"].values():
        assert wheel["url"].startswith("https://files.pythonhosted.org/")
        assert len(wheel["sha256"]) == 64


def test_the_worker_task_reports_the_failure(isolated, monkeypatch):
    from app.tasks.ai_runtime import install_claude_code

    monkeypatch.setattr(claude_code.platform, "machine", lambda: "sparc")
    monkeypatch.setattr(claude_code, "load_pin", lambda: _pin("http://127.0.0.1:1/x", "0" * 64))
    result = install_claude_code.run()
    assert result["ok"] is False
    assert "architecture" in result["error"]


def test_the_task_runs_on_long_running():
    from app.tasks import celery_app

    routes = celery_app.conf.task_routes
    assert routes["app.tasks.ai_runtime.*"] == {"queue": "long_running"}
    assert "app.tasks.ai_runtime" in celery_app.conf.include


# --- the API ---------------------------------------------------------------


def _body(provider_type: str) -> dict:
    return {
        "name": f"p-{uuid.uuid4().hex[:8]}",
        "provider_type": provider_type,
        "model": "claude-opus-5",
    }


class TestTheProvidersApi:
    async def test_creating_a_program_backed_provider_queues_the_install(
        self, superuser_client, isolated
    ):
        with patch("app.tasks.celery_app.send_task") as send:
            resp = await superuser_client.post("/api/ai/providers", json=_body("claude_agent"))
        assert resp.status_code == 201, resp.text
        assert resp.json()["runtime"]["state"] == "missing"
        send.assert_called_once()
        assert send.call_args.args[0] == "app.tasks.ai_runtime.install_claude_code"

    async def test_an_api_key_provider_has_no_runtime(self, superuser_client, isolated):
        body = _body("openai_compat") | {"base_url": "http://localhost:11434/v1"}
        with patch("app.tasks.celery_app.send_task") as send:
            resp = await superuser_client.post("/api/ai/providers", json=body)
        assert resp.status_code == 201, resp.text
        assert resp.json()["runtime"] is None
        send.assert_not_called()

    async def test_a_ready_program_is_not_installed_again(
        self, superuser_client, isolated, tmp_path, monkeypatch
    ):
        program = tmp_path / "empty-path" / "claude"
        program.write_text("#!/bin/sh\n")
        program.chmod(0o755)
        with patch("app.tasks.celery_app.send_task") as send:
            resp = await superuser_client.post("/api/ai/providers", json=_body("claude_cli"))
        assert resp.json()["runtime"]["state"] == "ready"
        send.assert_not_called()

    async def test_test_says_it_is_installing(self, superuser_client, isolated):
        with patch("app.tasks.celery_app.send_task"):
            created = await superuser_client.post("/api/ai/providers", json=_body("claude_cli"))
            tested = await superuser_client.post(f"/api/ai/providers/{created.json()['id']}/test")
        assert tested.json() == {
            "ok": False,
            "message": "Installing the Claude Code program; try again in a few minutes.",
        }

    async def test_test_returns_the_install_failure_and_retries(self, superuser_client, isolated):
        isolated.mkdir()
        (isolated / claude_code.ERROR_NAME).write_text(
            "Provider unavailable: could not download the Claude Code program from x.\n"
        )
        with patch("app.tasks.celery_app.send_task") as send:
            created = await superuser_client.post("/api/ai/providers", json=_body("claude_agent"))
            assert created.json()["runtime"]["state"] == "failed"
            tested = await superuser_client.post(f"/api/ai/providers/{created.json()['id']}/test")
        body = tested.json()
        assert body["ok"] is False
        assert body["message"].startswith("Provider unavailable: could not download")
        assert send.call_count == 2

    async def test_a_broker_outage_does_not_fail_the_save(self, superuser_client, isolated):
        with patch("app.tasks.celery_app.send_task", side_effect=OSError("no broker")):
            resp = await superuser_client.post("/api/ai/providers", json=_body("claude_agent"))
        assert resp.status_code == 201


def test_a_session_worker_installs_once_or_fails_with_the_reason(isolated, monkeypatch):
    from app.ai.service import AIDisabledError
    from app.tasks.ai_task import _ensure_program

    monkeypatch.setattr(claude_code.platform, "machine", lambda: "mips")
    monkeypatch.setattr(claude_code, "load_pin", lambda: _pin("http://127.0.0.1:1/x", "0" * 64))
    with pytest.raises(AIDisabledError) as err:
        _ensure_program()
    assert str(err.value).startswith("Provider unavailable:")
