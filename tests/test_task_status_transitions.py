import pytest
from orchestra.core.task_queue import TaskStatus, TRANSITIONS


def test_failed_state_exists():
    assert TaskStatus.FAILED.value == "failed"


def test_in_progress_can_transition_to_failed():
    assert TaskStatus.FAILED in TRANSITIONS[TaskStatus.IN_PROGRESS]


def test_unstarted_states_can_transition_to_failed():
    """Cascade-cancel of unstarted siblings."""
    assert TaskStatus.FAILED in TRANSITIONS[TaskStatus.IDEA]
    assert TaskStatus.FAILED in TRANSITIONS[TaskStatus.ASSIGNED]


def test_failed_can_recover_to_planning():
    assert TaskStatus.PLANNING in TRANSITIONS[TaskStatus.FAILED]


def test_failed_is_terminal_otherwise():
    """FAILED can ONLY go back to PLANNING, not anywhere else."""
    assert TRANSITIONS[TaskStatus.FAILED] == {TaskStatus.PLANNING}


def test_testing_can_transition_to_failed():
    """Dev server crashes during FI → TESTING → FAILED."""
    assert TaskStatus.FAILED in TRANSITIONS[TaskStatus.TESTING]
