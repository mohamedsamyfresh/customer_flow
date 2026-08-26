from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_dashboard_metrics_endpoint(client: AsyncClient):
    response = await client.get("/api/dashboard/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "people_in_store" in data
    assert "total_entries_today" in data
    assert "total_exits_today" in data
    assert "emotion_transitions" in data
    assert "longest_stay" in data
    assert "highest_occupancy_period" in data


@pytest.mark.asyncio
async def test_analytics_summary_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/analytics/summary")
    assert response.status_code == 200
    data = response.json()
    assert "people_in_store" in data


@pytest.mark.asyncio
async def test_analytics_occupancy_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/analytics/occupancy?bucket=15m")
    assert response.status_code == 200
    data = response.json()
    assert data["bucket"] == "15m"
    assert "timeline" in data
    assert len(data["timeline"]) > 0


@pytest.mark.asyncio
async def test_analytics_emotions_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/analytics/emotions")
    assert response.status_code == 200
    data = response.json()
    assert "natural_to_angry" in data
    assert "angry_to_natural" in data
    assert "natural_to_natural" in data
    assert "angry_to_angry" in data


@pytest.mark.asyncio
async def test_entries_list_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/entries?page=1&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "page" in data
    assert "limit" in data
    assert "data" in data


@pytest.mark.asyncio
async def test_waiting_times_list_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/waiting-times?page=1&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "data" in data


@pytest.mark.asyncio
async def test_create_websocket_ticket_endpoints(client: AsyncClient):
    # 1. Global ticket
    r1 = await client.post("/api/v1/dashboard/ws/ticket")
    assert r1.status_code == 201
    assert "ticket" in r1.json()
    assert len(r1.json()["ticket"]) >= 32

    # 2. Branch ticket
    r2 = await client.post("/api/v1/dashboard/branch-1/ws/ticket")
    assert r2.status_code == 201
    assert "ticket" in r2.json()

