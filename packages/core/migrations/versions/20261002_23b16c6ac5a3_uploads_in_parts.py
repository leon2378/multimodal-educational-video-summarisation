"""uploads in parts

Revision ID: 23b16c6ac5a3
Revises: 2b184745a31b
Create Date: 2026-10-02 12:27:49.030547

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "23b16c6ac5a3"
down_revision: str | Sequence[str] | None = "2b184745a31b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lectures", sa.Column("upload_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lectures", "upload_id")
