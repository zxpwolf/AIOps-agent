"""Tests for MemoryLayer — 短期记忆和长期记忆."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from aiops_agent.context.memory import MemoryLayer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory(tmp_path: Path) -> MemoryLayer:
    """MemoryLayer backed by a temporary directory."""
    return MemoryLayer(long_term_dir=str(tmp_path / "long_term"))


# ---------------------------------------------------------------------------
# Short-term memory tests
# ---------------------------------------------------------------------------


class TestShortTermMemory:
    """Tests for short-term (in-memory) session storage."""

    async def test_store_and_get(self, memory: MemoryLayer) -> None:
        """Storing data for a session and retrieving it returns the same data."""
        await memory.store_short_term("sess-1", {"role": "user", "content": "hello"})
        await memory.store_short_term("sess-1", {"role": "assistant", "content": "hi"})

        result = await memory.get_short_term("sess-1")
        assert len(result) == 2
        assert result[0]["content"] == "hello"
        assert result[1]["content"] == "hi"

    async def test_get_nonexistent_session(self, memory: MemoryLayer) -> None:
        """Getting short-term memory for an unknown session returns empty list."""
        result = await memory.get_short_term("nonexistent")
        assert result == []

    async def test_clear(self, memory: MemoryLayer) -> None:
        """Clearing a session's memory removes all its entries."""
        await memory.store_short_term("sess-1", {"content": "data"})
        await memory.clear_short_term("sess-1")
        assert await memory.get_short_term("sess-1") == []

    async def test_clear_nonexistent_session(self, memory: MemoryLayer) -> None:
        """Clearing a non-existent session does not raise."""
        await memory.clear_short_term("no-such-session")  # no-op

    async def test_session_isolation(self, memory: MemoryLayer) -> None:
        """Short-term memory is isolated between different sessions."""
        await memory.store_short_term("sess-A", {"content": "A-only"})
        await memory.store_short_term("sess-B", {"content": "B-only"})

        assert len(await memory.get_short_term("sess-A")) == 1
        assert (await memory.get_short_term("sess-A"))[0]["content"] == "A-only"
        assert len(await memory.get_short_term("sess-B")) == 1
        assert (await memory.get_short_term("sess-B"))[0]["content"] == "B-only"

    async def test_clear_does_not_affect_other_sessions(
        self, memory: MemoryLayer
    ) -> None:
        """Clearing one session's memory does not affect other sessions."""
        await memory.store_short_term("sess-A", {"content": "A"})
        await memory.store_short_term("sess-B", {"content": "B"})

        await memory.clear_short_term("sess-A")
        assert await memory.get_short_term("sess-A") == []
        assert (await memory.get_short_term("sess-B"))[0]["content"] == "B"


# ---------------------------------------------------------------------------
# Long-term memory — store
# ---------------------------------------------------------------------------


class TestLongTermStore:
    """Tests for storing cases in long-term (persistent) memory."""

    async def test_store_writes_json_file(self, memory: MemoryLayer) -> None:
        """store_long_term writes a JSON file to the long-term directory."""
        case = {
            "case_id": "case-001",
            "title": "CPU spike",
            "description": "CPU usage reached 95%",
            "resolution": "Restarted service",
            "tags": ["cpu", "performance"],
        }
        await memory.store_long_term(case)

        file_path = Path(memory._long_term_dir) / "case-001.json"
        assert file_path.exists()
        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["title"] == "CPU spike"
        assert data["resolution"] == "Restarted service"

    async def test_store_adds_stored_at_timestamp(self, memory: MemoryLayer) -> None:
        """store_long_term adds a stored_at ISO timestamp to the case."""
        case = {"case_id": "ts-test", "title": "Timestamp test"}
        await memory.store_long_term(case)

        file_path = Path(memory._long_term_dir) / "ts-test.json"
        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert "stored_at" in data
        # Should be a valid ISO 8601 string
        from datetime import datetime
        datetime.fromisoformat(data["stored_at"])

    async def test_store_defaults_case_id_to_uuid(self, memory: MemoryLayer) -> None:
        """When case_id is missing, a default based on index length is used for the filename."""
        case = {"title": "No ID case"}
        await memory.store_long_term(case)

        # The default is f"case_{len(self._long_term_index)}" = "case_0"
        # Note: the code uses this as a filename but does NOT inject case_id into the case dict.
        file_path = Path(memory._long_term_dir) / "case_0.json"
        assert file_path.exists()
        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["title"] == "No ID case"

    async def test_store_handles_oserror(self, memory: MemoryLayer) -> None:
        """OSError during file write is caught and logged, no exception raised."""
        case = {"case_id": "fail-case", "title": "Will fail"}

        # Patch write_text to raise OSError
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            await memory.store_long_term(case)  # should not raise

        # The in-memory index should NOT be updated (file write failed)
        assert all(c.get("case_id") != "fail-case" for c in memory._long_term_index)


