"""support tickets for help center

Revision ID: 0006_support_tickets
Revises: 0005_chat_group_invites
Create Date: 2026-02-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_support_tickets"
down_revision = "0005_chat_group_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requester_user_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=180), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default=sa.text("'normale'")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'open'")),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False, server_default=sa.text("'help_support_form'")),
        sa.Column("page_url", sa.String(length=1000), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "priority in ('basse','normale','haute','critique')",
            name="ck_support_tickets_priority",
        ),
        sa.CheckConstraint(
            "status in ('open','in_progress','resolved','closed')",
            name="ck_support_tickets_status",
        ),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_tickets_requester_user_id", "support_tickets", ["requester_user_id"], unique=False)
    op.create_index(
        "ix_support_tickets_requester_created",
        "support_tickets",
        ["requester_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_support_tickets_status_created",
        "support_tickets",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_status_created", table_name="support_tickets")
    op.drop_index("ix_support_tickets_requester_created", table_name="support_tickets")
    op.drop_index("ix_support_tickets_requester_user_id", table_name="support_tickets")
    op.drop_table("support_tickets")
