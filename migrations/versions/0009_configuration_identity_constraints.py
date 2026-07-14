"""Enforce persisted configuration and universe hash shapes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ops.runs
        ADD CONSTRAINT ck_runs_configuration_hash
        CHECK (length(configuration_hash) = 64 AND configuration_hash ~ '^[0-9a-f]+$')
        """
    )
    op.execute(
        """
        ALTER TABLE ops.research_manifests
        ADD CONSTRAINT ck_research_manifests_configuration_hash
        CHECK (length(configuration_hash) = 64 AND configuration_hash ~ '^[0-9a-f]+$')
        """
    )
    op.execute(
        """
        ALTER TABLE ops.backfill_plans
        ADD CONSTRAINT ck_backfill_plans_plan_hash
        CHECK (length(plan_hash) = 64 AND plan_hash ~ '^[0-9a-f]+$')
        """
    )
    op.execute(
        """
        ALTER TABLE ops.backfill_plans
        ADD CONSTRAINT ck_backfill_plans_universe_hash
        CHECK (length(universe_hash) = 64 AND universe_hash ~ '^[0-9a-f]+$')
        """
    )
    op.execute(
        """
        ALTER TABLE reference.provider_listings
        ADD CONSTRAINT ck_provider_listings_universe_hash
        CHECK (
            universe_hash IS NULL
            OR (length(universe_hash) = 64 AND universe_hash ~ '^[0-9a-f]+$')
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE reference.provider_listings DROP CONSTRAINT ck_provider_listings_universe_hash"
    )
    op.execute("ALTER TABLE ops.backfill_plans DROP CONSTRAINT ck_backfill_plans_universe_hash")
    op.execute("ALTER TABLE ops.backfill_plans DROP CONSTRAINT ck_backfill_plans_plan_hash")
    op.execute(
        "ALTER TABLE ops.research_manifests "
        "DROP CONSTRAINT ck_research_manifests_configuration_hash"
    )
    op.execute("ALTER TABLE ops.runs DROP CONSTRAINT ck_runs_configuration_hash")
