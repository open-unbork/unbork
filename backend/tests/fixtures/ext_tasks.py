"""A stand-in extension's task module (S-015, ``tests/test_extension_tasks.py``).

Not imported by anything until a test registers it through
``app.tasks.extensions.register_task_module``.
"""

from app.tasks import celery_app


@celery_app.task
def ping() -> str:
    return "pong"
