"""The real client address of a request, through trusted reverse proxies.

Used by the login rate limiter, audit IPs and the alert webhook's source
allowlist. ``X-Forwarded-For`` is believed only from a peer listed in
``server.trusted_proxies`` (SEC-49).
"""

from __future__ import annotations

import ipaddress
import logging
from functools import lru_cache

from fastapi import Request

from app.config import settings


def is_trusted_proxy(ip: str, trusted: list[str]) -> bool:
    """Whether *ip* is one of the configured proxies.

    Accepts CIDR entries as well as literal addresses (SEC-32). The
    check used to be a plain ``in`` against the list, so ``10.0.0.0/8``
    never matched anything — which made the setting unusable in Docker,
    where the proxy's address is assigned at container start and is not
    known when the config is written.
    """
    for entry in trusted:
        if ip == entry:
            return True
        try:
            if ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            # A malformed entry should not break IP resolution for
            # every request; skip it and try the next.
            continue
    return False


@lru_cache(maxsize=1)
def _warn_untrusted_forwarding() -> None:
    """Say once that forwarded addresses are being discarded.

    Cached rather than guarded by a module flag so the "only once" part
    is the mechanism rather than a convention someone has to maintain.
    """
    logging.getLogger(__name__).warning(
        "X-Forwarded-For is present but server.trusted_proxies is empty, so the "
        "real client address is being ignored. Set trusted_proxies to your "
        "reverse proxy's address or network."
    )


def get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting trusted proxies."""
    trusted = settings.server.trusted_proxies
    forwarded = request.headers.get("x-forwarded-for", "")
    if trusted and request.client:
        # SEC-49: the header is only as trustworthy as the peer that sent
        # it. A client reaching unbork around the proxy could otherwise
        # name any address it liked, defeating the login limiter's IP key
        # and falsifying audit IPs.
        if forwarded and is_trusted_proxy(request.client.host, trusted):
            # Walk the chain from right to left, skipping trusted proxies
            chain = [ip.strip() for ip in forwarded.split(",")]
            for ip in reversed(chain):
                if not is_trusted_proxy(ip, trusted):
                    return ip
    elif forwarded:
        # A reverse proxy is forwarding the real client address and
        # unbork is discarding it, so every request looks like it came
        # from the proxy — which is what collapsed the login rate limit
        # into a single shared bucket (SEC-32).
        _warn_untrusted_forwarding()
    if request.client:
        return request.client.host
    return "127.0.0.1"
