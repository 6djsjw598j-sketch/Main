"""Initial schema: workspaces, watchlist items, preferences.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspaces_token_hash", "workspaces", ["token_hash"], unique=True)

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "symbol", name="uq_watchlist_workspace_symbol"),
    )
    op.create_index("ix_watchlist_items_workspace_id", "watchlist_items", ["workspace_id"])

    op.create_table(
        "preferences",
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("preferences")
    op.drop_index("ix_watchlist_items_workspace_id", table_name="watchlist_items")
    op.drop_table("watchlist_items")
    op.drop_index("ix_workspaces_token_hash", table_name="workspaces")
    op.drop_table("workspaces")
