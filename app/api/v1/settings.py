from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import ensure_privacy_settings, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.schemas.profile import PrivacyOut, PrivacyPatch
from app.services.auth import AuthUser, get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/privacy", response_model=PrivacyOut)
async def get_privacy(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> PrivacyOut:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    row = await ensure_privacy_settings(db, user.id)
    return PrivacyOut(
        profile_visibility=row.profile_visibility,
        message_permission=row.message_permission,
        searchable=row.searchable,
    )


@router.patch("/privacy", response_model=PrivacyOut)
async def patch_privacy(
    payload: PrivacyPatch,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> PrivacyOut:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    row = await ensure_privacy_settings(db, user.id)

    if payload.profile_visibility is not None:
        row.profile_visibility = payload.profile_visibility
    if payload.message_permission is not None:
        row.message_permission = payload.message_permission
    if payload.searchable is not None:
        row.searchable = payload.searchable

    await db.commit()
    await db.refresh(row)
    return PrivacyOut(
        profile_visibility=row.profile_visibility,
        message_permission=row.message_permission,
        searchable=row.searchable,
    )
