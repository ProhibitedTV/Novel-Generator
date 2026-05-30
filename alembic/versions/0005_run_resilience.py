"""run resilience metadata and stage attempts"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_run_resilience"
down_revision = "0004_developmental_rewrite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_runs", sa.Column("worker_id", sa.String(length=255), nullable=True))
    op.add_column("generation_runs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "generation_runs",
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "run_stage_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "chapter_number",
            "attempt_number",
            name="uq_run_stage_attempt_number",
        ),
    )
    op.create_index("ix_run_stage_attempts_run_id", "run_stage_attempts", ["run_id"])
    op.create_index("ix_run_stage_attempts_stage", "run_stage_attempts", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_run_stage_attempts_stage", table_name="run_stage_attempts")
    op.drop_index("ix_run_stage_attempts_run_id", table_name="run_stage_attempts")
    op.drop_table("run_stage_attempts")
    op.drop_column("generation_runs", "recovery_count")
    op.drop_column("generation_runs", "last_heartbeat_at")
    op.drop_column("generation_runs", "worker_id")
