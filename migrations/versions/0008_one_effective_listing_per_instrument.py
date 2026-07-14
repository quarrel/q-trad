"""Enforce one effective provider listing per instrument.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_provider_listings_active_instrument
        ON reference.provider_listings (provider, environment, instrument_id)
        WHERE valid_to IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX reference.uq_provider_listings_active_instrument")
