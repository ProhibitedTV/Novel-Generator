"""story quality upgrade v2"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_story_quality_v2"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'AWAITING_APPROVAL'")

    op.add_column(
        "projects",
        sa.Column("story_brief", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.add_column(
        "generation_runs",
        sa.Column("pipeline_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "generation_runs",
        sa.Column("pause_after_outline", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("generation_runs", sa.Column("story_bible", sa.JSON(), nullable=True))
    op.add_column("generation_runs", sa.Column("continuity_ledger", sa.JSON(), nullable=True))

    op.add_column("chapter_drafts", sa.Column("continuity_update", sa.JSON(), nullable=True))
    op.add_column("chapter_drafts", sa.Column("qa_notes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chapter_drafts", "qa_notes")
    op.drop_column("chapter_drafts", "continuity_update")
    op.drop_column("generation_runs", "continuity_ledger")
    op.drop_column("generation_runs", "story_bible")
    op.drop_column("generation_runs", "pause_after_outline")
    op.drop_column("generation_runs", "pipeline_version")
    op.drop_column("projects", "story_brief")
