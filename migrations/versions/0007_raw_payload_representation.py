"""Identify raw payload representation without rewriting audit payloads.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL stores this constant fast default in catalogue metadata; existing rows are not
    # rewritten. Code zero remains deliberately conservative for pre-marker and rollback writers.
    op.execute(
        "ALTER TABLE raw.market_messages "
        "ADD COLUMN payload_representation SMALLINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE raw.market_messages "
        "ADD CONSTRAINT ck_raw_payload_representation "
        "CHECK (payload_representation IN (0, 1, 2, 3)) NOT VALID"
    )
    op.execute("ALTER TABLE raw.market_messages VALIDATE CONSTRAINT ck_raw_payload_representation")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM raw.market_messages WHERE payload_representation = 2
            ) THEN
                RAISE EXCEPTION
                    'cannot discard changed-field raw payload representation identity';
            END IF;
        END
        $$
        """
    )
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN payload_representation")
