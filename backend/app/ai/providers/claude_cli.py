"""Claude Code CLI backend.

Runs the locally installed ``claude`` binary. Useful when the operator
already has the CLI authenticated on the unbork host and would rather not
manage a second API key.

**unbork owns every tool.** The CLI is invoked with its own tools
disabled, so it can reason and answer but cannot touch the host itself —
all host access goes through unbork's classified, allowlisted,
audit-logged tool layer. That is the whole point of the safety model, and
handing a second unaudited execution path to the same model would defeat
it.

Two modes, chosen by probing the installed CLI once:

- **tool mode** — ``--output-format stream-json --input-format stream-json``
  lets unbork feed tool results back over stdin across turns.
- **single-shot** — one prompt in, text out. Used when the installed CLI
  is too old to round-trip tool results. The factory marks the provider
  ``supports_tools = False`` and the loop restricts it to reports and
  verify verdicts.

**Why this backend still exists alongside ``claude_agent``.** The Agent
SDK does everything this does and more, so the two looked redundant, and
the open question was whether to retire this one. The answer is no, for a
reason that has nothing to do with capability: ``claude-agent-sdk`` is an
optional extra that the ``.deb`` / ``.rpm`` / ``.tar.gz`` artefacts do not
install, and an image built without the assistant drops it too. This
backend needs only the Claude Code program, which unbork downloads on
first use (``app.ai.claude_code``) or finds on PATH, so it is the one way
such an install can use a Claude subscription, which neither API-key
backend can do.

The verify step added in phase 5 also gives ``supports_tools = False`` a
job rather than a limitation: a verify session is toolless by design (see
:mod:`app.ai.verify`), so a single-shot backend serves it exactly, and
this is the case that motivated keeping the distinction visible in the
provider list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from app.ai.providers.base import (
    LLMProviderError,
    NormalizedMessage,
    StreamEvent,
    TextDelta,
    ToolSpec,
    TurnEnd,
    Usage,
)

logger = logging.getLogger(__name__)

CLI_BINARY = "claude"
DEFAULT_TIMEOUT_SECONDS = 300

#: Env var the CLI reads a subscription OAuth token from, as minted by
#: ``claude setup-token``. This is the variable's *name*, not a token —
#: bandit's B105 matches on the identifier ending in _TOKEN and cannot tell
#: the difference.
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"  # nosec B105 - env var name, not a secret

#: Credentials that outrank ``CLAUDE_CODE_OAUTH_TOKEN`` in the CLI's own
#: precedence order. If either is present in the environment the CLI bills
#: the API account instead of the subscription — silently, with no error
#: and no output difference. An operator who configured a subscription
#: token asked for subscription billing, so we remove these from the child
#: environment rather than let an unrelated variable override the intent.
OVERRIDING_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: The environment is only half of the precedence problem. A credentials
#: file left by an interactive ``claude login`` *also* outranks
#: ``CLAUDE_CODE_OAUTH_TOKEN`` — verified against claude 2.1.220, where a
#: stored login wins and the configured token is ignored with no warning
#: and no difference in the output. So when a token is configured unbork
#: also points the CLI at a directory unbork owns, where no interactive
#: session can leave a login behind to shadow it.
#:
#: ``/var/lib/unbork`` is already unbork's state root — listed in the
#: systemd unit's ``ReadWritePaths`` and the documented persistent-volume
#: mount for container deploys — so the CLI's state travels with the rest
#: of it and needs no extra configuration on either deployment shape.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
DEFAULT_CONFIG_DIR = "/var/lib/unbork/claude-cli"

#: Raised when the program is absent. Since S-009 no deployment ships it:
#: unbork downloads it on first use (``app.ai.claude_code``), and a server
#: that cannot reach the download puts its own copy on PATH.
NOT_FOUND_MESSAGE = (
    "The Claude Code program is not installed. unbork downloads it the first "
    "time a provider that needs it is turned on or tested, in a container and "
    "on a package install alike; the provider shows the reason when that fails. "
    "A server without outbound access can install a `claude` program on PATH "
    "for the unbork service user instead (system-wide: a per-user install under "
    "a home directory is not visible to the service). An Anthropic provider "
    "needs no program and can also run tools. See docs/ui/assistant.md."
)


def program_path() -> str:
    """The program to run: the one unbork installed, else ``claude`` on PATH.

    Falls back to the bare name when neither exists, so starting it fails
    with ``FileNotFoundError`` and the caller reports :data:`NOT_FOUND_MESSAGE`.
    """
    from app.ai.claude_code import binary_path

    found = binary_path()
    return str(found) if found is not None else CLI_BINARY


#: The program's sign-in file inside its config directory.
CREDENTIALS_FILE = ".credentials.json"


def keeps_home_sign_in(env: Mapping[str, str], config_dir: str) -> bool:
    """Whether to leave the config directory alone for an older sign-in.

    Before 2.0 a provider without a token read the program's default
    directory, ``~/.claude``, so an operator may have signed in there. Until
    there is a sign-in in ``config_dir``, pinning would hide that one; it is
    used instead (S-014). Signing in as the docs now say moves to
    ``config_dir`` for good.
    """
    if (Path(config_dir) / CREDENTIALS_FILE).is_file():
        return False
    home = env.get("HOME")
    return bool(home) and (Path(home) / ".claude" / CREDENTIALS_FILE).is_file()


def build_cli_env(
    oauth_token: str | None,
    base: dict[str, str] | None = None,
    config_dir: str = DEFAULT_CONFIG_DIR,
) -> dict[str, str]:
    """The environment for a ``claude`` subprocess.

    With a token: inject it, drop the two variables that would silently
    take precedence over it, and isolate the config directory so a stored
    login cannot do the same thing from disk. Only a provider saved by an
    earlier version still holds a token (S-014): new ones take none.

    Without one: the operator's own sign-in on the server is used. The
    config directory is pinned to unbork's state directory, unless the
    service environment already sets ``CLAUDE_CONFIG_DIR``, so a sign-in
    made as the docs describe survives a restart or a replaced container.
    The credential variables are left alone.
    """
    env = dict(os.environ if base is None else base)
    if not oauth_token:
        if not keeps_home_sign_in(env, config_dir):
            env.setdefault(CONFIG_DIR_ENV, config_dir)
        return env
    env[OAUTH_TOKEN_ENV] = oauth_token
    env[CONFIG_DIR_ENV] = config_dir
    for name in OVERRIDING_CREDENTIAL_ENV:
        env.pop(name, None)
    return env


class ClaudeCLIProvider:
    """Single-shot Claude Code CLI backend.

    ``supports_tools`` is False: the loop uses this for report generation
    and verify verdicts, where one prompt in and one answer out is the
    whole interaction.
    """

    provider_type = "claude_cli"
    supports_tools = False

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        oauth_token: str | None = None,
        config_dir: str = DEFAULT_CONFIG_DIR,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        # A one-year subscription token from ``claude setup-token``, stored
        # encrypted like every other unbork credential. None means "use
        # whatever the host is already authenticated with".
        self.oauth_token = oauth_token
        self.config_dir = config_dir

    @property
    def env(self) -> dict[str, str]:
        return build_cli_env(self.oauth_token, config_dir=self.config_dir)

    def _ensure_config_dir(self) -> None:
        """Create the isolated config directory, best effort.

        Only when the CLI's environment points at it: a ``CLAUDE_CONFIG_DIR``
        the service environment sets is the operator's to create. The CLI
        runs fine without it (a missing directory just means no persisted
        state), so a failure here must not stop a session from starting; it
        is logged and left alone.
        """
        if self.env.get(CONFIG_DIR_ENV) != self.config_dir:
            return
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not create the Claude CLI config directory %s (%s); "
                "the CLI will run without persisted state.",
                self.config_dir,
                exc,
            )

    def _argv(self, prompt: str) -> list[str]:
        argv = [program_path(), "-p", prompt]
        if self.model:
            argv += ["--model", self.model]
        # Deny the CLI its own tools — host access belongs to unbork's
        # classified tool layer, not to a second unaudited path.
        argv += ["--disallowedTools", "*"]
        return argv

    @staticmethod
    def _flatten(messages: list[NormalizedMessage]) -> str:
        """Render the conversation as one prompt.

        The CLI takes a single prompt string, so multi-turn context is
        folded into a labelled transcript.
        """
        parts: list[str] = []
        for msg in messages:
            if not msg.content:
                continue
            if msg.role == "system":
                parts.append(msg.content)
            elif msg.role == "user":
                parts.append(f"User: {msg.content}")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}")
            else:
                parts.append(f"Tool result: {msg.content}")
        return "\n\n".join(parts)

    async def stream_turn(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        if tools:
            raise LLMProviderError(
                "A Claude single-shot provider cannot run tools. Use a Claude agent, "
                "Anthropic or OpenAI-compatible provider for agentic sessions."
            )

        prompt = self._flatten(messages)
        self._ensure_config_dir()
        try:
            # exec, never shell: the prompt is model- and operator-supplied
            # text and must never reach a shell interpreter.
            proc = await asyncio.create_subprocess_exec(
                *self._argv(prompt),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
        except FileNotFoundError as exc:
            raise LLMProviderError(NOT_FOUND_MESSAGE) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise LLMProviderError(
                f"The {CLI_BINARY} CLI timed out after {self.timeout_seconds}s"
            ) from None

        if proc.returncode != 0:
            # In -p mode the CLI prints errors — auth failures included — to
            # stdout and leaves stderr empty, so stderr alone would surface
            # "exited 1:" with nothing after the colon.
            detail = (
                stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
            )
            raise LLMProviderError(f"{CLI_BINARY} exited {proc.returncode}: {detail[:500]}")

        text = stdout.decode(errors="replace").strip()
        if text:
            yield TextDelta(text)
        # The CLI reports no token usage in this mode, so cost is a floor.
        yield Usage(prompt_tokens=0, completion_tokens=0, unknown=True)
        yield TurnEnd(stop_reason="end_turn")

    async def _run(self, argv: list[str], timeout: int) -> tuple[int, str, str]:
        """Run the CLI once, returning (returncode, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except FileNotFoundError as exc:
            raise LLMProviderError(NOT_FOUND_MESSAGE) from exc
        except TimeoutError as exc:
            raise LLMProviderError(
                f"The {CLI_BINARY} CLI did not respond within {timeout}s"
            ) from exc
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def test_connection(self) -> str:
        """Prove the CLI is present *and* that the credential works.

        This used to run ``--version`` alone, which authenticates against
        nothing: a provider holding a completely invalid token still tested
        green, and the first sign of trouble was a failed session. A test
        that cannot fail for the most likely reason is worse than no test,
        because it actively vouches for the thing that is broken.

        So it also sends the smallest possible real prompt. That costs one
        trivial round-trip against the account, which is the same bargain
        every other provider's test already makes.
        """
        from app.ai.claude_code import binary_path

        found = binary_path()
        if found is None:
            raise LLMProviderError(NOT_FOUND_MESSAGE)
        path = str(found)
        self._ensure_config_dir()

        code, version, stderr = await self._run([path, "--version"], timeout=30)
        if code != 0:
            raise LLMProviderError(stderr[:300] or "CLI error")

        code, out, stderr = await self._run(self._argv("Reply with the single word: ok"), 60)
        if code != 0:
            # -p mode reports auth failures on stdout with stderr empty.
            detail = stderr or out
            raise LLMProviderError(
                f"{CLI_BINARY} {version} is installed, but authentication failed: {detail[:300]}"
            )
        return f"Found {path} ({version}); authenticated; single-shot mode, no tool calls"


async def cli_supports_stream_json() -> bool:
    """Probe whether the installed CLI can round-trip tool results.

    Used by the factory to decide between tool mode and single-shot. The
    check is deliberately cheap and fail-safe: anything unexpected means
    single-shot, which always works.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            program_path(),
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (FileNotFoundError, TimeoutError, OSError):
        return False
    if proc.returncode != 0:
        return False
    help_text = stdout.decode(errors="replace")
    return "--input-format" in help_text and "stream-json" in help_text


def parse_stream_json_line(line: str) -> dict | None:
    """Parse one stream-json envelope, returning None for noise.

    Kept separate from the provider so the tool-mode transport can be
    built and tested against recorded CLI output.
    """
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
