"""Tests for deepseek-peak-hours — pure, offline, no network."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deepseek_peak_hours import cli
from deepseek_peak_hours.core import (
    BUNDLED_CONFIG,
    build_config,
    evaluate,
    humanize_delta,
    load_config,
    next_boundary,
    parse_footnote,
    parse_holidays_txt,
    staleness_warnings,
    weekdays_label,
    window_minutes,
)

FOOTNOTE_HTML = """
<html><body>
<p>Some unrelated paragraph.</p>
<p>(2) Off-peak rates are half of the peak rates. Peak hours are
01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday, excluding
Chinese public holidays. All other hours are off-peak, including weekends
and Chinese public holidays in full.</p>
</body></html>
"""


@pytest.fixture()
def cfg() -> dict:
    return load_config(BUNDLED_CONFIG)


# --------------------------------------------------------------------------- #
# window maths
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "spec,expected",
    [
        ("01:00-04:00", (60, 240)),
        ("06:00-10:00", (360, 600)),
        ("22:00-02:00", (1320, 120)),   # crosses midnight
    ],
)
def test_window_minutes(spec, expected):
    assert window_minutes(spec) == expected


def test_weekdays_label():
    assert weekdays_label([1, 2, 3, 4, 5]) == "Mon–Fri"
    assert weekdays_label([5, 1, 3, 2, 4]) == "Mon–Fri"
    assert weekdays_label([1, 2, 3, 4, 5, 6, 7]) == "every day"
    assert weekdays_label([6, 7]) == "Sat/Sun"


# --------------------------------------------------------------------------- #
# evaluation — the four branches
# --------------------------------------------------------------------------- #
def test_peak_instant(cfg):
    # Monday 2026-10-19, 02:00 UTC → inside 01:00-04:00
    res = evaluate(cfg, datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc))
    assert res["is_peak"] is True
    assert "inside peak window" in res["reason"]


def test_peak_window_end_is_exclusive(cfg):
    res = evaluate(cfg, datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc))
    assert res["is_peak"] is False
    assert "outside" in res["reason"]


def test_utc_gap_between_windows_is_offpeak(cfg):
    # 04:00–06:00 UTC on a weekday is off-peak even though it is "work hours"
    res = evaluate(cfg, datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc))
    assert res["is_peak"] is False


def test_in_peak_window_on_weekday(cfg):
    res = evaluate(cfg, datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc))
    assert res["is_peak"] is True


def test_weekend_is_offpeak(cfg):
    # 2026-10-17 is a Saturday
    res = evaluate(cfg, datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc))
    assert res["is_peak"] is False
    assert "weekend" in res["reason"]


def test_cn_holiday_is_offpeak_in_full(cfg):
    # 2026-10-01 National Day, 02:00 UTC would otherwise be peak
    res = evaluate(cfg, datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc))
    assert res["is_peak"] is False
    assert res["is_cn_holiday"] is True
    assert "CN public holiday" in res["reason"]


def test_holiday_check_uses_china_date_not_utc_date(cfg):
    # 2026-09-24 17:00 UTC == 2026-09-25 01:00 Asia/Shanghai (Mid-Autumn)
    res = evaluate(cfg, datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc))
    assert res["cn_date"] == "2026-09-25"
    assert res["is_cn_holiday"] is True
    assert res["is_peak"] is False


def test_holidays_can_be_ignored():
    cfg = {
        "peak": {"windows_utc": ["01:00-04:00"], "weekdays": [1, 2, 3, 4, 5],
                 "exclude_cn_holidays": False},
        "cn_holidays": [{"date": "2026-10-01", "name": "National Day"}],
        "display_timezone": "Asia/Hong_Kong",
        "cn_timezone": "Asia/Shanghai",
    }
    res = evaluate(cfg, datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc))
    assert res["is_peak"] is True


def test_naive_datetime_uses_display_timezone(cfg):
    # Naive 09:30 HKT = 01:30 UTC → peak
    res = evaluate(cfg, datetime(2026, 10, 19, 9, 30))
    assert res["is_peak"] is True
    assert "HST" not in res["at_display"] and "HKT" in res["at_display"]


# --------------------------------------------------------------------------- #
# next boundary
# --------------------------------------------------------------------------- #
def test_next_boundary_flips_weekend_to_monday_peak(cfg):
    # Saturday 04:00 UTC → the next flip is Sunday→Monday 01:00 UTC
    nxt = next_boundary(cfg, datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc))
    assert nxt is not None
    assert nxt["change"] == "peak starts"
    assert nxt["at"].startswith("2026-10-19T01:00")


def test_next_boundary_from_inside_peak(cfg):
    nxt = next_boundary(cfg, datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc))
    assert nxt is not None
    assert nxt["change"] == "off-peak starts"
    assert nxt["at"].startswith("2026-10-19T04:00")


def test_humanize_delta():
    assert humanize_delta(0) == "0m"
    assert humanize_delta(5400) == "1h 30m"
    assert humanize_delta(86400 + 3600) == "1d 1h"


# --------------------------------------------------------------------------- #
# footnote parsing
# --------------------------------------------------------------------------- #
def test_parse_footnote():
    parsed = parse_footnote(FOOTNOTE_HTML)
    assert parsed is not None
    assert parsed["windows"] == ["01:00-04:00", "06:00-10:00"]
    assert parsed["weekdays"] == [1, 2, 3, 4, 5]
    assert "Peak hours are" in parsed["footnote"]


def test_parse_footnote_returns_none_without_match():
    assert parse_footnote("<p>nothing to see here</p>") is None


def test_parse_footnote_handles_en_dash():
    html = "<p>Peak hours are 01:00–04:00 UTC, Monday through Friday.</p>"
    parsed = parse_footnote(html)
    assert parsed["windows"] == ["01:00-04:00"]


def test_parse_holidays_txt(tmp_path: Path):
    f = tmp_path / "h.txt"
    f.write_text("# comment\n2026-01-01  # New Year\n\n2026-05-01\nbad line\n")
    rows = parse_holidays_txt(f)
    assert rows == [
        {"date": "2026-01-01", "name": "New Year"},
        {"date": "2026-05-01", "name": ""},
    ]


def test_build_config_merges_previous_settings():
    prev = {"display_timezone": "Europe/London",
            "peak": {"weekdays": [1, 2], "exclude_cn_holidays": False}}
    parsed = {"windows": ["01:00-04:00"], "weekdays": [], "days": "",
              "footnote": "Peak hours are ..."}
    cfg = build_config(prev, parsed, [{"date": "2026-01-01", "name": "NY"}])
    assert cfg["display_timezone"] == "Europe/London"
    assert cfg["peak"]["weekdays"] == [1, 2]
    assert cfg["peak"]["exclude_cn_holidays"] is False
    assert cfg["cn_holidays"] == [{"date": "2026-01-01", "name": "NY"}]


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #
def test_no_staleness_warning_for_covered_years():
    holidays = [{"date": "2026-01-01", "name": ""},
                {"date": "2027-01-01", "name": ""}]
    assert staleness_warnings(holidays, datetime(2026, 10, 17,
                                                 tzinfo=timezone.utc)) == []


def test_staleness_warning_when_current_year_missing():
    warnings = staleness_warnings([{"date": "2025-01-01", "name": ""}],
                                  datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert any("no 2026" in w for w in warnings)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_check_peak_exit_code(capsys):
    code = cli.main(["--at", "2026-10-19T02:00:00+00:00"])
    assert code == 0
    out = capsys.readouterr().out
    assert "PEAK" in out


def test_cli_check_offpeak_exit_code(capsys):
    code = cli.main(["--at", "2026-10-17T02:00:00+00:00"])
    assert code == 1
    assert "OFF-PEAK" in capsys.readouterr().out


def test_cli_check_json(capsys):
    code = cli.main(["--at", "2026-10-19T02:00:00+00:00", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["is_peak"] is True


def test_cli_quiet(capsys):
    cli.main(["--at", "2026-10-19T02:00:00+00:00", "--quiet"])
    assert capsys.readouterr().out.strip() == "peak"


def test_cli_show_and_holidays(capsys):
    assert cli.main(["show"]) == 0
    assert "peak (UTC)" in capsys.readouterr().out
    assert cli.main(["holidays", "--year", "2026"]) == 0
    out = capsys.readouterr().out
    assert "2026-01-01" in out
    assert "2027-" not in out


def test_cli_next(capsys):
    assert cli.main(["next", "--at", "2026-10-17T04:00:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert "peak starts" in out


def test_cli_version(capsys):
    assert cli.main(["version"]) == 0
    assert "deepseek-peak-hours" in capsys.readouterr().out


def test_cli_update_from_html(tmp_path: Path, capsys):
    html = tmp_path / "pricing.html"
    html.write_text(FOOTNOTE_HTML)
    target = tmp_path / "cfg.json"
    code = cli.main(["update", "--html", str(html), "--config", str(target)])
    assert code == 0
    cfg = json.loads(target.read_text())
    assert cfg["peak"]["windows_utc"] == ["01:00-04:00", "06:00-10:00"]
    assert "config created" in capsys.readouterr().out


def test_cli_update_second_run_is_unchanged(tmp_path: Path, capsys):
    html = tmp_path / "pricing.html"
    html.write_text(FOOTNOTE_HTML)
    target = tmp_path / "cfg.json"
    assert cli.main(["update", "--html", str(html),
                     "--config", str(target)]) == 0
    capsys.readouterr()
    assert cli.main(["update", "--html", str(html),
                     "--config", str(target)]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_cli_update_reports_change_with_exit_10(tmp_path: Path, capsys):
    target = tmp_path / "cfg.json"
    target.write_text(json.dumps({
        "schema_version": 1,
        "peak": {"windows_utc": ["00:00-01:00"], "weekdays": [1, 2, 3, 4, 5],
                 "exclude_cn_holidays": True},
        "cn_holidays": [],
    }))
    html = tmp_path / "pricing.html"
    html.write_text(FOOTNOTE_HTML)
    code = cli.main(["update", "--html", str(html), "--config", str(target)])
    assert code == cli.EXIT_CHANGED == 10
    assert "CHANGED" in capsys.readouterr().out


def test_cli_update_unparseable_html(tmp_path: Path, capsys):
    html = tmp_path / "bad.html"
    html.write_text("<p>layout changed</p>")
    code = cli.main(["update", "--html", str(html),
                     "--config", str(tmp_path / "cfg.json")])
    assert code == cli.EXIT_ERROR
    assert not (tmp_path / "cfg.json").exists()
    assert "layout may have changed" in capsys.readouterr().out


def test_cli_init_writes_user_config(tmp_path: Path, capsys):
    dest = tmp_path / "user" / "config.json"
    assert cli.main(["init", "--config", str(dest)]) == 0
    assert dest.exists()
    assert json.loads(dest.read_text())["peak"]["windows_utc"]
    # second run without --force refuses
    assert cli.main(["init", "--config", str(dest)]) == cli.EXIT_ERROR
