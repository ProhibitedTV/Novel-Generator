"""Optional persisted local image settings."""
from alembic import op
import sqlalchemy as sa

revision = "0008_image_settings"
down_revision = "0007_standard_polish_pass"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("image_generation_config", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("options", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("image_generation_config")
