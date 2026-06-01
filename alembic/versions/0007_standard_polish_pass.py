"""standard polish pass defaults"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_standard_polish_pass"
down_revision = "0006_quality_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generation_runs") as batch_op:
        batch_op.alter_column(
            "developmental_rewrite_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_runs") as batch_op:
        batch_op.alter_column(
            "developmental_rewrite_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )
