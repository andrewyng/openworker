"""Shared calendar recurrence helpers for Google Calendar + Outlook create tools.

Agent-facing surface is flat (freq/interval/by_day/until/count). Forever =
freq set with neither until nor count — matches GCal (omit UNTIL/COUNT) and
Graph (range.type=noEnd).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FREQS = frozenset({"daily", "weekly", "monthly", "yearly"})

# Accept Graph names, RFC BYDAY tokens, and common abbreviations.
_DAY_ALIASES: dict[str, str] = {
    "su": "SU",
    "sun": "SU",
    "sunday": "SU",
    "mo": "MO",
    "mon": "MO",
    "monday": "MO",
    "tu": "TU",
    "tue": "TU",
    "tues": "TU",
    "tuesday": "TU",
    "we": "WE",
    "wed": "WE",
    "wednesday": "WE",
    "th": "TH",
    "thu": "TH",
    "thur": "TH",
    "thurs": "TH",
    "thursday": "TH",
    "fr": "FR",
    "fri": "FR",
    "friday": "FR",
    "sa": "SA",
    "sat": "SA",
    "saturday": "SA",
}

_GCAL_TO_GRAPH = {
    "SU": "sunday",
    "MO": "monday",
    "TU": "tuesday",
    "WE": "wednesday",
    "TH": "thursday",
    "FR": "friday",
    "SA": "saturday",
}

_WEEKDAY_TO_GCAL = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]  # datetime.weekday()

RECURRENCE_PROPS: dict[str, Any] = {
    "freq": {
        "type": "string",
        "description": (
            "Recurrence frequency: daily, weekly, monthly, or yearly. "
            "Omit for a one-off event. With neither until nor count, repeats "
            "indefinitely."
        ),
    },
    "interval": {
        "type": "integer",
        "description": "Repeat every N periods (default 1). E.g. 2 + weekly = biweekly.",
    },
    "by_day": {
        "type": "string",
        "description": (
            "Weekdays for weekly recurrence, comma-separated "
            "(MO,WE or monday,wednesday). Defaults to the weekday of start."
        ),
    },
    "until": {
        "type": "string",
        "description": (
            "Optional inclusive end date (YYYY-MM-DD). Do not combine with count."
        ),
    },
    "count": {
        "type": "integer",
        "description": "Optional number of occurrences. Do not combine with until.",
    },
}


def parse_recurrence(
    *,
    freq: str = "",
    interval: int = 1,
    by_day: str = "",
    until: str = "",
    count: int = 0,
    start: str = "",
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """Normalize recurrence args.

    Returns (spec, None) when recurring, (None, None) when one-off,
    (None, {"error": ...}) on invalid input.
    """
    raw = (freq or "").strip().lower()
    if not raw:
        return None, None
    if raw not in _FREQS:
        return None, {
            "error": "freq must be one of: daily, weekly, monthly, yearly"
        }

    try:
        iv = int(interval) if interval not in (None, "") else 1
    except (TypeError, ValueError):
        return None, {"error": "interval must be a positive integer"}
    if iv < 1:
        return None, {"error": "interval must be a positive integer"}

    until_s = (until or "").strip()
    try:
        count_n = int(count) if count not in (None, "") else 0
    except (TypeError, ValueError):
        return None, {"error": "count must be a positive integer"}
    if count_n < 0:
        return None, {"error": "count must be a positive integer"}
    if until_s and count_n:
        return None, {"error": "pass until or count, not both"}

    stamp = _parse_start(start)
    if stamp is None:
        return None, {"error": "recurrence needs a parseable ISO start datetime"}

    until_date = ""
    if until_s:
        until_date, err = _normalize_until(until_s)
        if err:
            return None, err
        if _dt.date.fromisoformat(until_date) < stamp.date():
            return None, {"error": "until must be on or after the event start date"}

    days = _parse_by_day(by_day)
    if days is None:
        return None, {
            "error": "by_day must be comma-separated weekdays "
            "(e.g. MO,WE or monday,wednesday)"
        }
    if raw == "weekly" and not days:
        days = [_WEEKDAY_TO_GCAL[stamp.weekday()]]
    elif raw != "weekly" and days:
        return None, {"error": "by_day is only supported for weekly recurrence"}

    day_of_month = 0
    month = 0
    if raw in ("monthly", "yearly"):
        day_of_month = stamp.day
        month = stamp.month

    return (
        {
            "freq": raw,
            "interval": iv,
            "by_days": days,
            "until_date": until_date,
            "count": count_n,
            "day_of_month": day_of_month,
            "month": month,
        },
        None,
    )


def gcal_recurrence(spec: dict[str, Any], timezone: str = "UTC") -> list[str]:
    """Build Google Calendar recurrence[] (RFC5545 RRULE line)."""
    try:
        event_zone = ZoneInfo(timezone)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError(
            "timezone must be a valid IANA name for recurring Google events"
        ) from exc

    parts = [f"FREQ={spec['freq'].upper()}"]
    if spec["interval"] != 1:
        parts.append(f"INTERVAL={spec['interval']}")
    if spec["freq"] == "weekly" and spec["by_days"]:
        parts.append("BYDAY=" + ",".join(spec["by_days"]))
    if spec["freq"] == "monthly" and spec["day_of_month"]:
        parts.append(f"BYMONTHDAY={spec['day_of_month']}")
    if spec["freq"] == "yearly":
        if spec["month"]:
            parts.append(f"BYMONTH={spec['month']}")
        if spec["day_of_month"]:
            parts.append(f"BYMONTHDAY={spec['day_of_month']}")
    if spec["until_date"]:
        end_date = _dt.date.fromisoformat(spec["until_date"])
        local_cutoff = _dt.datetime.combine(
            end_date, _dt.time(23, 59, 59), tzinfo=event_zone
        )
        until_utc = local_cutoff.astimezone(_dt.timezone.utc)
        parts.append(f"UNTIL={until_utc.strftime('%Y%m%dT%H%M%SZ')}")
    elif spec["count"]:
        parts.append(f"COUNT={spec['count']}")
    return ["RRULE:" + ";".join(parts)]


def outlook_recurrence(
    spec: dict[str, Any], start: str, timezone: str = "UTC"
) -> dict[str, Any]:
    """Build Microsoft Graph patternedRecurrence (noEnd when unbounded)."""
    start_date = _date_from_start(start)
    if not start_date:
        # parse_recurrence already requires parseable start for weekly/monthly/yearly;
        # daily with empty start still needs a range.startDate for Graph.
        raise ValueError("start date required for Outlook recurrence")

    pattern: dict[str, Any] = {
        "interval": spec["interval"],
    }
    freq = spec["freq"]
    if freq == "daily":
        pattern["type"] = "daily"
    elif freq == "weekly":
        pattern["type"] = "weekly"
        pattern["daysOfWeek"] = [_GCAL_TO_GRAPH[d] for d in spec["by_days"]]
        pattern["firstDayOfWeek"] = "sunday"
    elif freq == "monthly":
        pattern["type"] = "absoluteMonthly"
        pattern["dayOfMonth"] = spec["day_of_month"]
    else:  # yearly
        pattern["type"] = "absoluteYearly"
        pattern["dayOfMonth"] = spec["day_of_month"]
        pattern["month"] = spec["month"]

    if spec["until_date"]:
        range_obj: dict[str, Any] = {
            "type": "endDate",
            "startDate": start_date,
            "endDate": spec["until_date"],
            "recurrenceTimeZone": timezone,
        }
    elif spec["count"]:
        range_obj = {
            "type": "numbered",
            "startDate": start_date,
            "numberOfOccurrences": spec["count"],
            "recurrenceTimeZone": timezone,
        }
    else:
        range_obj = {
            "type": "noEnd",
            "startDate": start_date,
            "recurrenceTimeZone": timezone,
        }

    return {"pattern": pattern, "range": range_obj}


def _parse_by_day(raw: str) -> Optional[list[str]]:
    text = (raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\s]+", text):
        if not token:
            continue
        key = token.strip().lower()
        gcal = _DAY_ALIASES.get(key)
        if not gcal:
            return None
        if gcal not in seen:
            seen.add(gcal)
            out.append(gcal)
    return out


def _parse_start(start: str) -> Optional[_dt.datetime]:
    text = (start or "").strip()
    if not text:
        return None
    # Accept trailing Z by normalizing to fromisoformat-friendly offset form.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _date_from_start(start: str) -> str:
    stamp = _parse_start(start)
    if stamp is None:
        return ""
    return stamp.date().isoformat()


def _normalize_until(until: str) -> tuple[str, Optional[dict[str, str]]]:
    """Validate and return an inclusive YYYY-MM-DD recurrence end date."""
    text = until.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "", {"error": "until must be an inclusive date in YYYY-MM-DD format"}
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        return "", {"error": "until must be a valid date in YYYY-MM-DD format"}
    return text, None
