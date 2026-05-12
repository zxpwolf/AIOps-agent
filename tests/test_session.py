"""Tests for SessionStore — 会话状态管理."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from aiops_agent.context.session import SessionStore
from aiops_agent.models.schemas import InteractionMode, SessionState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """SessionStore backed by a temporary directory with short TTL for testing."""
    return SessionStore(persist_dir=str(tmp_path / "sessions"), ttl_minutes=30)


@pytest.fixture
def store_short_ttl(tmp_path: Path) -> SessionStore:
    """SessionStore with a 5-minute TTL for idle-check tests."""
    return SessionStore(persist_dir=str(tmp_path / "sessions"), ttl_minutes=5)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreate:
    """Tests for session creation."""

    async def test_create_initial_state(self, store: SessionStore) -> None:
        """New session has correct initial state."""
        session = await store.create("sess-1", "user-1")

        assert session.session_id == "sess-1"
        assert session.user_id == "user-1"
        assert session.mode == InteractionMode.CHAT
        assert session.created_at is not None
        assert session.last_active_at is not None
        assert session.ttl_minutes == 30

    async def test_create_timestamps_are_utc(self, store: SessionStore) -> None:
        """Created session timestamps are timezone-aware UTC."""
        session = await store.create("sess-1", "user-1")
        assert session.created_at.tzinfo == timezone.utc
        assert session.last_active_at.tzinfo == timezone.utc

    async def test_create_stores_in_memory(self, store: SessionStore) -> None:
        """Created session is accessible in memory."""
        await store.create("sess-1", "user-1")
        assert "sess-1" in store._sessions

    async def test_create_overwrites_existing(self, store: SessionStore) -> None:
        """Creating a session with an existing ID overwrites it."""
        s1 = await store.create("sess-1", "user-1")
        s2 = await store.create("sess-1", "user-2")
        assert s2.user_id == "user-2"
        assert store._sessions["sess-1"].user_id == "user-2"


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGet:
    """Tests for retrieving sessions."""

    async def test_get_memory_hit(self, store: SessionStore) -> None:
        """Getting a session that exists in memory returns it."""
        await store.create("sess-1", "user-1")
        session = await store.get("sess-1")
        assert session is not None
        assert session.session_id == "sess-1"

    async def test_get_restore_from_file(self, store: SessionStore) -> None:
        """Getting a session not in memory restores it from file."""
        # Create and persist
        session = await store.create("sess-1", "user-1")
        await store.persist("sess-1")

        # Remove from memory
        store._sessions.clear()

        # Get should restore from file
        restored = await store.get("sess-1")
        assert restored is not None
        assert restored.session_id == "sess-1"
        assert restored.user_id == "user-1"

    async def test_get_updates_last_active_at(self, store: SessionStore) -> None:
        """Getting a session updates its last_active_at timestamp."""
        session = await store.create("sess-1", "user-1")
        original_active = session.last_active_at

        # Wait a tiny bit (use a simulated old timestamp)
        session.last_active_at = datetime.now(timezone.utc) - timedelta(hours=1)

        retrieved = await store.get("sess-1")
        assert retrieved is not None
        assert retrieved.last_active_at > original_active
        # Should be very recent (within a few seconds)
        delta = datetime.now(timezone.utc) - retrieved.last_active_at
        assert delta < timedelta(seconds=5)

    async def test_get_nonexistent_returns_none(self, store: SessionStore) -> None:
        """Getting a session that doesn't exist returns None."""
        result = await store.get("no-such-session")
        assert result is None

    async def test_get_restore_puts_back_in_memory(
        self, store: SessionStore
    ) -> None:
        """Restoring a session from file puts it back in the in-memory cache."""
        await store.create("sess-1", "user-1")
        await store.persist("sess-1")
        store._sessions.clear()

        assert "sess-1" not in store._sessions
        await store.get("sess-1")
        assert "sess-1" in store._sessions


# ---------------------------------------------------------------------------
# Get or Create
# ---------------------------------------------------------------------------


