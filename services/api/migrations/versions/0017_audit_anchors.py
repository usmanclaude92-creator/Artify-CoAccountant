"""Audit anchors and platform audit chain

Revision ID: 0017
Revises: 0016
"""

from pathlib import Path

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0017_audit_anchors.sql"


def upgrade() -> None:
    sql = SQL.read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; use a fresh database or restore a backup.")
