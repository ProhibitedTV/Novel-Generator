"""provider routing and multi-provider support"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_provider_routing"
down_revision = "0002_story_quality_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("preferred_provider_name", sa.String(length=64), nullable=False, server_default="ollama"),
    )
    op.add_column(
        "projects",
        sa.Column("task_routing", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.add_column(
        "provider_configs",
        sa.Column("api_key", sa.String(length=500), nullable=True),
    )

    op.add_column(
        "generation_runs",
        sa.Column("provider_name", sa.String(length=64), nullable=False, server_default="ollama"),
    )
    op.add_column(
        "generation_runs",
        sa.Column("task_routing", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("generation_runs", "task_routing")
    op.drop_column("generation_runs", "provider_name")
    op.drop_column("provider_configs", "api_key")
    op.drop_column("projects", "task_routing")
    op.drop_column("projects", "preferred_provider_name")
