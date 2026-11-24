"""Optional parts of unbork that a server can leave out.

Read from the config file at process start (``[assistant] enabled``,
``UNBORK_ASSISTANT__ENABLED``), never from the database: leaving a
subsystem out needs server access (C-026).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

#: Written by ``docker build --build-arg WITH_ASSISTANT=0``. Such an image
#: has neither the assistant's pages nor the Agent SDK, so no config file
#: can turn it on.
BUILT_WITHOUT_MARKER = Path(__file__).with_name("_built_without_assistant")


@cache
def built_with_assistant() -> bool:
    return not BUILT_WITHOUT_MARKER.exists()


def assistant_enabled() -> bool:
    """Whether the AI assistant is loaded in this process."""
    from app.config import settings

    return settings.assistant.enabled and built_with_assistant()


def features() -> dict[str, bool]:
    """What ``GET /api/version`` reports, before sign-in."""
    return {"assistant": assistant_enabled()}


#: Permissions that only mean something with the assistant loaded. The
#: catalog keeps them (an extension naming them still loads, and
#: ``PLUGIN_API`` does not move); ``GET /api/users/me`` stops reporting
#: them when the assistant is left out.
ASSISTANT_PERMISSIONS = frozenset({"ai.use", "ai.approve"})


def shown_permissions(perms: frozenset[str] | set[str]) -> list[str]:
    """``perms`` sorted, without the assistant's when it is not loaded."""
    if assistant_enabled():
        return sorted(perms)
    return sorted(p for p in perms if p not in ASSISTANT_PERMISSIONS)
