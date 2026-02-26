from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.models.user import PrivacySettings, Profile, UserShadow
from app.services.wordpress import fetch_wp_user_by_email, fetch_wp_user_by_id

logger = logging.getLogger(__name__)

def as_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


async def get_user_shadow_by_wp_id(db: AsyncSession, wp_user_id: int) -> UserShadow:
    user = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == wp_user_id))
    ).scalar_one_or_none()
    if user is None:
        try:
            wp_user = await fetch_wp_user_by_id(wp_user_id)
        except Exception as exc:
            logger.exception("WordPress user lookup failed for wp_user_id=%s", wp_user_id)
            raise HTTPException(status_code=502, detail="WordPress user lookup failed") from exc
        if wp_user is None:
            raise HTTPException(status_code=401, detail="WordPress user not found")
        user = await upsert_user_shadow(
            db,
            wp_user_id=int(wp_user["id"]),
            email=str(wp_user.get("email") or "").strip().lower(),
            display_name=str(wp_user.get("display_name") or "").strip(),
            roles=[str(x).strip().lower() for x in (wp_user.get("roles") or [])],
            avatar_url=str(wp_user.get("avatar_url") or "").strip() or None,
        )
    return user


async def upsert_user_shadow(
    db: AsyncSession,
    *,
    wp_user_id: int,
    email: str,
    display_name: str,
    roles: list[str],
    avatar_url: str | None = None,
) -> UserShadow:
    clean_email = (email or "").strip().lower()
    clean_name = (display_name or "").strip()
    if not clean_email:
        raise HTTPException(status_code=401, detail="WordPress user email missing")
    if not clean_name:
        clean_name = clean_email

    clean_roles = [str(x).strip().lower() for x in roles if str(x).strip()]
    clean_avatar = (avatar_url or "").strip()
    if clean_avatar and not clean_avatar.lower().startswith(("http://", "https://")):
        clean_avatar = ""

    row = (await db.execute(select(UserShadow).where(UserShadow.wp_user_id == int(wp_user_id)))).scalar_one_or_none()
    if row is None:
        row = UserShadow(
            wp_user_id=int(wp_user_id),
            email=clean_email,
            display_name=clean_name,
            roles=clean_roles,
        )
        db.add(row)
        await db.flush()
    else:
        row.email = clean_email
        row.display_name = clean_name
        if clean_roles:
            row.roles = clean_roles

    if clean_avatar:
        profile = (await db.execute(select(Profile).where(Profile.user_id == row.id))).scalar_one_or_none()
        if profile is None:
            db.add(Profile(user_id=row.id, avatar_url=clean_avatar))
        elif profile.avatar_url != clean_avatar:
            profile.avatar_url = clean_avatar

    await db.commit()
    await db.refresh(row)
    return row


async def resolve_user_shadow_from_wp_identity(
    db: AsyncSession,
    *,
    wp_user_id: int | None,
    user_email: str | None,
    header_roles: list[str] | None = None,
) -> UserShadow:
    resolved: dict | None = None
    try:
        if wp_user_id is not None:
            resolved = await fetch_wp_user_by_id(int(wp_user_id))
        elif user_email:
            resolved = await fetch_wp_user_by_email((user_email or "").strip().lower())
        else:
            raise HTTPException(status_code=401, detail="Missing WordPress identity")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "WordPress identity lookup failed (wp_user_id=%s, email=%s)",
            wp_user_id,
            (user_email or "").strip().lower(),
        )
        raise HTTPException(status_code=502, detail="WordPress identity lookup failed") from exc

    if resolved is None:
        raise HTTPException(status_code=401, detail="Unable to resolve WordPress user")

    roles = [str(x).strip().lower() for x in (resolved.get("roles") or []) if str(x).strip()]
    if header_roles:
        for role in header_roles:
            if role and role not in roles:
                roles.append(role)
    return await upsert_user_shadow(
        db,
        wp_user_id=int(resolved["id"]),
        email=str(resolved.get("email") or "").strip().lower(),
        display_name=str(resolved.get("display_name") or "").strip(),
        roles=roles,
        avatar_url=str(resolved.get("avatar_url") or "").strip() or None,
    )


async def ensure_profile(db: AsyncSession, user_id: int) -> Profile:
    profile = (await db.execute(select(Profile).where(Profile.user_id == user_id))).scalar_one_or_none()
    if profile is None:
        profile = Profile(user_id=user_id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


async def ensure_privacy_settings(db: AsyncSession, user_id: int) -> PrivacySettings:
    row = (
        await db.execute(select(PrivacySettings).where(PrivacySettings.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        row = PrivacySettings(user_id=user_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row
