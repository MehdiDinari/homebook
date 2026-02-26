from __future__ import annotations

from pydantic import BaseModel


class AuthUserOut(BaseModel):
    wp_user_id: int
    email: str
    display_name: str
    roles: list[str]
