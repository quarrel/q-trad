"""Persist adapter recovery state and IBKR pacing reservations."""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ops.adapter_health
            ADD COLUMN reason_codes TEXT[] NOT NULL DEFAULT '{}',
            ADD COLUMN recovery_action TEXT NOT NULL DEFAULT 'NONE',
            ADD COLUMN attributes JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        """
        CREATE TABLE ops.ibkr_request_pacing (
            reservation_id UUID PRIMARY KEY,
            requested_at TIMESTAMPTZ NOT NULL,
            request_kind TEXT NOT NULL,
            contract_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            weight INTEGER NOT NULL CHECK (weight > 0),
            UNIQUE (request_kind, contract_key, request_fingerprint, requested_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_ibkr_request_pacing_requested_at
            ON ops.ibkr_request_pacing (requested_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_ibkr_request_pacing_requested_at")
    op.execute("DROP TABLE ops.ibkr_request_pacing")
    op.execute("ALTER TABLE ops.adapter_health DROP COLUMN attributes")
    op.execute("ALTER TABLE ops.adapter_health DROP COLUMN recovery_action")
    op.execute("ALTER TABLE ops.adapter_health DROP COLUMN reason_codes")
