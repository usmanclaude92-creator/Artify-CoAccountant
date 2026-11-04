"""Phase 1 foundation schema

Revision ID: 0001
Revises:
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0001_foundation.sql"


def upgrade() -> None:
    # psycopg treats % as a placeholder marker even without parameters; escape it.
    sql = SQL.read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("Phase 1 migrations are forward-only; use a fresh database or restore a backup.")
