"""chat group invites

Revision ID: 0005_chat_group_invites
Revises: 0004_wallet_presence_access
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_chat_group_invites"
down_revision = "0004_wallet_presence_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_room_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("inviter_user_id", sa.Integer(), nullable=False),
        sa.Column("invitee_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("inviter_user_id <> invitee_user_id", name="ck_chat_room_inviter_invitee_diff"),
        sa.ForeignKeyConstraint(["invitee_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inviter_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_id"], ["chat_rooms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "room_id",
            "invitee_user_id",
            "status",
            name="uq_chat_room_invite_room_invitee_status",
        ),
    )
    op.create_index("ix_chat_room_invites_room_id", "chat_room_invites", ["room_id"], unique=False)
    op.create_index("ix_chat_room_invites_inviter_user_id", "chat_room_invites", ["inviter_user_id"], unique=False)
    op.create_index("ix_chat_room_invites_invitee_user_id", "chat_room_invites", ["invitee_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_room_invites_invitee_user_id", table_name="chat_room_invites")
    op.drop_index("ix_chat_room_invites_inviter_user_id", table_name="chat_room_invites")
    op.drop_index("ix_chat_room_invites_room_id", table_name="chat_room_invites")
    op.drop_table("chat_room_invites")
