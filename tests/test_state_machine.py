"""Tests for aiops_agent.core.state_machine."""

import pytest

from aiops_agent.core.state_machine import TaskStateMachine
from aiops_agent.models.schemas import TaskStatus


# ────────────────────────────────────────────────────────────
# Valid transitions
# ────────────────────────────────────────────────────────────

class TestValidTransitions:
    @pytest.mark.parametrize("initial, target", [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.CANCELLED),
        (TaskStatus.RUNNING, TaskStatus.COMPLETED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
        (TaskStatus.FAILED, TaskStatus.PENDING),
    ])
    def test_valid_transition(self, initial, target):
        sm = TaskStateMachine(task_id="t1", initial_status=initial)
        sm.transition(target)
        assert sm.status == target

    def test_pending_to_running(self):
        sm = TaskStateMachine(task_id="t1")
        assert sm.status == TaskStatus.PENDING
        sm.transition(TaskStatus.RUNNING)
        assert sm.status == TaskStatus.RUNNING

    def test_pending_to_cancelled(self):
        sm = TaskStateMachine(task_id="t1")
        sm.transition(TaskStatus.CANCELLED)
        assert sm.status == TaskStatus.CANCELLED

    def test_running_to_completed(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.COMPLETED)
        assert sm.status == TaskStatus.COMPLETED

    def test_running_to_failed(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.FAILED)
        assert sm.status == TaskStatus.FAILED

    def test_running_to_cancelled(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.CANCELLED)
        assert sm.status == TaskStatus.CANCELLED

    def test_failed_to_pending_retry_flow(self):
        """FAILED → PENDING retry flow: go back to pending, then run again."""
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.FAILED)
        sm.transition(TaskStatus.PENDING)
        assert sm.status == TaskStatus.PENDING
        sm.transition(TaskStatus.RUNNING)
        assert sm.status == TaskStatus.RUNNING
        sm.transition(TaskStatus.COMPLETED)
        assert sm.status == TaskStatus.COMPLETED


# ────────────────────────────────────────────────────────────
# Invalid transitions
# ────────────────────────────────────────────────────────────

class TestInvalidTransitions:
    @pytest.mark.parametrize("initial, target", [
        (TaskStatus.PENDING, TaskStatus.COMPLETED),
        (TaskStatus.COMPLETED, TaskStatus.RUNNING),
        (TaskStatus.COMPLETED, TaskStatus.PENDING),
        (TaskStatus.COMPLETED, TaskStatus.CANCELLED),
        (TaskStatus.COMPLETED, TaskStatus.FAILED),
        (TaskStatus.FAILED, TaskStatus.COMPLETED),
        (TaskStatus.CANCELLED, TaskStatus.PENDING),
        (TaskStatus.CANCELLED, TaskStatus.RUNNING),
        (TaskStatus.CANCELLED, TaskStatus.COMPLETED),
        (TaskStatus.CANCELLED, TaskStatus.FAILED),
        (TaskStatus.CANCELLED, TaskStatus.CANCELLED),
    ])
    def test_invalid_transition_raises(self, initial, target):
        sm = TaskStateMachine(task_id="t1", initial_status=initial)
        with pytest.raises(ValueError):
            sm.transition(target)

    def test_pending_to_completed_raises(self):
        sm = TaskStateMachine(task_id="t1")
        with pytest.raises(ValueError):
            sm.transition(TaskStatus.COMPLETED)

    def test_completed_to_anything_raises(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.COMPLETED)
        for s in TaskStatus:
            if s != TaskStatus.COMPLETED:
                with pytest.raises(ValueError):
                    sm.transition(s)

    def test_cancelled_to_anything_raises(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.CANCELLED)
        for s in TaskStatus:
            if s != TaskStatus.CANCELLED:
                with pytest.raises(ValueError):
                    sm.transition(s)

    def test_failed_to_completed_raises(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.FAILED)
        with pytest.raises(ValueError):
            sm.transition(TaskStatus.COMPLETED)


# ────────────────────────────────────────────────────────────
# on_transition callback
# ────────────────────────────────────────────────────────────

class TestTransitionCallback:
    def test_callback_called_with_correct_args(self):
        calls = []
        def on_cb(task_id, old_status, new_status):
            calls.append((task_id, old_status, new_status))

        sm = TaskStateMachine(task_id="task-42", on_transition=on_cb)
        sm.transition(TaskStatus.RUNNING)

        assert len(calls) == 1
        assert calls[0] == ("task-42", TaskStatus.PENDING, TaskStatus.RUNNING)

    def test_callback_called_for_each_transition(self):
        calls = []
        def on_cb(task_id, old_status, new_status):
            calls.append((task_id, old_status, new_status))

        sm = TaskStateMachine(task_id="t2", on_transition=on_cb)
        sm.transition(TaskStatus.RUNNING)
        sm.transition(TaskStatus.COMPLETED)

        assert len(calls) == 2
        assert calls[0] == ("t2", TaskStatus.PENDING, TaskStatus.RUNNING)
        assert calls[1] == ("t2", TaskStatus.RUNNING, TaskStatus.COMPLETED)

    def test_no_callback_when_none(self):
        sm = TaskStateMachine(task_id="t3", on_transition=None)
        sm.transition(TaskStatus.RUNNING)
        # Should not raise

    def test_callback_not_called_on_invalid_transition(self):
        calls = []
        def on_cb(task_id, old_status, new_status):
            calls.append((task_id, old_status, new_status))

        sm = TaskStateMachine(task_id="t4", on_transition=on_cb)
        with pytest.raises(ValueError):
            sm.transition(TaskStatus.COMPLETED)  # PENDING → COMPLETED is invalid

        assert len(calls) == 0
        assert sm.status == TaskStatus.PENDING  # status unchanged


