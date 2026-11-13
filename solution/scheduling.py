"""Pure scheduling helpers for the campaign dispatcher.

Keeping time calculations separate from HTTP and database code makes the business
rules easy to reason about and test with fixed timestamps.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def as_ist(value: datetime) -> datetime:
    """Return an aware datetime in IST; treat naive fixture values as IST."""
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def parse_instant(value: str | datetime) -> datetime:
    """Parse provider timestamps (including trailing Z) and normalize to IST."""
    if isinstance(value, datetime):
        return as_ist(value)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return as_ist(datetime.fromisoformat(normalized))


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def is_open(
    instant: datetime,
    start: str,
    end: str,
    days: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Check a local-time window using the half-open interval [start, end)."""
    local = as_ist(instant)
    if days is not None and WEEKDAYS[local.weekday()] not in days:
        return False
    current = local.timetz().replace(tzinfo=None)
    return _clock(start) <= current < _clock(end)


def next_eligible_time(
    instant: datetime,
    campaign_start: str,
    campaign_end: str,
    campaign_days: list[str] | tuple[str, ...],
    regulatory_start: str = "09:00",
    regulatory_end: str = "21:00",
) -> datetime:
    """Find the earliest instant at/after ``instant`` inside both call windows."""
    now = as_ist(instant)

    # The campaign and regulatory windows must overlap on an allowed weekday.
    opens_at = max(_clock(campaign_start), _clock(regulatory_start))
    closes_at = min(_clock(campaign_end), _clock(regulatory_end))
    if opens_at >= closes_at:
        raise ValueError("campaign and regulatory calling windows do not overlap")

    for offset in range(8):
        day = now + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in campaign_days:
            continue
        opening = day.replace(
            hour=opens_at.hour,
            minute=opens_at.minute,
            second=0,
            microsecond=0,
        )
        closing = day.replace(
            hour=closes_at.hour,
            minute=closes_at.minute,
            second=0,
            microsecond=0,
        )
        candidate = max(now, opening) if offset == 0 else opening
        if candidate < closing:
            return candidate
    raise ValueError("no eligible day found within the next week")


def priority_key(
    kind: str,
    due_at: datetime | None,
    campaign_id: str,
    lead_id: str,
) -> tuple[int, datetime, str, str]:
    """Sort due callbacks before retries, and retries before fresh leads."""
    ranks = {"callback": 0, "retry": 1, "fresh": 2}
    if kind not in ranks:
        raise ValueError(f"unknown work kind: {kind}")
    # A missing due time means the lead is fresh; the rank still makes it last.
    due = as_ist(due_at) if due_at is not None else datetime.min.replace(tzinfo=IST)
    return (ranks[kind], due, campaign_id, lead_id)
