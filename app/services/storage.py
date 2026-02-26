from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings


s3_client = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint_url,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key,
    region_name=settings.s3_region,
    config=Config(signature_version="s3v4"),
)

_bucket_checked = False


def ensure_bucket() -> None:
    global _bucket_checked
    if _bucket_checked:
        return

    try:
        s3_client.head_bucket(Bucket=settings.s3_bucket)
        _bucket_checked = True
        return
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        if code not in {"404", "nosuchbucket", "notfound"}:
            raise

    create_args = {"Bucket": settings.s3_bucket}
    region = str(settings.s3_region or "").strip()
    if region and region != "us-east-1":
        create_args["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3_client.create_bucket(**create_args)
    _bucket_checked = True


def make_presigned_upload_url(object_key: str, media_type: str) -> str:
    ensure_bucket()
    return s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": object_key, "ContentType": media_type},
        ExpiresIn=settings.s3_presigned_expires_seconds,
    )


def make_public_url(object_key: str) -> str:
    base = settings.s3_endpoint_url.rstrip("/")
    return f"{base}/{settings.s3_bucket}/{object_key}"


def put_object_bytes(object_key: str, media_type: str, data: bytes) -> None:
    ensure_bucket()
    s3_client.put_object(
        Bucket=settings.s3_bucket,
        Key=object_key,
        Body=data,
        ContentType=media_type,
    )


def get_object_bytes(object_key: str) -> tuple[bytes, str]:
    ensure_bucket()
    try:
        obj = s3_client.get_object(Bucket=settings.s3_bucket, Key=object_key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        if code in {"nosuchkey", "404", "notfound"}:
            raise FileNotFoundError(object_key) from exc
        raise

    body = obj["Body"].read()
    media_type = str(obj.get("ContentType") or "application/octet-stream")
    return body, media_type
