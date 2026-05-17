"""developmental rewrite pass flag"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_developmental_rewrite"
down_revision = "0003_provider_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("developmental_rewrite_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("generation_runs", "developmental_rewrite_enabled")
