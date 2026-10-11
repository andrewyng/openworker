"""Unit tests for calendar recurrence helpers + create-tool payloads."""

from __future__ import annotations

from coworker.connectors.calendar_recurrence import (
    gcal_recurrence,
    outlook_recurrence,
    parse_recurrence,
)
from coworker.secrets import SecretStore


def test_parse_one_off_when_freq_empty():
    spec, err = parse_recurrence(freq="", start="2026-07-27T10:00:00")
    assert spec is None and err is None


def test_parse_rejects_until_and_count():
    spec, err = parse_recurrence(
        freq="daily",
        until="2026-12-31",
        count=5,
        start="2026-07-27T10:00:00",
    )
    assert spec is None
    assert "until or count" in err["error"]


def test_parse_rejects_non_positive_or_non_integer_bounds():
    for kwargs, field in (
        ({"count": 0}, "count"),
        ({"count": -1}, "count"),
        ({"count": 2.5}, "count"),
        ({"count": True}, "count"),
        ({"interval": 0}, "interval"),
        ({"interval": 1.5}, "interval"),
        ({"interval": True}, "interval"),
    ):
        spec, err = parse_recurrence(
            freq="daily",
            start="2026-07-27T10:00:00",
            **kwargs,
        )
        assert spec is None
        assert f"{field} must be a positive integer" == err["error"]


def test_parse_rejects_bad_freq():
    spec, err = parse_recurrence(freq="hourly", start="2026-07-27T10:00:00")
    assert spec is None
    assert "daily" in err["error"]


def test_indefinite_weekly_infers_weekday_from_start():
    # 2026-07-27 is a Monday
    spec, err = parse_recurrence(
        freq="weekly", start="2026-07-27T10:00:00", interval=1
    )
    assert err is None
    assert spec["freq"] == "weekly"
    assert spec["by_days"] == ["MO"]
    assert spec["count"] == 0
    assert spec["until_date"] == ""


def test_gcal_rrule_forever_weekly():
    spec, _ = parse_recurrence(
        freq="weekly", by_day="MO,WE", start="2026-07-27T10:00:00"
    )
    assert gcal_recurrence(spec) == ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"]


def test_gcal_rrule_until_and_count():
    until_spec, _ = parse_recurrence(
        freq="daily", until="2026-12-31", start="2026-07-27T10:00:00"
    )
    assert gcal_recurrence(until_spec, "America/Los_Angeles") == [
        "RRULE:FREQ=DAILY;UNTIL=20270101T075959Z"
    ]

    count_spec, _ = parse_recurrence(
        freq="weekly", by_day="friday", count=8, start="2026-07-31T10:00:00"
    )
    assert gcal_recurrence(count_spec) == [
        "RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=8"
    ]


def test_gcal_rrule_biweekly_interval():
    spec, _ = parse_recurrence(
        freq="weekly", interval=2, by_day="TU", start="2026-07-28T09:00:00"
    )
    assert gcal_recurrence(spec) == ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"]


def test_outlook_no_end_weekly():
    spec, _ = parse_recurrence(
        freq="weekly", by_day="monday", start="2026-07-27T10:00:00"
    )
    body = outlook_recurrence(spec, "2026-07-27T10:00:00")
    assert body == {
        "pattern": {
            "type": "weekly",
            "interval": 1,
            "daysOfWeek": ["monday"],
            "firstDayOfWeek": "sunday",
        },
        "range": {
            "type": "noEnd",
            "startDate": "2026-07-27",
            "recurrenceTimeZone": "UTC",
        },
    }


def test_outlook_end_date_and_numbered():
    until_spec, _ = parse_recurrence(
        freq="daily", until="2026-08-15", start="2026-07-27T10:00:00"
    )
    assert outlook_recurrence(until_spec, "2026-07-27T10:00:00")["range"] == {
        "type": "endDate",
        "startDate": "2026-07-27",
        "endDate": "2026-08-15",
        "recurrenceTimeZone": "UTC",
    }

    count_spec, _ = parse_recurrence(
        freq="monthly", count=6, start="2026-07-15T10:00:00"
    )
    out = outlook_recurrence(count_spec, "2026-07-15T10:00:00")
    assert out["pattern"] == {
        "type": "absoluteMonthly",
        "interval": 1,
        "dayOfMonth": 15,
    }
    assert out["range"] == {
        "type": "numbered",
        "startDate": "2026-07-15",
        "numberOfOccurrences": 6,
        "recurrenceTimeZone": "UTC",
    }


