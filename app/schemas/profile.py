from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    bio: str | None = None
    avatar_url: str | None = None
    interests: list[str] = Field(default_factory=list)
    location: str | None = None


class ProfilePatch(BaseModel):
    bio: str | None = None
    avatar_url: str | None = None
    interests: list[str] | None = None
    location: str | None = None


class PrivacyOut(BaseModel):
    profile_visibility: str = "public"
    message_permission: str = "friends"
    searchable: bool = True


class PrivacyPatch(BaseModel):
    profile_visibility: str | None = None
    message_permission: str | None = None
    searchable: bool | None = None


class FriendRequestCreate(BaseModel):
    to_wp_user_id: int


class FriendRequestOut(BaseModel):
    id: int
    from_wp_user_id: int
    to_wp_user_id: int
    status: str


class UserMiniOut(BaseModel):
    wp_user_id: int
    display_name: str
    role_tag: str | None = None
    avatar_url: str | None = None


class FriendRequestDetailedOut(BaseModel):
    id: int
    from_user: UserMiniOut
    to_user: UserMiniOut
    status: str
    created_at: str
