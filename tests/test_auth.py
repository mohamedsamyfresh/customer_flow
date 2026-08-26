from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from freshfamily_auth.config import AuthConfig
from freshfamily_auth.jwks import JwksClient
from httpx import AsyncClient

from app.core.auth import auth
from app.main import app
from tests.conftest import StubTransport


@pytest.mark.asyncio
async def test_01_missing_authorization_header(unauthed_client: AsyncClient):
    """1. Missing Authorization header -> 401 Unauthorized"""
    response = await unauthed_client.get("/api/v1/entries")
    assert response.status_code == 401
    assert "detail" in response.json()
    assert "missing" in response.json()["detail"].lower() or "authorization" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_02_malformed_jwt(unauthed_client: AsyncClient):
    """2. Malformed JWT -> 401 Unauthorized"""
    headers = {"Authorization": "Bearer not.a.valid.jwt.token"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_03_expired_jwt(unauthed_client: AsyncClient, mint_token):
    """3. Expired JWT -> 401 Unauthorized"""
    token = mint_token(exp_seconds=-3600)  # Expired 1 hour ago
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_04_invalid_signature(unauthed_client: AsyncClient, mint_token, rsa_keypair2):
    """4. Invalid signature (signed by untrusted private key) -> 401 Unauthorized"""
    token = mint_token(private_key=rsa_keypair2["private"], kid="key-1")
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_05_unknown_kid(unauthed_client: AsyncClient, mint_token):
    """5. Unknown kid -> 401 Unauthorized"""
    token = mint_token(kid="unknown-kid-999")
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_06_valid_jwt_accessible(client: AsyncClient):
    """6. Valid JWT -> endpoint accessible (200 OK)"""
    response = await client.get("/api/v1/entries")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "data" in data


@pytest.mark.asyncio
async def test_07_wrong_role(unauthed_client: AsyncClient, mint_token):
    """7. Wrong role -> 403 Forbidden on role-protected endpoint / checks"""
    # Mint token with viewer role and lack of manager role
    token = mint_token(claims={"roles": ["viewer"], "permissions": []})
    headers = {"Authorization": f"Bearer {token}"}
    
    # Testing require_role directly via an ad-hoc or protected check
    from fastapi import Depends
    @app.get("/api/test-role-check", dependencies=[Depends(auth.require_role("superadmin"))])
    async def _test_role():
        return {"ok": True}

    response = await unauthed_client.get("/api/test-role-check", headers=headers)
    assert response.status_code == 403
    assert "missing role" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_08_missing_permission(unauthed_client: AsyncClient, mint_token):
    """8. Missing permission -> 403 Forbidden"""
    # User has only waiting_times:read, attempting to access /api/v1/entries (requires entries:read)
    token = mint_token(claims={"permissions": ["waiting_times:read"]})
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 403
    assert "missing permission" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_09_correct_permission(unauthed_client: AsyncClient, mint_token):
    """9. Correct permission -> 200 OK"""
    token = mint_token(claims={"permissions": ["entries:read"]})
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_10_issuer_mismatch(unauthed_client: AsyncClient, mint_token):
    """10. Issuer mismatch -> 401 Unauthorized"""
    token = mint_token(claims={"iss": "https://untrusted-issuer.com"})
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_11_audience_mismatch(unauthed_client: AsyncClient, mint_token):
    """11. Audience mismatch -> 401 Unauthorized"""
    token = mint_token(claims={"aud": "wrong-audience-id"})
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_12_missing_required_claim(unauthed_client: AsyncClient, rsa_keypair):
    """12. Missing required claim (e.g. missing 'sub' or 'exp') -> 401 Unauthorized"""
    import jwt
    now = datetime.now(tz=timezone.utc)
    # Payload without 'sub' claim
    payload = {
        "roles": ["admin"],
        "permissions": ["entries:read"],
        "iss": "https://idp.freshfamily.local",
        "aud": "customer-flow-backend",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    token = jwt.encode(payload, rsa_keypair["private"], algorithm="RS256", headers={"kid": "key-1"})
    headers = {"Authorization": f"Bearer {token}"}
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_13_key_rotation_retry(unauthed_client: AsyncClient, jwk, jwk2, rsa_keypair2, mint_token):
    """13. Key rotation retry: Signature fails on cached key, cache invalidates and fetches rotated key"""
    # Start with cache containing key-1
    transport = StubTransport([
        {"keys": [jwk]},   # Initial fetch (key-1)
        {"keys": [jwk2]},  # Refresh fetch after rotation (key-2)
    ])
    client = JwksClient("https://idp.freshfamily.local/.well-known/jwks", cache_ttl=3600.0)
    client._client = httpx.AsyncClient(transport=transport)
    auth._jwks_client = client

    # Mint token signed by key-2
    token2 = mint_token(private_key=rsa_keypair2["private"], kid="key-2")
    headers = {"Authorization": f"Bearer {token2}"}

    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_14_jwks_cache_behavior(unauthed_client: AsyncClient, jwk, mint_token):
    """14. JWKS cache behavior: Multiple requests reuse cached key without extra HTTP fetches"""
    transport = StubTransport([{"keys": [jwk]}])
    client = JwksClient("https://idp.freshfamily.local/.well-known/jwks", cache_ttl=3600.0)
    client._client = httpx.AsyncClient(transport=transport)
    auth._jwks_client = client

    token = mint_token(claims={"permissions": ["entries:read", "waiting_times:read"]})
    headers = {"Authorization": f"Bearer {token}"}

    # Request 1
    r1 = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert r1.status_code == 200

    # Request 2
    r2 = await unauthed_client.get("/api/v1/waiting-times", headers=headers)
    assert r2.status_code == 200

    # Only 1 HTTP call made to JWKS endpoint
    assert len(transport._calls) == 1


@pytest.mark.asyncio
async def test_15_jwks_outage_behavior(unauthed_client: AsyncClient, jwk, mint_token):
    """15. JWKS outage behavior: Stale-on-failure keeps cached key valid even if IDP is down (500)"""
    # First response succeeds, subsequent refresh returns 500 error
    transport = StubTransport([
        {"keys": [jwk]},
        httpx.HTTPStatusError("500 Server Error", request=None, response=httpx.Response(500)),
    ])
    # Cache TTL set very short to trigger refresh attempt
    client = JwksClient("https://idp.freshfamily.local/.well-known/jwks", cache_ttl=0.01)
    client._client = httpx.AsyncClient(transport=transport)
    auth._jwks_client = client

    token = mint_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Request 1 populates cache
    r1 = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert r1.status_code == 200

    # Wait for TTL to expire
    await asyncio.sleep(0.02)

    # Request 2 encounters refresh failure (500), but stale cache serves the key
    r2 = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_16_dev_bypass_in_development(unauthed_client: AsyncClient, mint_token):
    """16. Development bypass works in development: admin-dev role skips permission checks"""
    auth._config = AuthConfig(
        jwks_url="https://idp.freshfamily.local/.well-known/jwks",
        issuer="https://idp.freshfamily.local",
        audience="customer-flow-backend",
        app_env="development",
        dev_bypass_role="admin-dev",
    )

    # Token has admin-dev role but NO permissions
    token = mint_token(claims={"roles": ["admin-dev"], "permissions": []})
    headers = {"Authorization": f"Bearer {token}"}

    # Should succeed because dev bypass is active in development
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_17_dev_bypass_disabled_in_production(unauthed_client: AsyncClient, mint_token):
    """17. Development bypass NEVER activates in production"""
    auth._config = AuthConfig(
        jwks_url="https://idp.freshfamily.local/.well-known/jwks",
        issuer="https://idp.freshfamily.local",
        audience="customer-flow-backend",
        app_env="production",
        dev_bypass_role="admin-dev",
    )

    # Token has admin-dev role but NO permissions
    token = mint_token(claims={"roles": ["admin-dev"], "permissions": []})
    headers = {"Authorization": f"Bearer {token}"}

    # Must be 403 in production!
    response = await unauthed_client.get("/api/v1/entries", headers=headers)
    assert response.status_code == 403
    assert "missing permission" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_18_health_endpoint_public(unauthed_client: AsyncClient):
    """18. Health endpoint remains intentionally public and accessible without authentication"""
    response = await unauthed_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
