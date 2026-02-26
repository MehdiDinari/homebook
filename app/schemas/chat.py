from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMemberPreview(BaseModel):
    wp_user_id: int
    display_name: str
    role_tag: str | None = None
    avatar_url: str | None = None


class ChatRoomCreate(BaseModel):
    room_type: str = Field(description="book|private|group")
    title: str
    book_work_id: str | None = None
    member_wp_user_ids: list[int] = Field(default_factory=list)


class ChatPrivateRoomEnsureIn(BaseModel):
    peer_wp_user_id: int
    title: str | None = None


class ChatRoomOut(BaseModel):
    room_id: int
    room_type: str
    title: str
    book_work_id: str | None = None
    member_wp_user_ids: list[int] = Field(default_factory=list)
    member_profiles: list[ChatMemberPreview] = Field(default_factory=list)
    unread_count: int = 0
    pending_invites_count: int = 0


class ChatMessageCreate(BaseModel):
    content: str
    asset_url: str | None = None


class ChatMessageOut(BaseModel):
    id: int
    room_id: int
    sender_wp_user_id: int
    sender_display_name: str | None = None
    sender_role_tag: str | None = None
    sender_avatar_url: str | None = None
    content: str
    asset_url: str | None = None
    created_at: str


class ChatInviteCreateIn(BaseModel):
    invitee_wp_user_id: int
    message: str | None = None


class ChatInviteOut(BaseModel):
    id: int
    room_id: int
    room_title: str | None = None
    inviter_wp_user_id: int
    inviter_display_name: str | None = None
    invitee_wp_user_id: int
    invitee_display_name: str | None = None
    invitee_role_tag: str | None = None
    invitee_avatar_url: str | None = None
    status: str
    message: str | None = None
    created_at: str
    responded_at: str | None = None