# ---------------------------------------------------------------------------
# Long-term memory — search
# ---------------------------------------------------------------------------


class TestLongTermSearch:
    """Tests for keyword-based long-term memory search."""

    async def _seed_cases(self, memory: MemoryLayer) -> None:
        """Insert sample cases for search tests."""
        cases = [
            {
                "case_id": "cpu-001",
                "title": "CPU spike on production",
                "description": "High CPU usage detected on web servers",
                "tags": ["cpu", "performance", "production"],
            },
            {
                "case_id": "disk-002",
                "title": "Disk space exhaustion",
                "description": "Root partition filled up due to log rotation failure",
                "tags": ["disk", "storage", "logs"],
            },
            {
                "case_id": "net-003",
                "title": "Network latency increase",
                "description": "ECS instances experiencing high network latency",
                "tags": ["network", "ecs", "latency"],
            },
            {
                "case_id": "mem-004",
                "title": "Memory leak in Java service",
                "description": "Heap memory continuously growing, OOM kills",
                "tags": ["memory", "java", "performance"],
            },
            {
                "case_id": "db-005",
                "title": "RDS connection pool exhaustion",
                "description": "Database connections maxed out during peak hours",
                "tags": ["rds", "database", "connections"],
            },
        ]
        for c in cases:
            await memory.store_long_term(c)

    async def test_search_by_title(self, memory: MemoryLayer) -> None:
        """Title match scores 2.0 and ranks highest."""
        await self._seed_cases(memory)

        results = await memory.search_long_term("CPU spike")
        assert len(results) >= 1
        assert results[0]["case_id"] == "cpu-001"

    async def test_search_by_description(self, memory: MemoryLayer) -> None:
        """Description match scores 1.0."""
        await self._seed_cases(memory)

        results = await memory.search_long_term("log rotation failure")
        assert len(results) >= 1
        assert results[0]["case_id"] == "disk-002"

    async def test_search_by_tags(self, memory: MemoryLayer) -> None:
        """Tag match scores 1.5 per matching word."""
        await self._seed_cases(memory)

        results = await memory.search_long_term("performance")
        # Both cpu-001 and mem-004 have "performance" tag
        ids = [r["case_id"] for r in results]
        assert "cpu-001" in ids
        assert "mem-004" in ids

    async def test_search_scoring_title_beats_description(
        self, memory: MemoryLayer
    ) -> None:
        """A title match (2.0) ranks above a description-only match (1.0)."""
        await self._seed_cases(memory)

        # "production" appears in cpu-001's description AND tags
        # "latency" appears in net-003's title AND description AND tags
        results = await memory.search_long_term("production")
        assert len(results) >= 1
        assert results[0]["case_id"] == "cpu-001"

    async def test_search_respects_top_k(self, memory: MemoryLayer) -> None:
        """Search returns at most top_k results."""
        await self._seed_cases(memory)

        results = await memory.search_long_term("performance", top_k=1)
        assert len(results) <= 1

    async def test_search_returns_sorted_by_score(self, memory: MemoryLayer) -> None:
        """Results are sorted in descending score order."""
        await self._seed_cases(memory)

        # Query that matches multiple cases with different scores
        results = await memory.search_long_term("cpu", top_k=10)
        scores = []
        for r in results:
            score = 0.0
            if "cpu" in r.get("title", "").lower():
                score += 2.0
            if "cpu" in r.get("description", "").lower():
                score += 1.0
            for word in "cpu".split():
                if word in [t.lower() for t in r.get("tags", [])]:
                    score += 1.5
            scores.append(score)

        # Verify descending order
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    async def test_search_empty_results(self, memory: MemoryLayer) -> None:
        """Search returns empty list when no match exists."""
        await self._seed_cases(memory)

        results = await memory.search_long_term("quantum computing")
        assert results == []

    async def test_search_empty_index(self, memory: MemoryLayer) -> None:
        """Search returns empty list when index is empty."""
        results = await memory.search_long_term("anything")
        assert results == []

    async def test_search_case_insensitive(self, memory: MemoryLayer) -> None:
        """Search is case-insensitive."""
        await self._seed_cases(memory)

        results_lower = await memory.search_long_term("cpu spike")
        results_upper = await memory.search_long_term("CPU SPIKE")
        assert len(results_lower) == len(results_upper)
        if results_lower:
            assert results_lower[0]["case_id"] == results_upper[0]["case_id"]


