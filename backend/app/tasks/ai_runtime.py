"""Install the Claude Code program in the background (S-009).

The API enqueues this when a provider that needs the program is created
or turned on, and when its Test button finds the program missing. It runs
on ``long_running`` because the download is tens of megabytes; the API
never downloads it in a request.
"""

from __future__ import annotations

import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ai_runtime.install_claude_code")
def install_claude_code() -> dict:
    """Install the pinned program; the error is kept for the providers page."""
    from app.ai import claude_code

    try:
        path = claude_code.install()
    except claude_code.ClaudeCodeUnavailable as exc:
        # install() has written the text to last_error, which the
        # providers page reads. A retry is the operator's call.
        logger.warning("claude code install failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": str(path)}
