"""Add the capture database read-only privilege role.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_READ_SCHEMAS = ("canonical", "reference", "read_model", "ops")


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qtrad_capture_reader') THEN
                EXECUTE 'CREATE ROLE qtrad_capture_reader NOLOGIN';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        ALTER ROLE qtrad_capture_reader
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO qtrad_capture_reader',
                current_database()
            );
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON SCHEMA raw FROM qtrad_capture_reader")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA raw FROM qtrad_capture_reader")
    for schema in _READ_SCHEMAS:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO qtrad_capture_reader")
        op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO qtrad_capture_reader")
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            "GRANT SELECT ON TABLES TO qtrad_capture_reader"
        )


def downgrade() -> None:
    for schema in _READ_SCHEMAS:
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            "REVOKE SELECT ON TABLES FROM qtrad_capture_reader"
        )
        op.execute(f"REVOKE SELECT ON ALL TABLES IN SCHEMA {schema} FROM qtrad_capture_reader")
        op.execute(f"REVOKE USAGE ON SCHEMA {schema} FROM qtrad_capture_reader")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE CONNECT ON DATABASE %I FROM qtrad_capture_reader',
                current_database()
            );
        END
        $$
        """
    )
    op.execute("DROP ROLE qtrad_capture_reader")
