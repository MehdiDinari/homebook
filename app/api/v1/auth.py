from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.auth import AuthUserOut
from app.services.auth import AuthUser, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthUserOut)
async def me(current_user: AuthUser = Depends(get_current_user)) -> AuthUserOut:
    return AuthUserOut(
        wp_user_id=current_user.wp_user_id,
        email=current_user.email,
        display_name=current_user.display_name,
        roles=current_user.roles,
    )
