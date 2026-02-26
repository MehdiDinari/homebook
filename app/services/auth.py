from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import resolve_user_shadow_from_wp_identity
from app.core.config import settings
from app.db.session import get_db


bearer = HTTPBearer(auto_error=False)


@dataclass(slots=True)
class AuthUser:
    wp_user_id: int
    email: str
    display_name: str
    roles: list[str]


def _decode_token(token: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "algorithms": [settings.jwt_algorithm],
        "leeway": settings.jwt_exp_leeway_seconds,
    }

    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    else:
        kwargs["options"] = {"verify_aud": False}

    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer

    try:
        payload = jwt.decode(token, settings.jwt_secret, **kwargs)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return payload


def _parse_payload(payload: dict[str, Any]) -> AuthUser:
    raw_id = payload.get("wp_user_id") or payload.get("sub")
    try:
        wp_user_id = int(raw_id)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid wp_user_id claim") from exc

    email = str(payload.get("email") or "").strip().lower()
    display_name = str(payload.get("display_name") or payload.get("name") or email or wp_user_id)
    roles = payload.get("roles") or []

    if not isinstance(roles, list):
        roles = []

    return AuthUser(
        wp_user_id=wp_user_id,
        email=email,
        display_name=display_name,
        roles=[str(r).strip().lower() for r in roles],
    )


async def _sync_user_shadow(db: AsyncSession, user: AuthUser) -> None:
    # WordPress remains the source of truth for user identity.
    await resolve_user_shadow_from_wp_identity(
        db,
        wp_user_id=user.wp_user_id,
        user_email=user.email,
        header_roles=user.roles,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    x_wp_user_id: int | None = Header(default=None, alias="X-WP-User-Id"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    x_wp_user_roles: str | None = Header(default=None, alias="X-WP-User-Roles"),
) -> AuthUser:
    if credentials is not None:
        payload = _decode_token(credentials.credentials)
        user = _parse_payload(payload)
        await _sync_user_shadow(db, user)
        return user

    if x_wp_user_id is not None or x_user_email:
        roles = [x.strip().lower() for x in (x_wp_user_roles or "").split(",") if x.strip()]
        row = await resolve_user_shadow_from_wp_identity(
            db,
            wp_user_id=x_wp_user_id,
            user_email=x_user_email,
            header_roles=roles,
        )
        return AuthUser(
            wp_user_id=row.wp_user_id,
            email=row.email,
            display_name=row.display_name,
            roles=[str(x).strip().lower() for x in (row.roles or [])],
        )

    raise HTTPException(status_code=401, detail="Missing bearer token or WordPress identity headers")


async def get_current_user_from_ws(websocket: WebSocket, db: AsyncSession) -> AuthUser:
    token = websocket.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    payload = _decode_token(token)
    user = _parse_payload(payload)
    await _sync_user_shadow(db, user)
    return user


def require_role(user: AuthUser, allowed: set[str]) -> None:
    if not allowed.intersection(set(user.roles)):
        raise HTTPException(status_code=403, detail="Forbidden")
