"""Fan out user-level authentication audit events to tenants

Revision ID: 0003
Revises: 0002
"""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0003_auth_audit_fanout.sql"


def upgrade() -> None:
    # psycopg treats % as a placeholder marker even without parameters; escape it.
    sql = SQL.read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; use a fresh database or restore a backup.")
