"""Persist native capture-session acceptance and persistence counters."""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops.capture_session_metrics (
            capture_session_id UUID PRIMARY KEY,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            source_class TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            records_received BIGINT NOT NULL CHECK (records_received >= 0),
            persisted BIGINT NOT NULL CHECK (persisted >= 0),
            failed BIGINT NOT NULL CHECK (failed >= 0),
            dropped BIGINT NOT NULL CHECK (dropped >= 0),
            UNIQUE (
                provider, environment, source_class, configuration_hash,
                capture_session_id
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ops.capture_session_metrics")
