from __future__ import annotations

import json
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    GRPC_HOST: str = "grpc-server"
    GRPC_PORT: int = 50051

    # Redis and WebSocket streaming configuration
    REDIS_URL: str = Field(default="redis://redis:6379/0", description="Redis connection URL")
    REDIS_KEY_NAMESPACE: str = Field(default="customer_flow", description="Redis key/channel namespace")
    WEBSOCKET_TICKET_TTL_SECONDS: int = Field(default=30, description="WebSocket ticket TTL in seconds")
    WEBSOCKET_ALLOWED_ORIGINS: list[str] | str = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
        description="Allowed origins for WebSocket handshake",
    )

    @field_validator("WEBSOCKET_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    return json.loads(value)
                except Exception:
                    pass
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        return value

    # Shared freshfamily-auth configuration
    JWKS_URL: str | None = Field(default=None, description="JWKS endpoint URL for token verification")
    AUTH_ISSUER: str | None = Field(default=None, description="Expected JWT issuer (iss claim)")
    AUTH_AUDIENCE: str | None = Field(default=None, description="Expected JWT audience (aud claim)")
    AUTH_ALGORITHMS: tuple[str, ...] = Field(default=("RS256",), description="Allowed JWT signing algorithms")
    AUTH_CACHE_TTL_SECONDS: float = Field(default=3600.0, description="JWKS cache TTL in seconds")
    AUTH_FETCH_TIMEOUT_SECONDS: float = Field(default=5.0, description="JWKS HTTP fetch timeout in seconds")
    AUTH_ROTATION_RETRY: bool = Field(default=True, description="Retry fetch once on signature verification failure")
    AUTH_APP_ENV: str = Field(default="production", description="Environment: 'production' or 'development'")
    AUTH_DEV_BYPASS_ROLE: str | None = Field(default="admin-dev", description="Role for dev bypass in development env")
    AUTH_LEEWAY: float = Field(default=30.0, description="Clock-skew tolerance in seconds (default 30.0 for prod)")
    AUTH_PUBLIC_KEY_PEM: str | None = Field(default=None, description="Static PEM public key for tests/sync only")
    AUTH_REQUIRED_CLAIMS: tuple[str, ...] = Field(default=("sub", "exp", "iat"), description="Required claims in JWT")
    AUTH_SUB_CLAIM: str = Field(default="sub", description="Subject claim name")
    AUTH_ROLES_CLAIM: str = Field(default="roles", description="Roles claim name")
    AUTH_PERMISSIONS_CLAIM: str = Field(default="permissions", description="Permissions claim name")

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # Properties satisfying freshfamily_auth.config.AuthConfigProtocol
    @property
    def jwks_url(self) -> str | None:
        return self.JWKS_URL

    @property
    def issuer(self) -> str | None:
        return self.AUTH_ISSUER

    @property
    def audience(self) -> str | None:
        return self.AUTH_AUDIENCE

    @property
    def algorithms(self) -> tuple[str, ...]:
        return self.AUTH_ALGORITHMS

    @property
    def cache_ttl_seconds(self) -> float:
        return self.AUTH_CACHE_TTL_SECONDS

    @property
    def fetch_timeout_seconds(self) -> float:
        return self.AUTH_FETCH_TIMEOUT_SECONDS

    @property
    def public_key_pem(self) -> str | None:
        return self.AUTH_PUBLIC_KEY_PEM

    @property
    def rotation_retry(self) -> bool:
        return self.AUTH_ROTATION_RETRY

    @property
    def app_env(self) -> str | None:
        return self.AUTH_APP_ENV

    @property
    def dev_bypass_role(self) -> str | None:
        return self.AUTH_DEV_BYPASS_ROLE

    @property
    def required_claims(self) -> tuple[str, ...]:
        return self.AUTH_REQUIRED_CLAIMS

    @property
    def leeway(self) -> float:
        return self.AUTH_LEEWAY

    @property
    def sub_claim(self) -> str:
        return self.AUTH_SUB_CLAIM

    @property
    def roles_claim(self) -> str:
        return self.AUTH_ROLES_CLAIM

    @property
    def permissions_claim(self) -> str:
        return self.AUTH_PERMISSIONS_CLAIM


settings = Settings()