import base64
from types import SimpleNamespace

import httpx
import pytest

from kami_sim.api import server


@pytest.mark.asyncio
async def test_auth_status_is_public_and_protected_api_requires_token(monkeypatch):
    monkeypatch.setattr(server.config, "api_token", "correct-secret")
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        public = await client.get("/api/auth/status")
        rejected = await client.get("/api/status")
        accepted = await client.get(
            "/api/auth/status",
            headers={"Authorization": "Bearer correct-secret"},
        )

    assert public.status_code == 200
    assert public.json() == {"required": True, "authenticated": False}
    assert rejected.status_code == 401
    assert rejected.headers["www-authenticate"] == "Bearer"
    assert accepted.json() == {"required": True, "authenticated": True}


@pytest.mark.asyncio
async def test_api_accepts_bearer_and_x_api_key(monkeypatch):
    monkeypatch.setattr(server.config, "api_token", "correct-secret")
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bearer = await client.get(
            "/api/status", headers={"Authorization": "Bearer correct-secret"}
        )
        api_key = await client.get(
            "/api/status", headers={"X-API-Key": "correct-secret"}
        )

    assert bearer.status_code == 200
    assert api_key.status_code == 200


def test_websocket_token_uses_urlsafe_subprotocol(monkeypatch):
    monkeypatch.setattr(server.config, "api_token", "s3cret/value+")
    encoded = base64.urlsafe_b64encode(b"s3cret/value+").decode().rstrip("=")
    websocket = SimpleNamespace(
        headers={"sec-websocket-protocol": f"kami-auth, token.{encoded}"}
    )

    token = server._websocket_api_token(websocket)

    assert token == "s3cret/value+"
    assert server._is_valid_api_token(token)


def test_empty_configuration_keeps_local_development_open(monkeypatch):
    monkeypatch.setattr(server.config, "api_token", "")

    assert server._is_valid_api_token(None)
