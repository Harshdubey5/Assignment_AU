from datetime import datetime

import pytest

from solution.scheduling import IST, is_open, next_eligible_time, parse_instant, priority_key


def test_open_interval_includes_start_and_excludes_end():
    assert is_open(datetime(2026, 11, 13, 10, 0, tzinfo=IST), "10:00", "19:00")
    assert not is_open(datetime(2026, 11, 13, 19, 0, tzinfo=IST), "10:00", "19:00")


def test_campaign_day_restriction_is_applied():
    saturday = datetime(2026, 11, 14, 10, 0, tzinfo=IST)
    assert is_open(saturday, "09:00", "18:00", ["sat"])
    assert not is_open(saturday, "09:00", "18:00", ["mon", "tue"])


def test_naive_provider_time_means_ist_and_z_means_utc():
    assert parse_instant("2026-11-13T10:00:00").utcoffset().total_seconds() == 19800
    assert parse_instant("2026-11-13T04:30:00Z") == datetime(2026, 11, 13, 10, 0, tzinfo=IST)


def test_next_eligible_time_waits_for_campaign_opening():
    now = datetime(2026, 11, 13, 9, 15, tzinfo=IST)
    assert next_eligible_time(now, "10:00", "19:00", ["fri"]) == datetime(
        2026, 11, 13, 10, 0, tzinfo=IST
    )


def test_next_eligible_time_never_rounds_backwards_within_open_window():
    now = datetime(2026, 11, 13, 10, 0, 45, tzinfo=IST)
    assert next_eligible_time(now, "10:00", "19:00", ["fri"]) == now


def test_next_eligible_time_skips_disallowed_day():
    saturday = datetime(2026, 11, 14, 16, 0, tzinfo=IST)
    assert next_eligible_time(saturday, "09:30", "18:00", ["mon", "fri"]) == datetime(
        2026, 11, 16, 9, 30, tzinfo=IST
    )


def test_campaign_window_is_intersected_with_regulatory_window():
    now = datetime(2026, 11, 13, 21, 0, tzinfo=IST)
    assert next_eligible_time(now, "20:00", "22:00", ["fri"]) == datetime(
        2026, 11, 20, 20, 0, tzinfo=IST
    )


def test_due_callbacks_then_retries_then_fresh_leads():
    due = datetime(2026, 11, 13, 12, 0, tzinfo=IST)
    jobs = [
        ("fresh", None, "b", "1"),
        ("retry", due, "a", "2"),
        ("callback", due, "c", "3"),
    ]
    ordered = sorted(jobs, key=lambda job: priority_key(*job))
    assert [item[0] for item in ordered] == ["callback", "retry", "fresh"]


def test_priority_key_rejects_unknown_kind():
    with pytest.raises(ValueError):
        priority_key("later", None, "campaign", "lead")
