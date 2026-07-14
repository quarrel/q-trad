"""Add immutable research manifest identity without breaking legacy writers.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ops.research_manifests ADD COLUMN manifest_sha256 TEXT")
    op.execute("ALTER TABLE ops.research_manifests ADD COLUMN universe_name TEXT")
    op.execute("ALTER TABLE ops.research_manifests ADD COLUMN file_sha256 JSONB")
    op.execute(
        """
        ALTER TABLE ops.research_manifests
        ADD CONSTRAINT ck_research_manifest_versioned_identity
        CHECK (
            (schema_version = 1 AND manifest_sha256 IS NULL
                AND universe_name IS NULL AND file_sha256 IS NULL)
            OR
            (schema_version = 2 AND manifest_sha256 ~ '^[0-9a-f]{64}$'
                AND universe_name IS NOT NULL AND length(universe_name) BETWEEN 1 AND 64
                AND file_sha256 IS NOT NULL AND jsonb_typeof(file_sha256) = 'object')
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ops.research_manifests WHERE schema_version = 2) THEN
                RAISE EXCEPTION 'cannot discard version-two research manifest identity';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE ops.research_manifests DROP CONSTRAINT ck_research_manifest_versioned_identity"
    )
    op.execute("ALTER TABLE ops.research_manifests DROP COLUMN file_sha256")
    op.execute("ALTER TABLE ops.research_manifests DROP COLUMN universe_name")
    op.execute("ALTER TABLE ops.research_manifests DROP COLUMN manifest_sha256")
