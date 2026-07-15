"""Complete historical coverage identity and plan provenance.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM read_model.historical_coverage_gaps) THEN
                RAISE EXCEPTION
                    'historical coverage identity cannot be inferred for existing rows';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD COLUMN source_listing_valid_from TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD COLUMN source_listing_metadata_version TEXT"
    )
    op.execute("ALTER TABLE read_model.historical_coverage_gaps ADD COLUMN provenance TEXT")
    op.execute("ALTER TABLE read_model.historical_coverage_gaps ADD COLUMN resolution TEXT")
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps ADD COLUMN detected_by_plan_hash TEXT"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps ADD COLUMN covered_by_plan_hash TEXT"
    )
    op.execute("ALTER TABLE read_model.historical_coverage_gaps ADD COLUMN observed_points INTEGER")
    for column in (
        "source_listing_valid_from",
        "source_listing_metadata_version",
        "provenance",
        "resolution",
        "detected_by_plan_hash",
    ):
        op.execute(
            f"ALTER TABLE read_model.historical_coverage_gaps ALTER COLUMN {column} SET NOT NULL"
        )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD CONSTRAINT ck_historical_coverage_provenance "
        "CHECK (provenance = 'IG_HISTORICAL')"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD CONSTRAINT ck_historical_coverage_resolution CHECK (resolution = 'MINUTE')"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD CONSTRAINT ck_historical_coverage_basis CHECK (basis IN ('BID', 'ASK', 'MID'))"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD CONSTRAINT ck_historical_coverage_observed_points "
        "CHECK (observed_points IS NULL OR observed_points >= 0)"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "DROP CONSTRAINT historical_coverage_gaps_pkey"
    )
    op.execute(
        """
        ALTER TABLE read_model.historical_coverage_gaps
        ADD PRIMARY KEY (
            instrument_id, source_provider, source_environment, source_external_id,
            source_listing_valid_from, source_listing_metadata_version,
            provenance, basis, resolution, interval_start, interval_end,
            detected_by_plan_hash
        )
        """
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD FOREIGN KEY (detected_by_plan_hash) REFERENCES ops.backfill_plans(plan_hash)"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD FOREIGN KEY (covered_by_plan_hash) REFERENCES ops.backfill_plans(plan_hash)"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM read_model.historical_coverage_gaps) THEN
                RAISE EXCEPTION 'cannot discard populated historical coverage identity';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "DROP CONSTRAINT historical_coverage_gaps_pkey"
    )
    op.execute(
        """
        ALTER TABLE read_model.historical_coverage_gaps
        ADD PRIMARY KEY (
            instrument_id, source_provider, source_environment, source_external_id,
            basis, interval_start, interval_end
        )
        """
    )
    for column in (
        "observed_points",
        "covered_by_plan_hash",
        "detected_by_plan_hash",
        "resolution",
        "provenance",
        "source_listing_metadata_version",
        "source_listing_valid_from",
    ):
        op.execute(f"ALTER TABLE read_model.historical_coverage_gaps DROP COLUMN {column} CASCADE")
