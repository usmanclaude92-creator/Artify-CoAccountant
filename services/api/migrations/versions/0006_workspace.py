"""Workspace structure and customization engine

Revision ID: 0006
Revises: 0005
"""

from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0006_workspace.sql"


def upgrade() -> None:
    sql = SQL.read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; use a fresh database or restore a backup.")
