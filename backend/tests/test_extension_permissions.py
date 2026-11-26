"""S-016: an extension declares a permission of its own.

A stand-in extension (``register`` below, loaded through the real plugin
loader into the real app) registers ``standin.override`` for admin and
operator, then adds one route behind ``require()`` and one that checks a
body target with ``require_on()``. Core's catalog and roles must come back
unchanged once it is gone.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.auth.permissions import (
    PLUGIN_API,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    known_permissions,
    register_permission,
    require,
    require_on,
    reset_extension_permissions,
    set_permission_resolver,
    set_visible_groups_resolver,
    set_visible_hosts_resolver,
)
from app.plugins import is_compatible, load_plugins
from tests.test_permission_scopes import Grant, world  # noqa: F401  (fixture)
from tests.test_permissions import _client

PERM = "standin.override"
_CORE = {role: frozenset(perms) for role, perms in ROLE_PERMISSIONS.items()}


def register(app: FastAPI) -> None:
    # Before the routes: require() checks the name when it is called.
    register_permission(PERM, roles=("admin", "operator"))
    router = APIRouter(prefix="/api/standin", tags=["standin"])

    @router.get("/override")
    async def read_override(user=Depends(require(PERM))):
        return {"ok": True}

    @router.post("/override/{target}")
    async def act_on(target: int, user=Depends(require(PERM, scope=_anywhere()))):
        await require_on(user, PERM, hosts=[target])
        return {"ok": True}

    app.include_router(router)


register.requires_plugin_api = "1.6"


def _anywhere():
    from app.auth.permissions import anywhere

    return anywhere


@pytest.fixture(autouse=True)
def _clean():
    reset_extension_permissions()
    yield
    reset_extension_permissions()


@pytest.fixture
def standin(app):
    """The stand-in loaded into the real app; its routes removed afterwards."""
    before = list(app.router.routes)
    loaded = load_plugins(
        app,
        [EntryPoint("standin", "tests.test_extension_permissions:register", "unbork.plugins")],
    )
    assert [p.name for p in loaded] == ["standin"]
    added = [r for r in app.router.routes if r not in before]
    # Ahead of the SPA fallback, as create_app() would have them.
    app.router.routes[:] = added + before
    try:
        yield
    finally:
        app.router.routes[:] = before


@pytest.mark.parametrize(("role", "expected"), [("admin", 200), ("operator", 200), ("viewer", 403)])
async def test_the_route_answers_the_roles_that_hold_it(app, db, standin, role, expected):
    client, _ = await _client(app, db, role)
    try:
        resp = await client.get("/api/standin/override")
        assert resp.status_code == expected, resp.text
        if expected == 403:
            assert resp.json()["detail"] == f"Missing permission: {PERM}"
        me = (await client.get("/api/users/me")).json()
        assert (PERM in me["permissions"]) is (expected == 200)
    finally:
        await client.aclose()


async def test_require_on_with_a_scoped_resolver(app, db, standin, world):  # noqa: F811
    grant = Grant({world["h_in"]}, {world["g_in"]})

    async def permissions(user, scope):
        held = await grant.permissions(user, scope)
        return held | {PERM} if held else held

    set_permission_resolver(permissions)
    set_visible_hosts_resolver(grant.visible_hosts)
    set_visible_groups_resolver(grant.visible_groups)
    client, _ = await _client(app, db, None)
    try:
        ok = await client.post(f"/api/standin/override/{world['h_in']}")
        assert ok.status_code == 200, ok.text
        refused = await client.post(f"/api/standin/override/{world['h_out']}")
        assert refused.status_code == 403
    finally:
        set_permission_resolver(None)
        set_visible_hosts_resolver(None)
        set_visible_groups_resolver(None)
        await client.aclose()


def test_an_unregistered_name_fails_at_definition_and_names_the_hook():
    with pytest.raises(ValueError, match="register_permission"):
        require("standin.unknown")


@pytest.mark.parametrize("name", ["hosts.view", "Bad Name", "nodot", "a.b.c", "Area.x"])
def test_core_and_badly_formed_names_are_refused(name):
    with pytest.raises(ValueError):
        register_permission(name)


def test_registering_again_is_a_no_op_and_other_roles_are_refused():
    register_permission(PERM, roles=("operator",))
    register_permission(PERM, roles=("operator", "admin"))
    with pytest.raises(ValueError):
        register_permission(PERM, roles=("viewer",))
    with pytest.raises(ValueError):
        register_permission("standin.other", roles=("superuser",))


def test_admin_always_holds_it_and_the_default_is_admin_only():
    register_permission("standin.admin_only")
    assert "standin.admin_only" in ROLE_PERMISSIONS[Role.ADMIN]
    assert "standin.admin_only" not in ROLE_PERMISSIONS[Role.OPERATOR]
    assert ROLE_PERMISSIONS[Role.ADMIN] == known_permissions()


def test_reset_restores_the_core_mapping():
    register_permission(PERM, roles=("viewer",))
    assert PERM in ROLE_PERMISSIONS[Role.VIEWER]
    reset_extension_permissions()
    assert ROLE_PERMISSIONS == _CORE
    assert known_permissions() == {p.value for p in Permission}


def test_the_contract_version():
    major, minor = (int(x) for x in PLUGIN_API.split("."))
    assert (major, minor) >= (1, 6)
    assert is_compatible("1.2")
