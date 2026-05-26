"""In-memory metrics store for observability dashboard.

Stores per-request event data in memory and supports aggregation queries.
"""

from __future__ import annotations

import asyncio
import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class LLMCallEvent:
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    duration_ms: float
    success: bool
    error: str = ""


@dataclass
class SkillCallEvent:
    skill_name: str
    action: str
    duration_ms: float
    success: bool
    error: str = ""


@dataclass
class RequestEvent:
    timestamp: datetime
    session_id: str
    trace_id: str
    status: str  # "completed" | "failed"
    duration_ms: float
    llm_calls: list[LLMCallEvent]
    skill_calls: list[SkillCallEvent]
    user_input: str  # truncated to 100 chars


class MetricsStore:
    """Thread-safe in-memory store for request events with aggregation queries."""

    MAX_EVENTS = 10_000

    def __init__(self) -> None:
        self._events: list[RequestEvent] = []
        self._lock = asyncio.Lock()

    async def record_request(self, event: RequestEvent) -> None:
        """Append an event, trimming oldest if over MAX_EVENTS."""
        async with self._lock:
            self._events.append(event)
            if len(self._events) > self.MAX_EVENTS:
                self._events = self._events[-self.MAX_EVENTS :]

    def _filter_by_time(
        self, start: datetime, end: datetime
    ) -> list[RequestEvent]:
        """Use bisect for efficient time-range filtering on sorted events."""
        # Ensure UTC-aware datetimes for comparison
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)

        timestamps = [e.timestamp for e in self._events]
        left = bisect.bisect_left(timestamps, start)
        right = bisect.bisect_right(timestamps, end)
        return self._events[left:right]

    def query_summary(self, start: datetime, end: datetime) -> dict:
        """Return aggregated summary for the time range."""
        events = self._filter_by_time(start, end)
        total_requests = len(events)
        if total_requests == 0:
            return {
                "total_requests": 0,
                "success_count": 0,
                "failure_count": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "p99_latency_ms": 0.0,
                "total_llm_tokens_in": 0,
                "total_llm_tokens_out": 0,
                "total_llm_calls": 0,
                "total_skill_calls": 0,
            }

        success_count = sum(1 for e in events if e.status == "completed")
        failure_count = total_requests - success_count
        success_rate = success_count / total_requests

        latencies = sorted(e.duration_ms for e in events)
        avg_latency_ms = sum(latencies) / total_requests
        p50 = self._percentile(latencies, 50)
        p95 = self._percentile(latencies, 95)
        p99 = self._percentile(latencies, 99)

        total_llm_tokens_in = 0
        total_llm_tokens_out = 0
        total_llm_calls = 0
        total_skill_calls = 0
        for e in events:
            total_llm_calls += len(e.llm_calls)
            total_skill_calls += len(e.skill_calls)
            for call in e.llm_calls:
                total_llm_tokens_in += call.tokens_in
                total_llm_tokens_out += call.tokens_out

        return {
            "total_requests": total_requests,
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency_ms,
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "p99_latency_ms": p99,
            "total_llm_tokens_in": total_llm_tokens_in,
            "total_llm_tokens_out": total_llm_tokens_out,
            "total_llm_calls": total_llm_calls,
            "total_skill_calls": total_skill_calls,
        }

    def query_timeline(
        self, start: datetime, end: datetime, bucket: str = "hour"
    ) -> list[dict]:
        """Group events into time buckets and aggregate."""
        events = self._filter_by_time(start, end)
        if not events:
            return []

        bucket_deltas = {
            "minute": timedelta(minutes=1),
            "hour": timedelta(hours=1),
            "day": timedelta(days=1),
        }
        delta = bucket_deltas.get(bucket, timedelta(hours=1))

        # Round start down to bucket boundary
        start = start.astimezone(timezone.utc)
        if bucket == "minute":
            bucket_start = start.replace(second=0, microsecond=0)
        elif bucket == "hour":
            bucket_start = start.replace(minute=0, second=0, microsecond=0)
        else:  # day
            bucket_start = start.replace(hour=0, minute=0, second=0, microsecond=0)

        buckets: dict[datetime, list[RequestEvent]] = defaultdict(list)
        for e in events:
            ts = e.timestamp.astimezone(timezone.utc)
            # Calculate bucket key
            if bucket == "minute":
                key = ts.replace(second=0, microsecond=0)
            elif bucket == "hour":
                key = ts.replace(minute=0, second=0, microsecond=0)
            else:  # day
                key = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            buckets[key].append(e)

        result = []
        current = bucket_start
        end_utc = end.astimezone(timezone.utc)
        while current <= end_utc:
            bucket_events = buckets.get(current, [])
            count = len(bucket_events)
            success_count = sum(1 for e in bucket_events if e.status == "completed")
            failure_count = count - success_count
            avg_latency = (
                sum(e.duration_ms for e in bucket_events) / count if count else 0.0
            )
            tokens_total = 0
            for e in bucket_events:
                for call in e.llm_calls:
                    tokens_total += call.tokens_in + call.tokens_out
            result.append(
                {
                    "timestamp": current,
                    "request_count": count,
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "avg_latency_ms": avg_latency,
                    "tokens_total": tokens_total,
                }
            )
            current += delta

        return result

    def query_skill_stats(self, start: datetime, end: datetime) -> list[dict]:
        """Return per-skill aggregated statistics."""
        events = self._filter_by_time(start, end)
        stats: dict[str, dict] = defaultdict(
            lambda: {
                "call_count": 0,
                "success_count": 0,
                "error_count": 0,
                "total_latency_ms": 0.0,
            }
        )

        for e in events:
            for call in e.skill_calls:
                s = stats[call.skill_name]
                s["call_count"] += 1
                if call.success:
                    s["success_count"] += 1
                else:
                    s["error_count"] += 1
                s["total_latency_ms"] += call.duration_ms

        result = []
        for skill_name, data in sorted(stats.items()):
            call_count = data["call_count"]
            result.append(
                {
                    "skill_name": skill_name,
                    "call_count": call_count,
                    "success_count": data["success_count"],
                    "error_count": data["error_count"],
                    "success_rate": (
                        data["success_count"] / call_count if call_count else 0.0
                    ),
                    "avg_latency_ms": (
                        data["total_latency_ms"] / call_count if call_count else 0.0
                    ),
                }
            )
        return result

    def query_llm_stats(self, start: datetime, end: datetime) -> list[dict]:
        """Return per-provider/model aggregated statistics."""
        events = self._filter_by_time(start, end)
        stats: dict[tuple[str, str], dict] = defaultdict(
            lambda: {
                "call_count": 0,
                "success_count": 0,
                "error_count": 0,
                "total_tokens_in": 0,
                "total_tokens_out": 0,
                "total_latency_ms": 0.0,
            }
        )

        for e in events:
            for call in e.llm_calls:
                key = (call.provider, call.model)
                s = stats[key]
                s["call_count"] += 1
                if call.success:
                    s["success_count"] += 1
                else:
                    s["error_count"] += 1
                s["total_tokens_in"] += call.tokens_in
                s["total_tokens_out"] += call.tokens_out
                s["total_latency_ms"] += call.duration_ms

        result = []
        for (provider, model), data in sorted(stats.items()):
            call_count = data["call_count"]
            result.append(
                {
                    "provider": provider,
                    "model": model,
                    "call_count": call_count,
                    "success_count": data["success_count"],
                    "error_count": data["error_count"],
                    "total_tokens_in": data["total_tokens_in"],
                    "total_tokens_out": data["total_tokens_out"],
                    "avg_latency_ms": (
                        data["total_latency_ms"] / call_count if call_count else 0.0
                    ),
                    "error_rate": (
                        data["error_count"] / call_count if call_count else 0.0
                    ),
                }
            )
        return result

    def query_recent_requests(self, limit: int = 50) -> list[dict]:
        """Return the most recent requests."""
        recent = self._events[-limit:] if self._events else []
        result = []
        for e in reversed(recent):
            skill_count = len(e.skill_calls)
            llm_token_total = sum(
                call.tokens_in + call.tokens_out for call in e.llm_calls
            )
            result.append(
                {
                    "timestamp": e.timestamp,
                    "session_id": e.session_id,
                    "trace_id": e.trace_id,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                    "user_input": e.user_input,
                    "skill_count": skill_count,
                    "llm_token_total": llm_token_total,
                }
            )
        return result

    @staticmethod
    def _percentile(sorted_values: list[float], p: float) -> float:
        """Compute percentile from a sorted list of values."""
        if not sorted_values:
            return 0.0
        n = len(sorted_values)
        if n == 1:
            return sorted_values[0]
        # Use linear interpolation (method used by numpy)
        idx = (p / 100.0) * (n - 1)
        lower = int(idx)
        upper = lower + 1
        if upper >= n:
            return sorted_values[-1]
        weight = idx - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


# Module-level singleton instance
_metrics_store_instance: MetricsStore | None = None


def get_metrics_store() -> MetricsStore:
    """Return the singleton MetricsStore instance."""
    global _metrics_store_instance
    if _metrics_store_instance is None:
        _metrics_store_instance = MetricsStore()
    return _metrics_store_instance
