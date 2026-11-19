"""add token usage to analysis jobs

Revision ID: 20261110tokens
Revises: d8f7c1b7a19a
Create Date: 2026-11-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261110tokens"
down_revision: Union[str, Sequence[str], None] = "d8f7c1b7a19a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "total_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column("analysis_jobs", "input_tokens", server_default=None)
    op.alter_column("analysis_jobs", "output_tokens", server_default=None)
    op.alter_column("analysis_jobs", "total_tokens", server_default=None)


def downgrade() -> None:
    op.drop_column("analysis_jobs", "total_tokens")
    op.drop_column("analysis_jobs", "output_tokens")
    op.drop_column("analysis_jobs", "input_tokens")
