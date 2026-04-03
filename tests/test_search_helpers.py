from fastapi import HTTPException

from backend.main import _parse_csv_values, _parse_iso_datetime


def test_parse_csv_values_handles_empty():
    assert _parse_csv_values("") == []
    assert _parse_csv_values(None) == []


def test_parse_csv_values_trims_entries():
    assert _parse_csv_values(" pending,failed , qa ") == ["pending", "failed", "qa"]


def test_parse_iso_datetime_accepts_iso_with_z():
    value = _parse_iso_datetime("2026-01-01T10:30:00Z", "started_after")
    assert value is not None
    assert value.year == 2026
    assert value.month == 1
    assert value.day == 1


def test_parse_iso_datetime_rejects_invalid():
    try:
        _parse_iso_datetime("not-a-date", "created_before")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "created_before" in str(exc.detail)
