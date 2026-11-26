"""S-015: an extension's own Celery tasks and periodic jobs.

A stand-in extension (``register`` below, loaded through the real plugin
loader) adds ``tests.fixtures.ext_tasks`` on ``long_running`` and one
periodic job. The rules pinned here: its tasks are imported with the core
modules and routed where it asked, never to ``orchestrator`` or to a queue
nobody consumes; beat writes its entry once and removes the entries of an
extension that is gone; a broken extension stops neither process.
"""

from __future__ import annotations

import logging
import sys
from importlib.metadata import EntryPoint

import pytest
from fastapi import FastAPI

from app.auth.permissions import PLUGIN_API
from app.celery_manager import CeleryManager
from app.plugins import is_compatible, load_plugins
from app.tasks import celery_app
from app.tasks.beat_registry import register_all
from app.tasks.extensions import (
    EXTENSION_QUEUES,
    periodic_tasks,
    register_periodic_task,
    register_task_module,
    reset_extension_tasks,
)

MODULE = "tests.fixtures.ext_tasks"
TASK = f"{MODULE}.ping"

_registered: list[str] = []


def register(app):
    _registered.append("register")
    register_task_module(MODULE, queue="long_running")
    register_periodic_task("stand-in-tick", TASK, 30)


register.requires_plugin_api = "1.5"


def register_broken(app):
    raise RuntimeError("boom")


register_broken.requires_plugin_api = "1.5"


def _ep(name: str, attr: str) -> EntryPoint:
    return EntryPoint(name=name, value=f"tests.test_extension_tasks:{attr}", group="unbork.plugins")


@pytest.fixture(autouse=True)
def _clean():
    import app.plugins as plugins

    before_include = list(celery_app.conf.include)
    before_routes = dict(celery_app.conf.task_routes)
    reset_extension_tasks()
    _registered.clear()
    saved_once = plugins._loaded_once
    plugins._loaded_once = None
    yield
    reset_extension_tasks()
    plugins._loaded_once = saved_once
    celery_app.tasks.pop(TASK, None)
    sys.modules.pop(MODULE, None)
    assert list(celery_app.conf.include) == before_include
    assert dict(celery_app.conf.task_routes) == before_routes


def _route(name: str) -> str:
    return celery_app.amqp.router.route({}, name)["queue"].name


# ---------------------------------------------------------------------------
# Task modules and routing
# ---------------------------------------------------------------------------


def test_a_registered_module_is_imported_and_routed():
    load_plugins(FastAPI(), [_ep("stand-in", "register")])
    celery_app.loader.import_default_modules()

    assert TASK in celery_app.tasks
    assert _route(TASK) == "long_running"
    # Core routing is unchanged.
    assert _route("app.tasks.action_orchestrator.run_action") == "orchestrator"


def test_the_routing_contract_holds_with_the_stand_in_registered():
    load_plugins(FastAPI(), [_ep("stand-in", "register")])
    celery_app.loader.import_default_modules()

    consumed = set(CeleryManager.QUEUES)
    for name in celery_app.tasks:
        if name.startswith("celery."):
            continue
        queue = _route(name)
        assert queue in consumed, f"{name} routes to {queue}, which nobody consumes"
        if name.startswith(MODULE):
            assert queue != "orchestrator"


@pytest.mark.parametrize("queue", ["orchestrator", "nobody-consumes-this"])
def test_the_orchestrator_and_unknown_queues_are_refused(queue):
    with pytest.raises(ValueError):
        register_task_module("x.tasks", queue=queue)
    assert "orchestrator" not in EXTENSION_QUEUES


@pytest.mark.parametrize("module", ["app.tasks.sneaky", "app", ""])
def test_a_core_module_is_refused(module):
    with pytest.raises(ValueError):
        register_task_module(module)


def test_registering_twice_is_a_no_op_and_a_different_queue_is_refused():
    register_task_module("x.tasks", queue="drift")
    register_task_module("x.tasks", queue="drift")
    assert celery_app.conf.include.count("x.tasks") == 1
    with pytest.raises(ValueError):
        register_task_module("x.tasks", queue="default")


# ---------------------------------------------------------------------------
# Periodic tasks
# ---------------------------------------------------------------------------


def test_an_interval_under_ten_seconds_is_refused():
    with pytest.raises(ValueError):
        register_periodic_task("fast", TASK, 5)


