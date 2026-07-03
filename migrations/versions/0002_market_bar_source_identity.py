"""Preserve distinct market-bar sources in the read model.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("market_bars_pkey", "market_bars", schema="read_model", type_="primary")
    op.create_primary_key(
        "market_bars_pkey",
        "market_bars",
        [
            "instrument_id",
            "basis",
            "interval_start",
            "revision",
            "provenance",
            "source_provider",
            "source_environment",
            "source_external_id",
        ],
        schema="read_model",
    )


def downgrade() -> None:
    op.drop_constraint("market_bars_pkey", "market_bars", schema="read_model", type_="primary")
    op.create_primary_key(
        "market_bars_pkey",
        "market_bars",
        ["instrument_id", "basis", "interval_start", "revision"],
        schema="read_model",
    )
