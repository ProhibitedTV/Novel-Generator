"""quality profiles for generation runs"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_quality_profiles"
down_revision = "0005_run_resilience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("quality_profile", sa.String(length=32), nullable=False, server_default="balanced"),
    )


def downgrade() -> None:
    op.drop_column("generation_runs", "quality_profile")
