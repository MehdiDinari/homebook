from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import ensure_profile, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.schemas.profile import ProfileOut, ProfilePatch
from app.services.auth import AuthUser, get_current_user

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("/me", response_model=ProfileOut)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ProfileOut:
    user_shadow = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    profile = await ensure_profile(db, user_shadow.id)
    return ProfileOut(
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        interests=profile.interests or [],
        location=profile.location,
    )


@router.patch("/me", response_model=ProfileOut)
async def patch_my_profile(
    payload: ProfilePatch,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ProfileOut:
    user_shadow = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    profile = await ensure_profile(db, user_shadow.id)

    if payload.bio is not None:
        profile.bio = payload.bio
    if payload.avatar_url is not None:
        profile.avatar_url = payload.avatar_url
    if payload.interests is not None:
        profile.interests = payload.interests
    if payload.location is not None:
        profile.location = payload.location

    await db.commit()
    await db.refresh(profile)

    return ProfileOut(
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        interests=profile.interests or [],
        location=profile.location,
    )
