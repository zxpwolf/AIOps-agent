"""Tests for the AIOps Agent Web Server.

Covers all HTTP endpoints: health, ready, chat, skills, index.
Uses aiohttp.test_utils.TestServer + TestClient with mocked orchestrator.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aiops_agent.models.schemas import AgentResponse, SkillDefinition
from aiops_agent.web.server import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_app(mock_orchestrator: AsyncMock):
    """Create a test application with the orchestrator monkey-patched."""
    app = create_app()
    # Inject mock orchestrator into the module-level helper
    import aiops_agent.web.server as server_mod

    async def _fake_get_orchestrator(app_obj):
        return mock_orchestrator

    server_mod._get_orchestrator = _fake_get_orchestrator
    return app


@pytest.fixture
def mock_orchestrator():
    """Provide a fresh AsyncMock orchestrator."""
    return AsyncMock()


@pytest.fixture
async def client(mock_orchestrator):
    """Provide an aiohttp TestClient with mocked orchestrator."""
    app = _make_test_app(mock_orchestrator)
    async with TestClient(TestServer(app)) as tc:
        yield tc, mock_orchestrator


# ---------------------------------------------------------------------------
# Health & Ready
# ---------------------------------------------------------------------------


async def test_health(client):
    """GET /health returns {"status": "healthy"}."""
    tc, _ = client
    async with tc.get("/health") as resp:
        assert resp.status == 200
        data = await resp.json()
        assert data == {"status": "healthy"}


async def test_ready(client):
    """GET /ready returns {"status": "ready"}."""
    tc, _ = client
    async with tc.get("/ready") as resp:
        assert resp.status == 200
        data = await resp.json()
        assert data == {"status": "ready"}


# ---------------------------------------------------------------------------
# Chat — happy path
# ---------------------------------------------------------------------------


async def test_chat_valid_message(client):
    """POST /api/chat with valid message returns response."""
    tc, orch = client
    orch.process_request.return_value = AgentResponse(
        success=True,
        message="Hello back!",
        data={"key": "value"},
        error_code=None,
        suggestion=None,
        trace_id="trace-123",
    )

    async with tc.post(
        "/api/chat",
        json={"message": "Hello", "session_id": "sess-1", "user_id": "user-1"},
    ) as resp:
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["message"] == "Hello back!"
        assert data["data"] == {"key": "value"}
        assert data["session_id"] == "sess-1"

    orch.process_request.assert_awaited_once()


# ---------------------------------------------------------------------------
# Chat — error cases
# ---------------------------------------------------------------------------


async def test_chat_empty_message(client):
    """POST /api/chat with empty message returns 400."""
    tc, _ = client
    async with tc.post("/api/chat", json={"message": "   "}) as resp:
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data


async def test_chat_invalid_json(client):
    """POST /api/chat with invalid JSON returns 400."""
    tc, _ = client
    async with tc.post(
        "/api/chat",
        data="not json at all",
        headers={"Content-Type": "application/json"},
    ) as resp:
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data


async def test_chat_missing_message_field(client):
    """POST /api/chat with missing message field returns 400."""
    tc, _ = client
    async with tc.post("/api/chat", json={"other_field": "value"}) as resp:
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data


async def test_chat_orchestrator_exception(client):
    """POST /api/chat with orchestrator exception returns 500."""
    tc, orch = client
    orch.process_request.side_effect = RuntimeError("Backend is down")

    async with tc.post("/api/chat", json={"message": "test"}) as resp:
        assert resp.status == 500
        data = await resp.json()
        assert data["success"] is False
        assert "Backend is down" in data["message"]
        assert data["error_code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------


async def test_skills_returns_skill_list(client):
    """GET /api/skills returns skill list."""
    tc, orch = client
    skills = [
        SkillDefinition(
            skill_name="monitor",
            description="Monitor systems",
            version="1.0.0",
            capabilities=["monitoring"],
            author="test",
            category="监控诊断",
            icon="🔍",
            tags=["ops"],
            install_count=42,
            rating=4.5,
            updated_at="2025-01-01T00:00:00Z",
            readme="# Monitor",
        ),
    ]
    # _skill_registry must be a real mock, not an AsyncMock, so list_skills
    # returns a value directly instead of a coroutine.
    mock_registry = MagicMock()
    mock_registry.list_skills.return_value = skills
    orch._skill_registry = mock_registry

    async with tc.get("/api/skills") as resp:
        assert resp.status == 200
        data = await resp.json()
        assert "skills" in data
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "monitor"
        assert data["skills"][0]["description"] == "Monitor systems"
        assert data["skills"][0]["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


async def test_index_returns_plaintext(client):
    """GET / returns index page or plaintext when no static file exists."""
    tc, _ = client
    async with tc.get("/") as resp:
        assert resp.status == 200
        text = await resp.text()
        # Either an HTML page or the fallback plaintext
        assert len(text) > 0
