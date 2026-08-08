"""Add durable lineage for provider-selectable native capture."""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE read_model.capture_latest_quotes (
            source_class TEXT NOT NULL,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            capture_source_id TEXT NOT NULL,
            universe_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            external_id TEXT NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            received_time TIMESTAMPTZ NOT NULL,
            bid NUMERIC,
            ask NUMERIC,
            bid_size NUMERIC,
            ask_size NUMERIC,
            quality TEXT NOT NULL,
            global_position BIGINT NOT NULL,
            PRIMARY KEY (source_class, provider, environment, instrument_id)
        )
        """
    )
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN capture_session_id UUID")
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN source_class TEXT")
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN capture_source_id TEXT")
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN universe_id TEXT")
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN configuration_hash TEXT")
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN connection_generation BIGINT")
    op.execute("ALTER TABLE raw.market_messages ADD COLUMN arrival_sequence BIGINT")
    op.execute(
        """
        ALTER TABLE raw.market_messages
        ADD CONSTRAINT raw_market_messages_source_class_check
        CHECK (
            source_class IS NULL OR source_class IN (
                'IG_NATIVE_CAPTURE', 'IBKR_NATIVE_CAPTURE', 'IBKR_HISTORICAL_RESEARCH'
            )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE raw.market_messages
        ADD CONSTRAINT raw_market_messages_lineage_check
        CHECK (
            (connection_generation IS NULL AND arrival_sequence IS NULL)
            OR (connection_generation > 0 AND arrival_sequence > 0)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE raw.market_messages
        ADD CONSTRAINT raw_market_messages_configuration_hash_check
        CHECK (
            configuration_hash IS NULL
            OR configuration_hash ~ '^[0-9a-f]{64}$'
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_raw_market_messages_capture_lineage
            ON raw.market_messages (capture_session_id, connection_generation, arrival_sequence)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_raw_market_messages_capture_lineage
            ON raw.market_messages (capture_session_id, connection_generation, arrival_sequence)
            WHERE capture_session_id IS NOT NULL
              AND connection_generation IS NOT NULL
              AND arrival_sequence IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_raw_market_messages_capture_lineage")
    op.execute("DROP INDEX ix_raw_market_messages_capture_lineage")
    op.execute(
        "ALTER TABLE raw.market_messages "
        "DROP CONSTRAINT raw_market_messages_configuration_hash_check"
    )
    op.execute("ALTER TABLE raw.market_messages DROP CONSTRAINT raw_market_messages_lineage_check")
    op.execute(
        "ALTER TABLE raw.market_messages DROP CONSTRAINT raw_market_messages_source_class_check"
    )
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN arrival_sequence")
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN connection_generation")
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN configuration_hash")
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN universe_id")
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN capture_source_id")
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN source_class")
    op.execute("ALTER TABLE raw.market_messages DROP COLUMN capture_session_id")
    op.execute("DROP TABLE read_model.capture_latest_quotes")
