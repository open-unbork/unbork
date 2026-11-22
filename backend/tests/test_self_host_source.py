"""The anti-lockout rule on the host unbork itself runs on.

A bridged container reaches its own host without NAT, so that host sees
the container's bridge address, which Docker hands out again on every
restart. A ``/32`` for it locked unbork out of lin-manager the first time
the container came back with another address, and the probe that would
have noticed was blocked by that very rule. So there the bridge's network
is recorded instead.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai import labdog_host
from app.rules.merge import _make_ssh_lockout_rule, merge_group_rules
from app.ssh_utils import get_source_ip

DOCKER_HOST = "\n".join(
    [
        "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever",
        "2: eth0    inet 10.10.101.5/24 brd 10.10.101.255 scope global eth0",
        "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0",
        "5: br-3f2a1b    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-3f2a1b",
    ]
)


def _conn(stdout: str, exit_status: int = 0) -> MagicMock:
    conn = MagicMock()
    conn.run = AsyncMock(return_value=SimpleNamespace(stdout=stdout, exit_status=exit_status))
    conn.get_extra_info = MagicMock(return_value=("192.0.2.99", 50000))
    return conn


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ("172.18.0.2", "172.18.0.0/16"),  # compose network on this host
        ("172.17.0.3", "172.17.0.0/16"),  # default bridge
        ("10.10.101.5", None),  # host networking / native install
        ("10.10.101.7", None),  # another machine on the LAN
        ("garbage", None),
    ],
)
def test_bridge_network(client, expected) -> None:
    _, addresses = labdog_host.parse_probe("x 1 22\n" + DOCKER_HOST)
    network = labdog_host.bridge_network(client, addresses)
    assert (str(network) if network else None) == expected


async def test_own_host_records_the_bridge_network() -> None:
    conn = _conn("172.18.0.2 40022 22\n" + DOCKER_HOST)
    assert await get_source_ip(conn) == "172.18.0.0/16"


async def test_other_hosts_record_the_address() -> None:
    """Remote hosts see unbork through NAT, as its host's address."""
    conn = _conn("10.10.101.5 40022 22\n2: eth0    inet 10.10.10.52/24 scope global eth0")
    assert await get_source_ip(conn) == "10.10.101.5"


async def test_no_iproute2_still_reads_ssh_client() -> None:
    """``ip`` missing makes the command exit 127; the first line still counts."""
    conn = _conn("10.10.101.5 40022 22\n", exit_status=127)
    assert await get_source_ip(conn) == "10.10.101.5"


async def test_falls_back_to_the_socket() -> None:
    conn = _conn("")
    assert await get_source_ip(conn) == "192.0.2.99"


def test_lockout_rule_keeps_a_network() -> None:
    assert _make_ssh_lockout_rule("172.18.0.0/16").source_cidr == "172.18.0.0/16"
    assert _make_ssh_lockout_rule("10.10.101.5").source_cidr == "10.10.101.5/32"


def test_merge_uses_the_network() -> None:
    rules = merge_group_rules([], host_source_ip="172.18.0.0/16")
    assert rules[0].is_system and rules[0].source_cidr == "172.18.0.0/16"
