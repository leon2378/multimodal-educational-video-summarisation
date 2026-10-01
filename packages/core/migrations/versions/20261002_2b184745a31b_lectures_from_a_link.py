"""lectures from a link

Revision ID: 2b184745a31b
Revises: dc4d01cd56d0
Create Date: 2026-10-02 09:38:51.744926

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b184745a31b"
down_revision: str | Sequence[str] | None = "dc4d01cd56d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lectures", sa.Column("source_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("lectures", "source_url")
