"""Create data-foundation schemas and tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA raw")
    op.execute("CREATE SCHEMA canonical")
    op.execute("CREATE SCHEMA reference")
    op.execute("CREATE SCHEMA read_model")
    op.execute("CREATE SCHEMA ops")

    op.execute(
        """
        CREATE TABLE raw.market_messages (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            subscription TEXT NOT NULL,
            deduplication_key TEXT NOT NULL,
            received_time TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (provider, environment, deduplication_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE raw.quarantine (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            raw_record_id BIGINT NOT NULL UNIQUE REFERENCES raw.market_messages(id),
            reason_code TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE canonical.events (
            global_position BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id UUID NOT NULL UNIQUE,
            stream_id TEXT NOT NULL,
            stream_version INTEGER NOT NULL CHECK (stream_version > 0),
            event_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version > 0),
            event_time TIMESTAMPTZ NOT NULL,
            received_time TIMESTAMPTZ NOT NULL,
            persisted_time TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            correlation_id UUID NOT NULL,
            causation_id UUID,
            producer TEXT NOT NULL,
            producer_version TEXT NOT NULL,
            payload JSONB NOT NULL,
            raw_record_id BIGINT REFERENCES raw.market_messages(id),
            UNIQUE (stream_id, stream_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX events_type_time_idx ON canonical.events (event_type, event_time)"
    )
    op.execute(
        """
        CREATE TABLE reference.instruments (
            instrument_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            base_currency TEXT,
            quote_currency TEXT NOT NULL,
            search_aliases JSONB NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE reference.provider_listings (
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            external_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            display_name TEXT NOT NULL,
            product_type TEXT NOT NULL,
            currency TEXT NOT NULL,
            minimum_deal_size NUMERIC NOT NULL,
            price_increment NUMERIC,
            valid_from TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            metadata_version TEXT NOT NULL,
            metadata JSONB NOT NULL,
            PRIMARY KEY (provider, environment, external_id, valid_from)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE read_model.latest_quotes (
            instrument_id TEXT PRIMARY KEY REFERENCES reference.instruments(instrument_id),
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            external_id TEXT NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            received_time TIMESTAMPTZ NOT NULL,
            bid NUMERIC,
            ask NUMERIC,
            bid_size NUMERIC,
            ask_size NUMERIC,
            quality TEXT NOT NULL,
            global_position BIGINT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE read_model.market_bars (
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            basis TEXT NOT NULL,
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ NOT NULL,
            open NUMERIC NOT NULL,
            high NUMERIC NOT NULL,
            low NUMERIC NOT NULL,
            close NUMERIC NOT NULL,
            sample_count INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            provenance TEXT NOT NULL,
            quality TEXT NOT NULL,
            source_provider TEXT NOT NULL,
            source_environment TEXT NOT NULL,
            source_external_id TEXT NOT NULL,
            global_position BIGINT NOT NULL,
            PRIMARY KEY (instrument_id, basis, interval_start, revision)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE read_model.data_gaps (
            gap_id UUID PRIMARY KEY,
            instrument_id TEXT NOT NULL REFERENCES reference.instruments(instrument_id),
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ NOT NULL,
            reason TEXT NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL,
            repaired_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.projection_checkpoints (
            projection_name TEXT PRIMARY KEY,
            global_position BIGINT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.runs (
            run_id UUID PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            environment TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            configuration_hash TEXT NOT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.adapter_health (
            adapter_name TEXT PRIMARY KEY,
            environment TEXT NOT NULL,
            status TEXT NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            last_message_at TIMESTAMPTZ,
            detail TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.quota_state (
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            allowance_name TEXT NOT NULL,
            remaining INTEGER NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (provider, environment, allowance_name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.research_manifests (
            manifest_id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL,
            schema_version INTEGER NOT NULL,
            row_count BIGINT NOT NULL,
            minimum_event_time TIMESTAMPTZ,
            maximum_event_time TIMESTAMPTZ,
            content_sha256 TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            files JSONB NOT NULL,
            metadata JSONB NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA ops CASCADE")
    op.execute("DROP SCHEMA read_model CASCADE")
    op.execute("DROP SCHEMA reference CASCADE")
    op.execute("DROP SCHEMA canonical CASCADE")
    op.execute("DROP SCHEMA raw CASCADE")
