from __future__ import annotations

import base64
from typing import Any

import httpx

from app.core.config import settings


def _auth_header() -> dict[str, str]:
    if not settings.wp_app_user or not settings.wp_app_password:
        return {}
    raw = f"{settings.wp_app_user}:{settings.wp_app_password}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "HomeBook/1.0",
    }
    headers.update(_auth_header())
    return headers


def _wp_users_url() -> str:
    return f"{settings.wp_base_url.rstrip('/')}/wp-json/wp/v2/users"


def _normalize_user(payload: dict[str, Any]) -> dict[str, Any]:
    roles = payload.get("roles") if isinstance(payload.get("roles"), list) else []
    avatar = ""
    avatar_urls = payload.get("avatar_urls")
    if isinstance(avatar_urls, dict):
        for key in ("96", "64", "48", "24"):
            candidate = avatar_urls.get(key)
            if isinstance(candidate, str) and candidate.strip():
                avatar = candidate.strip()
                break
    return {
        "id": int(payload.get("id")),
        "email": str(payload.get("email") or "").strip().lower(),
        "display_name": str(payload.get("name") or payload.get("slug") or "").strip(),
        "roles": [str(x).strip().lower() for x in roles if str(x).strip()],
        "avatar_url": avatar,
    }


async def fetch_wp_user_by_id(wp_user_id: int) -> dict[str, Any] | None:
    if not settings.wp_base_url:
        return None
    url = f"{_wp_users_url()}/{wp_user_id}"
    params = {"context": "edit"}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(url, params=params, headers=_headers())
        if res.status_code == 404:
            return None
        res.raise_for_status()
        payload = res.json()
    return _normalize_user(payload)


async def fetch_wp_user_by_email(email: str) -> dict[str, Any] | None:
    if not settings.wp_base_url:
        return None
    q = (email or "").strip()
    if not q:
        return None
    params = {"context": "edit", "search": q, "per_page": 20, "page": 1}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(_wp_users_url(), params=params, headers=_headers())
        if res.status_code == 404:
            return None
        res.raise_for_status()
        rows = res.json() or []
    if not isinstance(rows, list):
        return None
    lower_q = q.lower()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        found = str(raw.get("email") or "").strip().lower()
        if found and found == lower_q:
            return _normalize_user(raw)
    return None


async def fetch_wp_users_by_role(role: str, *, max_pages: int = 10, per_page: int = 100) -> list[dict[str, Any]]:
    if not settings.wp_base_url:
        return []

    page = 1
    role = role.strip()
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25) as client:
        while page <= max_pages:
            params = {
                "context": "edit",
                "per_page": per_page,
                "page": page,
            }
            if role:
                params["roles"] = role

            res = await client.get(_wp_users_url(), params=params, headers=_headers())
            if res.status_code == 400:
                break
            res.raise_for_status()
            rows = res.json() or []
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                if isinstance(r, dict) and r.get("id") is not None:
                    out.append(_normalize_user(r))

            total_pages = int(res.headers.get("X-WP-TotalPages") or page)
            if page >= total_pages:
                break
            page += 1
    return out
