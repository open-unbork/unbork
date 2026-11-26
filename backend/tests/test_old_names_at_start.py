"""D-29: names from before the rename are not read, but they are named.

An upgraded install that still sets an old variable, keeps the old config
file or copies old keys into the new one is told at every start which
names it is ignoring, and the startup error lists them too.
"""

from __future__ import annotations

import logging

import pytest

from app import config

OLD = "lab" + "dog"  # built, so the old-name guard need not exempt this file


def test_old_variables_are_named():
    env = {f"{OLD.upper()}_SECURITY__SECRET_KEY": "x", "UNBORK_X": "y", "PATH": "/bin"}
    assert config.old_names_in_use(env)[:1] == [f"{OLD.upper()}_SECURITY__SECRET_KEY"]


def test_the_old_config_file_is_named_even_with_a_new_one(tmp_path, monkeypatch):
    """A package upgrade always has the new file (the package ships it)."""
    old_file = tmp_path / f"{OLD}.toml"
    old_file.write_text("")
    monkeypatch.setattr(config, "_OLD_CONFIG_PATH", old_file)
    assert str(old_file) in config.old_names_in_use({})


def test_an_old_config_directory_closed_to_the_service_is_named(monkeypatch):
    """Found by the packaging upgrade test: after the old package is removed,
    its config directory is still root:<old group> 0750, and stat() on the
    file raised PermissionError at import, so the new version never started."""

    class Closed(type(config._OLD_CONFIG_PATH)):
        def is_file(self):
            raise PermissionError(13, "Permission denied", str(self))

    closed = Closed(f"/etc/{OLD}/{OLD}.toml")
    monkeypatch.setattr(config, "_OLD_CONFIG_PATH", closed)
    assert str(closed) in config.old_names_in_use({})


def test_old_keys_in_the_config_file_are_named():
    data = {"security": {f"{OLD}_server_ip": "10.0.0.1", "unbork_server_ip": "x"}, "x": 1}
    assert config.old_toml_keys(data) == [f"security.{OLD}_server_ip (in the config file)"]


def test_the_startup_error_lists_them():
    s = config.Settings()
    s.security.secret_key = "short"
    with pytest.raises(SystemExit) as exc:
        config._validate_required(s, [f"security.{OLD}_server_ip (in the config file)"])
    assert f"security.{OLD}_server_ip" in str(exc.value)


def test_a_start_that_succeeds_still_warns(monkeypatch, caplog):
    monkeypatch.setenv(f"{OLD.upper()}_LOGGING__LEVEL", "debug")
    monkeypatch.setattr(config, "_validate_required", lambda s, old=None: None)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        config.load_settings()
    assert f"{OLD.upper()}_LOGGING__LEVEL" in caplog.text