def test_a_name_registered_twice_differently_is_refused():
    register_periodic_task("tick", TASK, 30)
    register_periodic_task("tick", TASK, 30)
    with pytest.raises(ValueError):
        register_periodic_task("tick", TASK, 60)
    assert [p.entry_name for p in periodic_tasks()] == ["ext-tick"]


@pytest.fixture
def redbeat(monkeypatch):
    """RedBeat entries in a dict, keyed like Redis."""
    stored: dict = {}
    saved: list[str] = []
    deleted: list[str] = []

    class _Entry:
        def __init__(self, name=None, task=None, schedule=None, app=None, **kw):
            self.name, self.task, self.schedule = name, task, schedule

        @classmethod
        def from_key(cls, key, app=None):
            if key not in stored:
                raise KeyError(key)
            return stored[key]

        def save(self):
            saved.append(self.name)
            stored[f"redbeat:{self.name}"] = self

        def delete(self):
            deleted.append(self.name)
            stored.pop(f"redbeat:{self.name}", None)

    monkeypatch.setattr("redbeat.RedBeatSchedulerEntry", _Entry)
    monkeypatch.setattr(
        "app.tasks.beat_registry._stored_extension_entries",
        lambda app: sorted(k[len("redbeat:") :] for k in stored if k.startswith("redbeat:ext-")),
    )
    return stored, saved, deleted, _Entry


def test_beat_writes_the_entry_once(redbeat):
    stored, saved, _, _ = redbeat
    load_plugins(FastAPI(), [_ep("stand-in", "register")])

    results = register_all(celery_app)
    assert results["extension:stand-in-tick"] == "ok"
    assert "ext-stand-in-tick" in saved
    assert stored["redbeat:ext-stand-in-tick"].task == TASK

    saved.clear()
    register_all(celery_app)
    assert "ext-stand-in-tick" not in saved


def test_beat_removes_an_entry_nothing_registers_and_never_a_core_one(redbeat):
    stored, _, deleted, entry = redbeat
    stored["redbeat:ext-old"] = entry(name="ext-old", task="gone.tasks.tick")
    stored["redbeat:audit-prune"] = entry(name="audit-prune", task="app.tasks.x")

    register_all(celery_app)

    assert deleted == ["ext-old"]
    assert "redbeat:audit-prune" in stored


def test_no_extension_adds_no_result_key(redbeat):
    results = register_all(celery_app)
    assert not any(k.startswith("extension:") for k in results)


def test_listing_stored_entries_asks_redis_for_ext_keys_only(monkeypatch):
    from app.tasks import beat_registry

    class _Redis:
        def __init__(self):
            self.pattern = None

        def scan_iter(self, match):
            self.pattern = match
            return iter([b"redbeat:ext-a", "redbeat:ext-b"])

    r = _Redis()
    monkeypatch.setattr("redbeat.schedulers.get_redis", lambda app: r)
    assert beat_registry._stored_extension_entries(celery_app) == ["ext-a", "ext-b"]
    assert r.pattern == "redbeat:ext-*"


# ---------------------------------------------------------------------------
# Loading in the Celery processes
# ---------------------------------------------------------------------------


def test_load_plugins_once_registers_once(monkeypatch):
    from app import plugins

    monkeypatch.setattr(plugins, "entry_points", lambda group: [_ep("stand-in", "register")])
    first = plugins.load_plugins_once()
    second = plugins.load_plugins_once()
    assert first is second
    assert [p.name for p in first] == ["stand-in"]
    assert _registered == ["register"]


def test_a_broken_extension_does_not_stop_the_task_imports(monkeypatch, caplog):
    from app import plugins

    monkeypatch.setattr(plugins, "entry_points", lambda group: [_ep("broken", "register_broken")])
    with caplog.at_level(logging.ERROR):
        celery_app.loader.import_default_modules()
    assert "register() failed" in caplog.text


def test_a_loader_that_raises_does_not_stop_the_task_imports(monkeypatch, caplog):
    from app import plugins

    def explode(app=None):
        raise RuntimeError("worse")

    monkeypatch.setattr(plugins, "load_plugins_once", explode)
    with caplog.at_level(logging.ERROR):
        celery_app.loader.import_default_modules()
    assert "loading extensions before the task modules failed" in caplog.text


def test_the_contract_version():
    major, minor = (int(x) for x in PLUGIN_API.split("."))
    assert (major, minor) >= (1, 5)
    assert is_compatible("1.2")
