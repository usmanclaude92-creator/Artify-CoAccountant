"""Row-level security, grants, triggers, definer functions

Revision ID: 0002
Revises: 0001
"""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0002_rls_and_grants.sql"


def upgrade() -> None:
    # psycopg treats % as a placeholder marker even without parameters; escape it.
    sql = SQL.read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("Phase 1 migrations are forward-only; use a fresh database or restore a backup.")
