"""Extension hook: background work of an extension's own (PLUGIN_API 1.5).

An extension calls these from its ``register(app)``:

* :func:`register_task_module` adds a module of Celery tasks to the app's
  ``include`` list and routes its tasks to one queue. Workers import it
  like a core task module, so its ``@celery_app.task`` functions are known
  by name in the worker that receives them.
* :func:`register_periodic_task` asks beat to send a task on a fixed
  interval. The RedBeat entry is named ``ext-<name>``; beat writes it at
  start (:func:`app.tasks.beat_registry.register_all`) and deletes any
  ``ext-`` entry no installed extension registers any more.

Extensions load in every Celery process that needs them: the worker parent
and beat (through Celery's ``import_modules`` signal, before the task
modules are imported) and each pool child (``worker_process_init``), once
per process (:func:`app.plugins.load_plugins_once`).

The queue rule: an extension's tasks go to a queue some worker consumes,
never ``orchestrator``, which is reserved for ``run_action`` (see
``CLAUDE.md``, "Celery workers and queues"). Task bodies reach the
database the way core tasks do, with ``asyncio.run(with_task_engine(...))``
and ``app.db.task_session``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Beat entry names an extension's periodic tasks are stored under.
EXTENSION_ENTRY_PREFIX = "ext-"
#: The shortest interval an extension may ask for.
MIN_INTERVAL_SECONDS = 10


def _extension_queues() -> tuple[str, ...]:
    from app.celery_manager import CeleryManager

    return tuple(q for q in CeleryManager.QUEUES if q != "orchestrator")


#: Queues an extension's tasks may use: every consumed queue but ``orchestrator``.
EXTENSION_QUEUES: tuple[str, ...] = _extension_queues()


@dataclass(frozen=True)
class PeriodicTask:
    """One fixed-interval job an extension asked for."""

    name: str
    task: str
    every_seconds: float

    @property
    def entry_name(self) -> str:
        return EXTENSION_ENTRY_PREFIX + self.name


_task_modules: dict[str, str] = {}
_periodic: dict[str, PeriodicTask] = {}


def _app():
    from app.tasks import celery_app

    return celery_app


def _refresh_routes(app) -> None:
    """Make a route change take effect. ``flush_routes`` rebuilds the route
    table, but the router built from the old one is cached on ``amqp``."""
    app.amqp.flush_routes()
    app.amqp.__dict__.pop("router", None)


def register_task_module(module: str, *, queue: str = "default") -> None:
    """Have workers import ``module`` and route its tasks to ``queue``.

    The route matches ``<module>.*``, so a task gets it only when its name
    starts with the module path, which Celery's default naming does. A
    task with any other name goes to ``default``, which is consumed too.
    Registering the same module and queue again is a no-op.
    """
    if not isinstance(module, str) or not module or module == "app" or module.startswith("app."):
        raise ValueError(f"an extension's task module cannot be {module!r}")
    if queue not in EXTENSION_QUEUES:
        raise ValueError(
            f"queue {queue!r} is not one an extension may use; pick one of "
            + ", ".join(EXTENSION_QUEUES)
        )
    known = _task_modules.get(module)
    if known == queue:
        return
    if known is not None:
        raise ValueError(f"task module {module!r} is already registered on queue {known!r}")
    app = _app()
    include = list(app.conf.include or ())
    if module not in include:
        include.append(module)
        app.conf.include = include
    routes = app.conf.task_routes
    routes[f"{module}.*"] = {"queue": queue}
    _refresh_routes(app)
    _task_modules[module] = queue


def register_periodic_task(name: str, task: str, every_seconds: float) -> PeriodicTask:
    """Have beat send ``task`` every ``every_seconds`` (at least 10).

    The entry is stored as ``ext-<name>``. Registering the same name again
    with the same task and interval is a no-op; with anything different it
    is refused, since two extensions would otherwise overwrite each other.
    """
    if not name or not isinstance(name, str):
        raise ValueError("a periodic task needs a name")
    if not task or not isinstance(task, str):
        raise ValueError("a periodic task needs a task name")
    if every_seconds < MIN_INTERVAL_SECONDS:
        raise ValueError(
            f"periodic task {name!r}: every {every_seconds}s is under the "
            f"{MIN_INTERVAL_SECONDS}s minimum"
        )
    entry = PeriodicTask(name=name, task=task, every_seconds=every_seconds)
    known = _periodic.get(name)
    if known is not None and known != entry:
        raise ValueError(f"periodic task {name!r} is already registered as {known}")
    _periodic[name] = entry
    return entry


def periodic_tasks() -> list[PeriodicTask]:
    """Every periodic task registered in this process, by name."""
    return [_periodic[n] for n in sorted(_periodic)]


def reset_extension_tasks() -> None:
    """Forget every registration and undo its include and route entries. For tests."""
    app = _app()
    if _task_modules:
        app.conf.include = [m for m in app.conf.include if m not in _task_modules]
        routes = app.conf.task_routes
        for module in _task_modules:
            routes.pop(f"{module}.*", None)
        _refresh_routes(app)
    _task_modules.clear()
    _periodic.clear()
