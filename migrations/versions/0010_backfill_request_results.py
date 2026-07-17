"""Record completed historical requests separately from data coverage.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops.historical_request_usage (
            request_id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES ops.runs(run_id),
            plan_hash TEXT NOT NULL REFERENCES ops.backfill_plans(plan_hash),
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            source_provider TEXT NOT NULL,
            source_environment TEXT NOT NULL,
            source_external_id TEXT NOT NULL,
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ NOT NULL,
            requested_points INTEGER NOT NULL CHECK (requested_points > 0),
            returned_points INTEGER CHECK (returned_points >= 0),
            provider_remaining INTEGER CHECK (provider_remaining >= 0),
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            CHECK ((completed_at IS NULL) = (returned_points IS NULL))
        )
        """
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD COLUMN request_completed_at TIMESTAMPTZ"
    )
    op.execute("ALTER TABLE read_model.historical_coverage_gaps ADD COLUMN returned_points INTEGER")
    op.execute(
        """
        UPDATE read_model.historical_coverage_gaps
        SET request_completed_at = covered_at,
            returned_points = observed_points
        WHERE covered_at IS NOT NULL
        """
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD CONSTRAINT ck_historical_request_result_pair "
        "CHECK ((request_completed_at IS NULL) = (returned_points IS NULL))"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "ADD CONSTRAINT ck_historical_returned_points "
        "CHECK (returned_points IS NULL OR returned_points >= 0)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "DROP CONSTRAINT ck_historical_returned_points"
    )
    op.execute(
        "ALTER TABLE read_model.historical_coverage_gaps "
        "DROP CONSTRAINT ck_historical_request_result_pair"
    )
    op.execute("ALTER TABLE read_model.historical_coverage_gaps DROP COLUMN returned_points")
    op.execute("ALTER TABLE read_model.historical_coverage_gaps DROP COLUMN request_completed_at")
    op.execute("DROP TABLE ops.historical_request_usage")
