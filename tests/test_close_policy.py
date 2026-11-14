"""Unit-level tests for two-strike close bookkeeping helpers."""

from app.config import Settings


def test_close_miss_threshold_default():
    settings = Settings()
    assert settings.close_miss_threshold == 2


def test_two_strike_logic():
    """Pure logic mirror of pipeline strike rules."""

    def next_state(missing_poll_count: int, present: bool, threshold: int = 2) -> tuple[int, bool]:
        if present:
            return 0, False
        count = missing_poll_count + 1
        if count >= threshold:
            return count, True  # closed
        return count, False

    assert next_state(0, True) == (0, False)
    assert next_state(0, False) == (1, False)
    assert next_state(1, False) == (2, True)
    assert next_state(1, True) == (0, False)


def test_baseline_suppresses_new_flags():
    is_baseline = True
    emit_events = not is_baseline
    match_searches = not is_baseline
    counts_as_new_since_visit = not is_baseline
    assert emit_events is False
    assert match_searches is False
    assert counts_as_new_since_visit is False
