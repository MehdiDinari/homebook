from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="HomeBook API", alias="APP_NAME")
    app_env: Literal["dev", "staging", "prod"] = Field(default="dev", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    ws_prefix: str = Field(default="/ws", alias="WS_PREFIX")

    database_url: str = Field(
        default="postgresql+asyncpg://homebook:homebook@localhost:5432/homebook",
        alias="DATABASE_URL",
        validation_alias=AliasChoices("DATABASE_URL", "API_DB_URL"),
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    jwt_secret: str = Field(default="change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_issuer: str = Field(default="", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="", alias="JWT_AUDIENCE")
    jwt_exp_leeway_seconds: int = Field(default=30, alias="JWT_EXP_LEEWAY_SECONDS")

    wp_base_url: str = Field(default="", alias="WP_BASE_URL")
    wp_app_user: str = Field(default="", alias="WP_APP_USER")
    wp_app_password: str = Field(default="", alias="WP_APP_PASSWORD")
    wp_role_teacher: str = Field(default="prof", alias="WP_ROLE_TEACHER")
    wp_role_student: str = Field(default="student", alias="WP_ROLE_STUDENT")
    wp_table_prefix: str = Field(default="wp_", alias="WP_TABLE_PREFIX")
    allowed_cors_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_CORS_ORIGINS")
    allowed_cors_origin_regex: str = Field(
        default=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^https://([a-z0-9-]+\.)?mystagingwebsite\.com$",
        alias="ALLOWED_CORS_ORIGIN_REGEX",
    )

    s3_endpoint_url: str = Field(default="http://localhost:9000", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="minioadmin", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="minioadmin", alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="homebook-assets", alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")
    s3_presigned_expires_seconds: int = Field(default=900, alias="S3_PRESIGNED_EXPIRES_SECONDS")

    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3.1:8b-instruct", alias="OLLAMA_MODEL")
    ollama_timeout_seconds: int = Field(default=45, alias="OLLAMA_TIMEOUT_SECONDS")
    stripe_secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY")
    stripe_publishable_key: str = Field(default="", alias="STRIPE_PUBLISHABLE_KEY")
    stripe_webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    stripe_success_url: str = Field(default="", alias="STRIPE_SUCCESS_URL")
    stripe_cancel_url: str = Field(default="", alias="STRIPE_CANCEL_URL")
    paypal_client_id: str = Field(default="", alias="PAYPAL_CLIENT_ID")
    paypal_client_secret: str = Field(default="", alias="PAYPAL_CLIENT_SECRET")
    paypal_env: Literal["sandbox", "live"] = Field(default="sandbox", alias="PAYPAL_ENV")
    paypal_success_url: str = Field(default="", alias="PAYPAL_SUCCESS_URL")
    paypal_cancel_url: str = Field(default="", alias="PAYPAL_CANCEL_URL")
    teacher_revenue_share: float = Field(default=0.7, alias="TEACHER_REVENUE_SHARE")
    live_session_cleanup_minutes: int = Field(default=0, alias="LIVE_SESSION_CLEANUP_MINUTES")

    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    max_upload_size_mb: int = Field(default=8, alias="MAX_UPLOAD_SIZE_MB")

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_cors_origins.split(",") if x.strip()]

    @property
    def cors_origin_regex(self) -> str | None:
        value = self.allowed_cors_origin_regex.strip()
        return value or None

    @property
    def database_url_sync(self) -> str:
        if self.database_url.startswith("postgresql+asyncpg://"):
            return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