class TestGetOrCreate:
    """Tests for get_or_create logic."""

    async def test_get_or_create_existing(self, store: SessionStore) -> None:
        """When session exists, returns it without creating a new one."""
        original = await store.create("sess-1", "user-1")
        result = await store.get_or_create("sess-1", "user-2")
        assert result.session_id == "sess-1"
        assert result.user_id == "user-1"  # original user, not the new one

    async def test_get_or_create_new(self, store: SessionStore) -> None:
        """When session doesn't exist, creates a new one."""
        result = await store.get_or_create("new-sess", "user-1")
        assert result.session_id == "new-sess"
        assert result.user_id == "user-1"
        assert result.mode == InteractionMode.CHAT

    async def test_get_or_create_stores_in_memory(
        self, store: SessionStore
    ) -> None:
        """get_or_create puts the session in memory for both branches."""
        # Existing branch
        await store.create("sess-1", "user-1")
        await store.get_or_create("sess-1", "user-1")
        assert "sess-1" in store._sessions

        # New branch
        await store.get_or_create("sess-2", "user-2")
        assert "sess-2" in store._sessions


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------


class TestPersist:
    """Tests for session persistence."""

    async def test_persist_writes_json(self, store: SessionStore) -> None:
        """Persist writes a JSON file with model_dump(mode="json") data."""
        await store.create("sess-1", "user-1")
        await store.persist("sess-1")

        file_path = store._persist_dir / "sess-1.json"
        assert file_path.exists()

        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["session_id"] == "sess-1"
        assert data["user_id"] == "user-1"
        assert data["mode"] == "chat"

    async def test_persist_uses_model_dump_json(self, store: SessionStore) -> None:
        """Persist uses model_dump(mode="json") for serialization."""
        await store.create("sess-1", "user-1")
        await store.persist("sess-1")

        file_path = store._persist_dir / "sess-1.json"
        data = json.loads(file_path.read_text(encoding="utf-8"))
        # model_dump(mode="json") converts enums to their values
        assert isinstance(data["mode"], str)
        assert data["mode"] == "chat"

    async def test_persist_oserror_is_noop(self, store: SessionStore) -> None:
        """OSError during persist is caught and logged, no exception raised."""
        await store.create("sess-1", "user-1")

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            await store.persist("sess-1")  # should not raise

    async def test_persist_missing_session_is_noop(
        self, store: SessionStore
    ) -> None:
        """Persisting a session that doesn't exist does nothing (no-op)."""
        await store.persist("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


class TestRemove:
    """Tests for session removal."""

    async def test_remove_from_memory(self, store: SessionStore) -> None:
        """Remove deletes the session from in-memory store."""
        await store.create("sess-1", "user-1")
        await store.remove("sess-1")
        assert "sess-1" not in store._sessions

    async def test_remove_deletes_file(self, store: SessionStore) -> None:
        """Remove deletes the session's JSON file."""
        await store.create("sess-1", "user-1")
        await store.persist("sess-1")

        file_path = store._persist_dir / "sess-1.json"
        assert file_path.exists()

        await store.remove("sess-1")
        assert not file_path.exists()

    async def test_remove_memory_and_file(self, store: SessionStore) -> None:
        """Remove clears both memory and file."""
        await store.create("sess-1", "user-1")
        await store.persist("sess-1")

        await store.remove("sess-1")
        assert "sess-1" not in store._sessions
        assert not (store._persist_dir / "sess-1.json").exists()

    async def test_remove_nonexistent_is_noop(self, store: SessionStore) -> None:
        """Removing a non-existent session does not raise."""
        await store.remove("no-such-session")  # should not raise

    async def test_remove_memory_only_no_file(self, store: SessionStore) -> None:
        """Remove works when session is in memory but not yet persisted."""
        await store.create("sess-1", "user-1")
        await store.remove("sess-1")
        assert "sess-1" not in store._sessions


# ---------------------------------------------------------------------------
# Idle check
# ---------------------------------------------------------------------------


class TestIdleSessions:
    """Tests for idle session detection and eviction."""

    async def test_idle_sessions_detected_and_evicted(
        self, store_short_ttl: SessionStore,
    ) -> None:
        """Sessions idle beyond TTL are returned and evicted from memory."""
        store = store_short_ttl

        # Create a session and backdate its last_active_at
        session = await store.create("sess-1", "user-1")
        session.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        # TTL is 5 minutes, so 10 minutes idle > TTL

        idle = await store.check_idle_sessions()
        assert "sess-1" in idle
        assert "sess-1" not in store._sessions

    async def test_idle_session_is_persisted(
        self, store_short_ttl: SessionStore,
    ) -> None:
        """Idle sessions are persisted before eviction."""
        store = store_short_ttl

        session = await store.create("sess-1", "user-1")
        session.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        await store.check_idle_sessions()
        file_path = store._persist_dir / "sess-1.json"
        assert file_path.exists()

    async def test_exact_ttl_boundary_not_idle(
        self, store_short_ttl: SessionStore,
    ) -> None:
        """A session idle for exactly TTL minutes is NOT considered idle.

        The implementation uses strict greater-than (idle_time > TTL), so
        we test with a time just under the boundary to avoid microsecond drift.
        """
        store = store_short_ttl

        session = await store.create("sess-1", "user-1")
        # Set to just under TTL boundary (4 min 59 sec) to ensure
        # we stay strictly below the > TTL check even with microsecond drift
        session.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=4, seconds=59)

        idle = await store.check_idle_sessions()
        assert "sess-1" not in idle
        # Session should still be in memory
        assert "sess-1" in store._sessions

    async def test_active_sessions_not_evicted(
        self, store_short_ttl: SessionStore,
    ) -> None:
        """Recently active sessions are not returned or evicted."""
        store = store_short_ttl

        await store.create("sess-1", "user-1")
        # last_active_at is now, well within TTL

        idle = await store.check_idle_sessions()
        assert idle == []
        assert "sess-1" in store._sessions

    async def test_mixed_idle_and_active(
        self, store_short_ttl: SessionStore,
    ) -> None:
        """Only idle sessions are evicted, active ones remain."""
        store = store_short_ttl

        # Idle session
        s1 = await store.create("sess-1", "user-1")
        s1.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        # Active session
        await store.create("sess-2", "user-2")

        idle = await store.check_idle_sessions()
        assert "sess-1" in idle
        assert "sess-2" not in idle
        assert "sess-2" in store._sessions
        assert "sess-1" not in store._sessions

    async def test_empty_store_idle_check(
        self, store_short_ttl: SessionStore,
    ) -> None:
        """Idle check on empty store returns empty list."""
        idle = await store_short_ttl.check_idle_sessions()
        assert idle == []


