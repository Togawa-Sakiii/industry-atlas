"""0002 · entity 加 website 列（演示：改字段 → 升级 → 回滚）"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE entity ADD COLUMN website TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE entity DROP COLUMN website")