from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from freshfamily_auth.config import AuthConfig
from freshfamily_auth.jwks import JwksClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.auth import auth
from app.core.config import settings
from app.core.db import get_db
from app.main import app


class StubTransport(httpx.AsyncBaseTransport):
    """In-memory HTTP transport returning queued responses for JWKS mocking."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses) if responses else []
        self._calls: list[httpx.Request] = []

    def queue_response(self, response: Any) -> None:
        self._responses.append(response)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._calls.append(request)
        if not self._responses:
            return httpx.Response(500, json={"error": "No more responses queued in StubTransport"})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return httpx.Response(200, json=response)


@pytest.fixture(scope="session")
def rsa_keypair():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()
    return {"private": private_key, "public": public_key}


@pytest.fixture(scope="session")
def rsa_keypair2():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()
    return {"private": private_key, "public": public_key}


@pytest.fixture(scope="session")
def public_key(rsa_keypair):
    return rsa_keypair["public"]


@pytest.fixture(scope="session")
def public_pem(rsa_keypair):
    return (
        rsa_keypair["public"]
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


@pytest.fixture
def jwk(rsa_keypair):
    key = RSAAlgorithm.to_jwk(rsa_keypair["public"], as_dict=True)
    key["kid"] = "key-1"
    return key


@pytest.fixture
def jwk2(rsa_keypair2):
    key = RSAAlgorithm.to_jwk(rsa_keypair2["public"], as_dict=True)
    key["kid"] = "key-2"
    return key


@pytest.fixture
def mint_token(rsa_keypair):
    def _mint(
        claims: dict[str, Any] | None = None,
        *,
        kid: str = "key-1",
        exp_seconds: int = 300,
        private_key=None,
    ) -> str:
        now = datetime.now(tz=timezone.utc)
        payload = {
            "sub": "emp-100",
            "roles": ["admin", "manager"],
            "permissions": ["entries:read", "waiting_times:read", "analytics:read", "admin:all"],
            "iss": "https://idp.freshfamily.local",
            "aud": "customer-flow-backend",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=exp_seconds)).timestamp()),
        }
        if claims:
            payload.update(claims)
        key_to_use = private_key if private_key is not None else rsa_keypair["private"]
        headers = {"kid": kid} if kid is not None else {}
        return jwt.encode(payload, key_to_use, algorithm="RS256", headers=headers)

    return _mint


@pytest.fixture(autouse=True)
def configure_auth_mock(jwk):
    """
    Auto-configure AuthKit with an in-memory JwksClient so tests don't make real network calls.
    """
    transport = StubTransport([{"keys": [jwk]}])
    client = JwksClient("https://idp.freshfamily.local/.well-known/jwks", cache_ttl=3600.0)
    client._client = httpx.AsyncClient(transport=transport)
    auth._jwks_client = client
    auth._config = AuthConfig(
        jwks_url="https://idp.freshfamily.local/.well-known/jwks",
        issuer="https://idp.freshfamily.local",
        audience="customer-flow-backend",
        algorithms=("RS256",),
        app_env="production",
        dev_bypass_role="admin-dev",
        leeway=30.0,
    )
    yield transport
    auth._jwks_client = None


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_sessionmaker(test_engine):
    return async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@pytest_asyncio.fixture
async def db_session(test_sessionmaker):
    async with test_sessionmaker() as session:
        # Clean test tables
        await session.execute(text("DELETE FROM entries"))
        await session.execute(text("DELETE FROM waiting_times"))
        await session.commit()
        yield session
        await session.execute(text("DELETE FROM entries"))
        await session.execute(text("DELETE FROM waiting_times"))
        await session.commit()


@pytest_asyncio.fixture
async def client(db_session, test_sessionmaker, mint_token):
    """
    Standard authenticated test client.
    Default requests carry valid token with full permissions unless headers overridden.
    """
    async def _override_get_db():
        async with test_sessionmaker() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    token = mint_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauthed_client(db_session, test_sessionmaker):
    """
    Unauthenticated test client (no Authorization header).
    """
    async def _override_get_db():
        async with test_sessionmaker() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
