"""Persist the Stage 3 IBKR historical execution state machine."""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ops.ibkr_request_pacing ADD COLUMN profile_sha256 TEXT")
    op.execute(
        """
        CREATE TABLE ops.ibkr_historical_plans (
            plan_sha256 TEXT PRIMARY KEY,
            plan_bytes BYTEA NOT NULL,
            plan_bytes_sha256 TEXT NOT NULL,
            plan_payload JSONB NOT NULL,
            registered_at TIMESTAMPTZ NOT NULL,
            publication_status TEXT NOT NULL,
            published_at TIMESTAMPTZ,
            CHECK (publication_status IN ('PENDING', 'PUBLISHED')),
            CHECK (length(plan_bytes) > 0),
            CHECK (length(plan_bytes_sha256) = 64)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.ibkr_historical_requests (
            plan_sha256 TEXT NOT NULL
                REFERENCES ops.ibkr_historical_plans(plan_sha256),
            request_sha256 TEXT NOT NULL,
            request_payload JSONB NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            request_kind TEXT NOT NULL,
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            selected_attempt_id UUID,
            publication_status TEXT NOT NULL,
            result_sha256 TEXT,
            published_at TIMESTAMPTZ,
            PRIMARY KEY (plan_sha256, request_sha256),
            CHECK (status IN ('PENDING', 'IN_FLIGHT', 'SUCCEEDED', 'TERMINAL')),
            CHECK (publication_status IN ('PENDING', 'PUBLISHED')),
            CHECK (interval_end > interval_start),
            CHECK ((publication_status = 'PUBLISHED') = (result_sha256 IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.ibkr_historical_attempts (
            attempt_id UUID PRIMARY KEY,
            plan_sha256 TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            attempt_ordinal INTEGER NOT NULL CHECK (attempt_ordinal > 0),
            provider_request_id BIGINT NOT NULL CHECK (provider_request_id > 0),
            connection_generation BIGINT NOT NULL CHECK (connection_generation > 0),
            started_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL,
            terminal_at TIMESTAMPTZ,
            terminal_disposition TEXT,
            detail TEXT,
            UNIQUE (plan_sha256, request_sha256, attempt_ordinal),
            UNIQUE (connection_generation, provider_request_id),
            FOREIGN KEY (plan_sha256, request_sha256)
                REFERENCES ops.ibkr_historical_requests(plan_sha256, request_sha256),
            CHECK (
                status IN (
                    'STARTED', 'SUCCEEDED', 'RETRYABLE_FAILURE',
                    'INVALIDATED', 'TERMINAL_FAILURE'
                )
            ),
            CHECK ((status = 'STARTED') = (terminal_at IS NULL)),
            CHECK (terminal_disposition IS NULL OR length(terminal_disposition) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_ibkr_historical_attempts_plan_status
            ON ops.ibkr_historical_attempts (plan_sha256, status)
        """
    )
    op.execute(
        """
        CREATE TABLE ops.ibkr_historical_callbacks (
            callback_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            attempt_id UUID NOT NULL
                REFERENCES ops.ibkr_historical_attempts(attempt_id),
            provider_request_id BIGINT NOT NULL CHECK (provider_request_id > 0),
            connection_generation BIGINT NOT NULL CHECK (connection_generation > 0),
            sequence BIGINT NOT NULL CHECK (sequence > 0),
            callback_kind TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL,
            closure_eligible BOOLEAN NOT NULL,
            UNIQUE (attempt_id, sequence)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.ibkr_historical_completion_markers (
            marker_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            attempt_id UUID NOT NULL
                REFERENCES ops.ibkr_historical_attempts(attempt_id),
            provider_request_id BIGINT NOT NULL CHECK (provider_request_id > 0),
            connection_generation BIGINT NOT NULL CHECK (connection_generation > 0),
            sequence BIGINT NOT NULL CHECK (sequence > 0),
            completed_at TIMESTAMPTZ NOT NULL,
            raw_midpoint_bar_callback_count INTEGER NOT NULL
                CHECK (raw_midpoint_bar_callback_count >= 0),
            raw_schedule_callback_count INTEGER NOT NULL
                CHECK (raw_schedule_callback_count >= 0),
            closure_eligible BOOLEAN NOT NULL,
            payload JSONB NOT NULL,
            UNIQUE (attempt_id, sequence)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_ibkr_historical_callbacks_attempt_sequence
            ON ops.ibkr_historical_callbacks (attempt_id, sequence)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_ibkr_historical_callbacks_attempt_sequence")
    op.execute("DROP TABLE ops.ibkr_historical_completion_markers")
    op.execute("DROP TABLE ops.ibkr_historical_callbacks")
    op.execute("DROP INDEX ix_ibkr_historical_attempts_plan_status")
    op.execute("DROP TABLE ops.ibkr_historical_attempts")
    op.execute("DROP TABLE ops.ibkr_historical_requests")
    op.execute("DROP TABLE ops.ibkr_historical_plans")
    op.execute("ALTER TABLE ops.ibkr_request_pacing DROP COLUMN profile_sha256")
