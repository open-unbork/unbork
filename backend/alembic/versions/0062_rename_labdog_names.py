"""Rename the last stored LabDog names to unbork.

``hosts.labdog_source_ip`` becomes ``hosts.unbork_source_ip``, and the
seeded playbooks pack and its git repository row become
``unbork-playbooks``. A pack or repository the operator renamed is left
alone, and so is the seeded one when a row with the new name already
exists. No data is dropped.

Revision ID: 0062_rename_labdog_names
Revises: 0061_run_gate_hold
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0062_rename_labdog_names"
down_revision = "0061_run_gate_hold"
branch_labels = None
depends_on = None

_OLD = "labdog-playbooks"
_NEW = "unbork-playbooks"


def _rename(table: str, old: str, new: str) -> None:
    op.get_bind().execute(
        sa.text(
            f"UPDATE {table} SET name = :new WHERE name = :old "  # noqa: S608 - fixed table names
            f"AND NOT EXISTS (SELECT 1 FROM {table} WHERE name = :new)"
        ),
        {"old": old, "new": new},
    )


def upgrade() -> None:
    op.alter_column("hosts", "labdog_source_ip", new_column_name="unbork_source_ip")
    _rename("action_packs", _OLD, _NEW)
    _rename("git_repositories", _OLD, _NEW)


def downgrade() -> None:
    _rename("git_repositories", _NEW, _OLD)
    _rename("action_packs", _NEW, _OLD)
    op.alter_column("hosts", "unbork_source_ip", new_column_name="labdog_source_ip")
