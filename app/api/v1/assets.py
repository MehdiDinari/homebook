from __future__ import annotations

import base64
import mimetypes
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_user_shadow_by_wp_id
from app.core.config import settings
from app.db.session import get_db
from app.models.asset import Asset
from app.services.auth import AuthUser, get_current_user
from app.services.storage import (
    get_object_bytes,
    make_presigned_upload_url,
    make_public_url,
    put_object_bytes,
)

router = APIRouter(prefix="/assets", tags=["assets"])


class AssetPresignIn(BaseModel):
    filename: str
    media_type: str
    size_bytes: int


class AssetPresignOut(BaseModel):
    asset_id: int
    object_key: str
    upload_url: str
    public_url: str


class AssetUploadOut(BaseModel):
    asset_id: int
    object_key: str
    public_url: str


class AssetUploadBase64In(BaseModel):
    filename: str | None = None
    media_type: str
    data_base64: str


def _public_asset_url(asset_id: int) -> str:
    path = f"/wp-json/homebook/v1/proxy/api/v1/assets/{asset_id}/file"
    base = str(settings.wp_base_url or "").strip().rstrip("/")
    return (base + path) if base else path


def _store_asset_row(
    owner_wp_user_id: int,
    media_type: str,
    data: bytes,
) -> tuple[str, int, str]:
    size_bytes = len(data)
    if size_bytes <= 0:
        raise HTTPException(status_code=400, detail="Empty file")

    max_bytes = int(settings.max_upload_size_mb) * 1024 * 1024
    if size_bytes > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (max {settings.max_upload_size_mb} MB)")

    ext = mimetypes.guess_extension(media_type) or ""
    object_key = f"users/{owner_wp_user_id}/{uuid.uuid4().hex}{ext}"
    put_object_bytes(object_key=object_key, media_type=media_type, data=data)
    return object_key, size_bytes, media_type


@router.post("/presign", response_model=AssetPresignOut)
async def presign_upload(
    payload: AssetPresignIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> AssetPresignOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    if payload.size_bytes <= 0:
        raise HTTPException(status_code=400, detail="size_bytes must be > 0")

    ext = mimetypes.guess_extension(payload.media_type) or ""
    object_key = f"users/{me.wp_user_id}/{uuid.uuid4().hex}{ext}"

    upload_url = make_presigned_upload_url(object_key=object_key, media_type=payload.media_type)
    public_url = make_public_url(object_key)

    row = Asset(
        owner_user_id=me.id,
        object_key=object_key,
        public_url=public_url,
        media_type=payload.media_type,
        size_bytes=payload.size_bytes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Prefer serving assets through API/proxy URL to avoid mixed-content/internal-host issues.
    proxied_url = _public_asset_url(row.id)
    if proxied_url and proxied_url != row.public_url:
        row.public_url = proxied_url
        await db.commit()
        await db.refresh(row)

    return AssetPresignOut(
        asset_id=row.id,
        object_key=row.object_key,
        upload_url=upload_url,
        public_url=row.public_url,
    )


@router.post("/upload", response_model=AssetUploadOut)
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> AssetUploadOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    media_type = str(file.content_type or "application/octet-stream")
    data = await file.read()
    object_key, size_bytes, media_type = _store_asset_row(
        owner_wp_user_id=me.wp_user_id,
        media_type=media_type,
        data=data,
    )

    row = Asset(
        owner_user_id=me.id,
        object_key=object_key,
        public_url="pending",
        media_type=media_type,
        size_bytes=size_bytes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    row.public_url = _public_asset_url(row.id)
    await db.commit()
    await db.refresh(row)

    return AssetUploadOut(asset_id=row.id, object_key=row.object_key, public_url=row.public_url)


@router.post("/upload-base64", response_model=AssetUploadOut)
async def upload_file_base64(
    payload: AssetUploadBase64In,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> AssetUploadOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    media_type = str(payload.media_type or "application/octet-stream").strip() or "application/octet-stream"
    b64 = str(payload.data_base64 or "").strip()
    if not b64:
        raise HTTPException(status_code=400, detail="Empty data_base64")
    if "," in b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]

    try:
        data = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 payload") from exc

    object_key, size_bytes, media_type = _store_asset_row(
        owner_wp_user_id=me.wp_user_id,
        media_type=media_type,
        data=data,
    )

    row = Asset(
        owner_user_id=me.id,
        object_key=object_key,
        public_url="pending",
        media_type=media_type,
        size_bytes=size_bytes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    row.public_url = _public_asset_url(row.id)
    await db.commit()
    await db.refresh(row)

    return AssetUploadOut(asset_id=row.id, object_key=row.object_key, public_url=row.public_url)


@router.get("/{asset_id}/file")
async def get_asset_file(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: AuthUser = Depends(get_current_user),
) -> Response:
    row = (
        await db.execute(select(Asset).where(Asset.id == asset_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        data, media_type = get_object_bytes(row.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Asset binary not found") from exc

    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )
