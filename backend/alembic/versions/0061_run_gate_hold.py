"""Sync jobs and action runs can be held by an extension's run gate.

Revision ID: 0061_run_gate_hold
Revises: 0060_schedule_owner

Additive. ``gate_retry_at`` is set on a ``pending`` row an installed
extension deferred (``app.run_gate``): the host queue skips the row until
then, and ``app.tasks.run_gate_retry`` sends it again once it has passed.
NULL, as on every existing row, means not held.
"""

import sqlalchemy as sa

from alembic import op

revision = "0061_run_gate_hold"
down_revision = "0060_schedule_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_jobs", sa.Column("gate_retry_at", sa.DateTime(timezone=True)))
    op.add_column("action_runs", sa.Column("gate_retry_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("action_runs", "gate_retry_at")
    op.drop_column("sync_jobs", "gate_retry_at")