# ---------------------------------------------------------------------------
# Internal — _load_long_term_index
# ---------------------------------------------------------------------------


class TestLoadLongTermIndex:
    """Tests for loading the long-term index from disk."""

    async def test_loads_json_files(self, tmp_path: Path) -> None:
        """_load_long_term_index reads all *.json files from the directory."""
        lt_dir = tmp_path / "lt"
        lt_dir.mkdir()

        case_a = {"case_id": "a", "title": "Case A"}
        case_b = {"case_id": "b", "title": "Case B"}
        (lt_dir / "a.json").write_text(json.dumps(case_a), encoding="utf-8")
        (lt_dir / "b.json").write_text(json.dumps(case_b), encoding="utf-8")

        memory = MemoryLayer(long_term_dir=str(lt_dir))
        assert len(memory._long_term_index) == 2
        titles = {c["title"] for c in memory._long_term_index}
        assert titles == {"Case A", "Case B"}

    async def test_handles_json_decode_error(self, tmp_path: Path) -> None:
        """Malformed JSON files are skipped without crashing."""
        lt_dir = tmp_path / "lt"
        lt_dir.mkdir()

        (lt_dir / "good.json").write_text(
            json.dumps({"case_id": "ok", "title": "OK"}), encoding="utf-8"
        )
        (lt_dir / "bad.json").write_text("not valid json {{{", encoding="utf-8")

        memory = MemoryLayer(long_term_dir=str(lt_dir))
        # Only the good file should be loaded
        assert len(memory._long_term_index) == 1
        assert memory._long_term_index[0]["title"] == "OK"

    async def test_handles_os_error(self, tmp_path: Path) -> None:
        """OS errors during file read are handled gracefully."""
        lt_dir = tmp_path / "lt"
        lt_dir.mkdir()

        good_file = lt_dir / "good.json"
        good_file.write_text(
            json.dumps({"case_id": "ok", "title": "OK"}), encoding="utf-8"
        )
        # File exists but unreadable
        bad_file = lt_dir / "bad.json"
        bad_file.write_text("data", encoding="utf-8")
        bad_file.chmod(0o000)

        try:
            memory = MemoryLayer(long_term_dir=str(lt_dir))
            # At least the good file should be loaded
            assert len(memory._long_term_index) >= 1
        finally:
            # Restore permissions so tmp_path cleanup works
            bad_file.chmod(0o644)

    async def test_ignores_non_json_files(self, tmp_path: Path) -> None:
        """Non-JSON files in the directory are ignored."""
        lt_dir = tmp_path / "lt"
        lt_dir.mkdir()

        (lt_dir / "data.json").write_text(
            json.dumps({"case_id": "x", "title": "X"}), encoding="utf-8"
        )
        (lt_dir / "readme.txt").write_text("some text", encoding="utf-8")
        (lt_dir / "notes.md").write_text("# Notes", encoding="utf-8")

        memory = MemoryLayer(long_term_dir=str(lt_dir))
        assert len(memory._long_term_index) == 1


# ---------------------------------------------------------------------------
# Init — directory creation
# ---------------------------------------------------------------------------


class TestMemoryInit:
    """Tests for MemoryLayer initialization."""

    def test_creates_directory(self, tmp_path: Path) -> None:
        """MemoryLayer creates the long-term directory if it doesn't exist."""
        new_dir = tmp_path / "new_memory"
        assert not new_dir.exists()

        MemoryLayer(long_term_dir=str(new_dir))
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_existing_directory_ok(self, tmp_path: Path) -> None:
        """MemoryLayer does not raise if directory already exists."""
        existing = tmp_path / "existing"
        existing.mkdir()

        memory = MemoryLayer(long_term_dir=str(existing))
        assert memory._long_term_dir == existing
