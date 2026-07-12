"""Add capture-operation audit projections.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE reference.provider_listings ADD COLUMN economics JSONB NOT NULL "
        "DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE reference.provider_listings ADD COLUMN universe_hash TEXT")
    op.execute(
        """
        CREATE TABLE ops.backfill_plans (
            plan_id UUID PRIMARY KEY,
            plan_hash TEXT NOT NULL UNIQUE,
            universe_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('PLANNED', 'EXECUTING', 'COMPLETED', 'FAILED')),
            plan JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            executed_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE read_model.historical_coverage_gaps (
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            source_provider TEXT NOT NULL,
            source_environment TEXT NOT NULL,
            source_external_id TEXT NOT NULL,
            basis TEXT NOT NULL,
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL,
            covered_at TIMESTAMPTZ,
            PRIMARY KEY (
                instrument_id, source_provider, source_environment, source_external_id,
                basis, interval_start, interval_end
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE read_model.historical_coverage_gaps")
    op.execute("DROP TABLE ops.backfill_plans")
    op.execute("ALTER TABLE reference.provider_listings DROP COLUMN universe_hash")
    op.execute("ALTER TABLE reference.provider_listings DROP COLUMN economics")