# ────────────────────────────────────────────────────────────
# can_transition
# ────────────────────────────────────────────────────────────

class TestCanTransition:
    def test_pending_can_go_to_running(self):
        sm = TaskStateMachine(task_id="t1")
        assert sm.can_transition(TaskStatus.RUNNING) is True

    def test_pending_can_go_to_cancelled(self):
        sm = TaskStateMachine(task_id="t1")
        assert sm.can_transition(TaskStatus.CANCELLED) is True

    def test_pending_cannot_go_to_completed(self):
        sm = TaskStateMachine(task_id="t1")
        assert sm.can_transition(TaskStatus.COMPLETED) is False

    def test_running_can_go_to_completed(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        assert sm.can_transition(TaskStatus.COMPLETED) is True

    def test_running_can_go_to_failed(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        assert sm.can_transition(TaskStatus.FAILED) is True

    def test_running_can_go_to_cancelled(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        assert sm.can_transition(TaskStatus.CANCELLED) is True

    def test_running_cannot_go_to_pending(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        assert sm.can_transition(TaskStatus.PENDING) is False

    def test_completed_cannot_go_anywhere(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.COMPLETED)
        for s in TaskStatus:
            assert sm.can_transition(s) is False

    def test_failed_can_go_to_pending(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.FAILED)
        assert sm.can_transition(TaskStatus.PENDING) is True

    def test_failed_cannot_go_to_completed(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.FAILED)
        assert sm.can_transition(TaskStatus.COMPLETED) is False

    def test_cancelled_cannot_go_anywhere(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.CANCELLED)
        for s in TaskStatus:
            assert sm.can_transition(s) is False


# ────────────────────────────────────────────────────────────
# is_terminal
# ────────────────────────────────────────────────────────────

class TestIsTerminal:
    @pytest.mark.parametrize("status, expected", [
        (TaskStatus.COMPLETED, True),
        (TaskStatus.FAILED, True),
        (TaskStatus.CANCELLED, True),
        (TaskStatus.PENDING, False),
        (TaskStatus.RUNNING, False),
    ])
    def test_is_terminal(self, status, expected):
        sm = TaskStateMachine(task_id="t1", initial_status=status)
        assert sm.is_terminal is expected

    def test_becomes_terminal_after_transition(self):
        sm = TaskStateMachine(task_id="t1")
        assert sm.is_terminal is False
        sm.transition(TaskStatus.RUNNING)
        assert sm.is_terminal is False
        sm.transition(TaskStatus.COMPLETED)
        assert sm.is_terminal is True

    def test_failed_is_terminal(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.FAILED)
        assert sm.is_terminal is True

    def test_cancelled_is_terminal(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        sm.transition(TaskStatus.CANCELLED)
        assert sm.is_terminal is True


# ────────────────────────────────────────────────────────────
# Error message format
# ────────────────────────────────────────────────────────────

class TestErrorMessageFormat:
    def test_error_message_contains_statuses(self):
        sm = TaskStateMachine(task_id="abc")
        with pytest.raises(ValueError) as exc_info:
            sm.transition(TaskStatus.COMPLETED)

        msg = str(exc_info.value)
        assert "pending" in msg
        assert "completed" in msg

    def test_error_message_contains_task_id(self):
        sm = TaskStateMachine(task_id="unique-123")
        with pytest.raises(ValueError) as exc_info:
            sm.transition(TaskStatus.COMPLETED)

        msg = str(exc_info.value)
        assert "unique-123" in msg

    def test_error_message_format(self):
        sm = TaskStateMachine(task_id="xyz")
        with pytest.raises(ValueError) as exc_info:
            sm.transition(TaskStatus.FAILED)  # PENDING → FAILED is invalid

        msg = str(exc_info.value)
        assert "pending" in msg.lower() or "PENDING" in msg or "pending" in msg
        assert "xyz" in msg


# ────────────────────────────────────────────────────────────
# Non-default initial status
# ────────────────────────────────────────────────────────────

class TestNonDefaultInitialStatus:
    def test_start_from_failed_retry_flow(self):
        sm = TaskStateMachine(task_id="retry", initial_status=TaskStatus.FAILED)
        assert sm.status == TaskStatus.FAILED
        sm.transition(TaskStatus.PENDING)
        assert sm.status == TaskStatus.PENDING
        sm.transition(TaskStatus.RUNNING)
        sm.transition(TaskStatus.COMPLETED)

    def test_start_from_running(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.RUNNING)
        assert sm.status == TaskStatus.RUNNING
        assert sm.is_terminal is False

    def test_start_from_completed(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.COMPLETED)
        assert sm.status == TaskStatus.COMPLETED
        assert sm.is_terminal is True

    def test_start_from_cancelled(self):
        sm = TaskStateMachine(task_id="t1", initial_status=TaskStatus.CANCELLED)
        assert sm.status == TaskStatus.CANCELLED
        assert sm.is_terminal is True

    def test_task_id_property(self):
        sm = TaskStateMachine(task_id="my-task-001")
        assert sm.task_id == "my-task-001"