def test_outlook_yearly_absolute():
    spec, _ = parse_recurrence(freq="yearly", start="2026-03-15T09:00:00")
    out = outlook_recurrence(spec, "2026-03-15T09:00:00")
    assert out["pattern"]["type"] == "absoluteYearly"
    assert out["pattern"]["month"] == 3
    assert out["pattern"]["dayOfMonth"] == 15
    assert out["range"]["type"] == "noEnd"


def test_parse_rejects_invalid_or_pre_start_until():
    for until in ("2026-02-30", "2026-07-26", "2026-07-27T23:59:59Z"):
        spec, err = parse_recurrence(
            freq="daily", until=until, start="2026-07-27T10:00:00"
        )
        assert spec is None
        assert "until" in err["error"]


def test_parse_rejects_nonweekly_by_day_and_invalid_start():
    spec, err = parse_recurrence(
        freq="monthly", by_day="MO", start="2026-07-27T10:00:00"
    )
    assert spec is None
    assert "only supported for weekly" in err["error"]

    spec, err = parse_recurrence(freq="daily", start="not-a-date")
    assert spec is None
    assert "ISO start datetime" in err["error"]


def test_gcal_rejects_non_iana_timezone_for_bounded_recurrence():
    spec, _ = parse_recurrence(
        freq="daily", until="2026-08-01", start="2026-07-27T10:00:00"
    )
    try:
        gcal_recurrence(spec, "Pacific Standard Time")
    except ValueError as exc:
        assert "valid IANA name" in str(exc)
    else:
        raise AssertionError("expected invalid IANA timezone to be rejected")


def _connect_gcal(secrets: SecretStore) -> None:
    from coworker.connectors import gcal_accounts

    gcal_accounts.managed_connect_account(
        secrets,
        {
            "type": "oauth",
            "enabled": True,
            "managed": True,
            "access_token": "tok",
            "account": "me@example.com",
        },
    )


def test_gcal_create_event_indefinite_payload(tmp_path, monkeypatch):
    import coworker.connectors.integration_tools as it

    secrets = SecretStore(tmp_path / "secrets.json")
    _connect_gcal(secrets)
    calls = []

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        calls.append({"method": method, "url": url, "json": json})
        return {"ok": True, "data": {"id": "series1"}}

    monkeypatch.setattr(it, "_request", fake_request)
    tools = {t.__name__: t for t in it.make_integration_tools(secrets)}

    tools["gcal_create_event"](
        "Standup",
        "2026-07-27T10:00:00",
        "2026-07-27T10:15:00",
        timezone="America/Los_Angeles",
        freq="weekly",
        by_day="MO,WE,FR",
    )
    payload = calls[-1]["json"]
    assert payload["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"]
    assert payload["start"]["timeZone"] == "America/Los_Angeles"


def test_gcal_create_event_rejects_until_and_count(tmp_path, monkeypatch):
    import coworker.connectors.integration_tools as it

    secrets = SecretStore(tmp_path / "secrets.json")
    _connect_gcal(secrets)
    monkeypatch.setattr(
        it,
        "_request",
        lambda *a, **k: {"ok": True, "data": {}},
    )
    tools = {t.__name__: t for t in it.make_integration_tools(secrets)}
    out = tools["gcal_create_event"](
        "X",
        "2026-07-27T10:00:00",
        "2026-07-27T11:00:00",
        freq="daily",
        until="2026-12-31",
        count=3,
    )
    assert "error" in out


def test_outlook_create_event_indefinite_payload(tmp_path, monkeypatch):
    import coworker.connectors.integration_tools as it
    from coworker.cloud import managed_profile_from_callback
    from coworker.connectors.setup import managed_connect_connector

    secrets = SecretStore(tmp_path / "secrets.json")
    managed_connect_connector(
        secrets,
        "outlook",
        managed_profile_from_callback(
            {
                "access_token": "tok",
                "account": "rohit@openworker.com",
                "provider": "microsoft",
            }
        ),
    )
    calls = []

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        calls.append({"json": json})
        return {"ok": True, "data": {"id": "series1"}}

    monkeypatch.setattr(it, "_request", fake_request)
    tools = {t.__name__: t for t in it.make_integration_tools(secrets)}

    tools["outlook_create_event"](
        "Sync",
        "2026-07-27T10:00:00",
        "2026-07-27T10:30:00",
        timezone="Pacific Standard Time",
        freq="weekly",
    )
    rec = calls[-1]["json"]["recurrence"]
    assert rec["range"]["type"] == "noEnd"
    assert rec["range"]["startDate"] == "2026-07-27"
    assert rec["range"]["recurrenceTimeZone"] == "Pacific Standard Time"
    assert rec["pattern"]["daysOfWeek"] == ["monday"]