# ---------------------------------------------------------------------------
# Restore (internal)
# ---------------------------------------------------------------------------


class TestRestore:
    """Tests for the internal _restore method."""

    async def test_restore_json_decode_error_returns_none(
        self, store: SessionStore,
    ) -> None:
        """Corrupt JSON file causes _restore to return None."""
        file_path = store._persist_dir / "sess-bad.json"
        file_path.write_text("not valid json {{{", encoding="utf-8")

        result = await store._restore("sess-bad")
        assert result is None

    async def test_restore_file_not_found_returns_none(
        self, store: SessionStore,
    ) -> None:
        """Missing file causes _restore to return None."""
        result = await store._restore("nonexistent-session")
        assert result is None

    async def test_restore_valid_file(self, store: SessionStore) -> None:
        """Valid JSON file is restored correctly."""
        # Create, persist, then restore via _restore
        await store.create("sess-1", "user-1")
        await store.persist("sess-1")

        # Clear memory so _restore must read from file
        store._sessions.clear()

        restored = await store._restore("sess-1")
        assert restored is not None
        assert restored.session_id == "sess-1"
        assert restored.user_id == "user-1"
        assert isinstance(restored, SessionState)

    async def test_restore_invalid_json_raises_no_exception(
        self, store: SessionStore,
    ) -> None:
        """Restore handles ValueError (e.g. pydantic validation error) gracefully."""
        file_path = store._persist_dir / "sess-bad.json"
        # Valid JSON but missing required fields for SessionState
        file_path.write_text(json.dumps({"garbage": "data"}), encoding="utf-8")

        result = await store._restore("sess-bad")
        assert result is None


# ---------------------------------------------------------------------------
# Init — directory creation
# ---------------------------------------------------------------------------


class TestSessionInit:
    """Tests for SessionStore initialization."""

    def test_creates_directory(self, tmp_path: Path) -> None:
        """SessionStore creates the persist directory if it doesn't exist."""
        new_dir = tmp_path / "new_sessions"
        assert not new_dir.exists()

        SessionStore(persist_dir=str(new_dir))
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_existing_directory_ok(self, tmp_path: Path) -> None:
        """SessionStore does not raise if directory already exists."""
        existing = tmp_path / "existing_sessions"
        existing.mkdir()

        store = SessionStore(persist_dir=str(existing))
        assert store._persist_dir == existing

    def test_nested_directory_creation(self, tmp_path: Path) -> None:
        """SessionStore creates nested directories."""
        nested = tmp_path / "a" / "b" / "c" / "sessions"
        assert not nested.exists()

        SessionStore(persist_dir=str(nested))
        assert nested.exists()
        assert nested.is_dir()
