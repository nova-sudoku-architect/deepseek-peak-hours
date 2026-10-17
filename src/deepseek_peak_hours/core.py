"""Core logic: load the config, decide peak vs off-peak, refresh from the docs.

The rule lives in **data**, not in prose. ``config.json`` records the peak
windows, the peak weekdays and the excluded Chinese public holidays, so a
pricing change propagates to every caller at once.

DeepSeek bills 2x during peak windows; everything else — including weekends and
Chinese public holidays in full — is off-peak (half price).
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DISPLAY_TZ = "Asia/Hong_Kong"
DEFAULT_CN_TZ = "Asia/Shanghai"
PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
ENV_CONFIG = "DSPEAK_CONFIG"

PKG_DIR = Path(__file__).resolve().parent
BUNDLED_CONFIG = PKG_DIR / "data" / "config.json"
BUNDLED_HOLIDAYS = PKG_DIR / "data" / "cn_holidays.txt"

WEEKDAYS = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
    "sunday": 7, "sun": 7,
}
DAY_LABEL = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


# --------------------------------------------------------------------------- #
# config resolution
# --------------------------------------------------------------------------- #
def user_config_path() -> Path:
    """Per-user config location (honours XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "deepseek-peak-hours" / "config.json"


def resolve_config_path(explicit: str | os.PathLike | None = None) -> Path:
    """Pick the config file: --config, $DSPEAK_CONFIG, user config, bundled."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    user = user_config_path()
    if user.exists():
        return user
    return BUNDLED_CONFIG


def load_config(path: Path) -> dict:
    """Load and minimally validate the JSON config file."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"config not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"config is not valid JSON: {path}: {exc}") from None

    peak = cfg.get("peak") or {}
    if not peak.get("windows_utc"):
        raise SystemExit(f"config has no peak.windows_utc: {path}")
    cfg.setdefault("display_timezone", DEFAULT_DISPLAY_TZ)
    cfg.setdefault("cn_timezone", DEFAULT_CN_TZ)
    peak.setdefault("weekdays", [1, 2, 3, 4, 5])
    peak.setdefault("exclude_cn_holidays", True)
    cfg["peak"] = peak
    cfg.setdefault("cn_holidays", [])
    return cfg


# --------------------------------------------------------------------------- #
# window maths
# --------------------------------------------------------------------------- #
def _parse_hm(text: str) -> time:
    hh, mm = text.split(":")
    return time(int(hh), int(mm))


def window_minutes(spec: str) -> tuple[int, int]:
    """``"01:00-04:00"`` → ``(60, 240)`` minutes past midnight (UTC)."""
    start, end = spec.split("-")
    s, e = _parse_hm(start.strip()), _parse_hm(end.strip())
    return s.hour * 60 + s.minute, e.hour * 60 + e.minute


def in_window(minute: int, start: int, end: int) -> bool:
    """Half-open [start, end) test that tolerates midnight-crossing windows."""
    if start == end:            # degenerate / full-day window
        return True
    if end < start:             # window crosses midnight
        return minute >= start or minute < end
    return start <= minute < end


def weekdays_label(days: list[int]) -> str:
    days = sorted(set(int(d) for d in days))
    if days == [1, 2, 3, 4, 5]:
        return "Mon–Fri"
    if days == [1, 2, 3, 4, 5, 6, 7]:
        return "every day"
    return "/".join(DAY_LABEL.get(d, str(d)) for d in days)


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #
def evaluate(cfg: dict, at: datetime | None = None) -> dict:
    """Decide whether ``at`` (default: now) is a peak instant."""
    display_tz = ZoneInfo(cfg["display_timezone"])
    cn_tz = ZoneInfo(cfg["cn_timezone"])

    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:                       # naive → assume display tz
        at = at.replace(tzinfo=display_tz)

    utc = at.astimezone(timezone.utc)
    cn_date = at.astimezone(cn_tz).date().isoformat()

    holidays = {h["date"]: h.get("name", "") for h in cfg["cn_holidays"]}
    peak = cfg["peak"]
    windows = list(peak["windows_utc"])
    weekdays = [int(d) for d in peak["weekdays"]]
    utc_weekday = utc.isoweekday()              # 1 = Mon … 7 = Sun

    is_holiday = cn_date in holidays
    in_win = any(in_window(utc.hour * 60 + utc.minute, *window_minutes(w))
                 for w in windows)
    on_peak_day = utc_weekday in weekdays

    if peak["exclude_cn_holidays"] and is_holiday:
        is_peak, reason = False, (
            f"CN public holiday (off-peak in full): {holidays[cn_date] or cn_date}")
    elif not on_peak_day:
        is_peak, reason = False, (
            f"weekend/off-day — peak runs {weekdays_label(weekdays)} only")
    elif not in_win:
        is_peak, reason = False, (
            f"{DAY_LABEL[utc_weekday]} is a peak day but "
            f"{utc:%H:%M} UTC is outside {', '.join(windows)} UTC")
    else:
        is_peak, reason = True, f"inside peak window ({utc:%H:%M} UTC)"

    return {
        "at": at.isoformat(),
        "at_display": at.astimezone(display_tz).strftime("%a %Y-%m-%d %H:%M %Z"),
        "at_utc": utc.strftime("%a %Y-%m-%d %H:%M UTC"),
        "cn_date": cn_date,
        "is_cn_holiday": is_holiday,
        "is_peak": is_peak,
        "reason": reason,
        "peak_windows_utc": windows,
        "peak_weekdays": weekdays,
        "config_updated_at": cfg.get("updated_at"),
    }


def next_boundary(cfg: dict, at: datetime | None = None,
                  horizon_days: int = 14) -> dict | None:
    """Find the next instant where peak/off-peak flips.

    Returns ``{"at", "at_display", "change", "delta_seconds"}`` or ``None`` when
    nothing flips inside the horizon.
    """
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=ZoneInfo(cfg["display_timezone"]))
    at = at.replace(second=0, microsecond=0)

    display_tz = ZoneInfo(cfg["display_timezone"])
    current = evaluate(cfg, at)["is_peak"]

    step = timedelta(minutes=1)
    probe = at
    for _ in range(horizon_days * 24 * 60):
        probe += step
        state = evaluate(cfg, probe)["is_peak"]
        if state != current:
            return {
                "at": probe.isoformat(),
                "at_utc": probe.astimezone(timezone.utc).strftime(
                    "%a %Y-%m-%d %H:%M UTC"),
                "at_display": probe.astimezone(display_tz).strftime(
                    "%a %Y-%m-%d %H:%M %Z"),
                "change": "peak starts" if state else "off-peak starts",
                "delta_seconds": int((probe - at).total_seconds()),
            }
    return None


def humanize_delta(seconds: int) -> str:
    """``5400`` → ``"1h 30m"``."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# refreshing from the pricing page
# --------------------------------------------------------------------------- #
def _paragraphs(raw: str) -> list[str]:
    blocks = []
    for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", raw, re.S | re.I):
        txt = re.sub(r"<[^>]+>", " ", m.group(1))
        txt = re.sub(r"\s+", " ", html.unescape(txt)).strip()
        if txt:
            blocks.append(txt)
    return blocks


def parse_footnote(raw: str) -> dict | None:
    """Extract ``{footnote, windows, weekdays, days}`` from the pricing HTML."""
    foot = next((b for b in _paragraphs(raw)
                 if re.search(r"peak hours? (are|is)", b, re.I)), "")
    if not foot:
        return None

    windows = [
        f"{int(h1):02d}:{m1}-{int(h2):02d}:{m2}"
        for h1, m1, h2, m2 in re.findall(
            r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})", foot)
    ]
    if not windows:
        return None

    days_phrase = ""
    md = re.search(r"(Monday[^.,;)]*)", foot, re.I)
    if md:
        days_phrase = md.group(1).strip()

    weekdays: list[int] = []
    low = days_phrase.lower()
    if "monday" in low and "friday" in low:
        weekdays = [1, 2, 3, 4, 5]
    else:
        for word in re.findall(r"[a-z]+", low):
            if word in WEEKDAYS and WEEKDAYS[word] not in weekdays:
                weekdays.append(WEEKDAYS[word])
        weekdays.sort()

    return {"footnote": foot, "windows": windows,
            "days": days_phrase, "weekdays": weekdays}


def parse_holidays_txt(path: Path) -> list[dict]:
    """Parse ``YYYY-MM-DD  # name`` lines from the human-edited holiday file."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(?:#\s*(.*))?$", line)
        if m:
            out.append({"date": m.group(1), "name": (m.group(2) or "").strip()})
    return out


def build_config(prev: dict, parsed: dict, holidays: list[dict],
                 now: datetime | None = None) -> dict:
    """Merge a parsed pricing footnote + holiday list into a config dict."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    prev_peak = prev.get("peak") or {}
    return {
        "schema_version": 1,
        "updated_at": stamp,
        "display_timezone": prev.get("display_timezone", DEFAULT_DISPLAY_TZ),
        "cn_timezone": prev.get("cn_timezone", DEFAULT_CN_TZ),
        "peak": {
            "windows_utc": parsed["windows"],
            "weekdays": parsed["weekdays"] or prev_peak.get("weekdays", [1, 2, 3, 4, 5]),
            "exclude_cn_holidays": prev_peak.get("exclude_cn_holidays", True),
        },
        "cn_holidays": holidays or prev.get("cn_holidays", []),
        "source": {
            "url": PRICING_URL,
            "footnote": parsed["footnote"],
            "days_phrase": parsed["days"],
            "recorded_at": stamp,
        },
    }


def staleness_warnings(holidays: list[dict],
                       now: datetime | None = None) -> list[str]:
    """Warn when the human-maintained CN holiday list has no current/next year."""
    now = now or datetime.now(timezone.utc)
    years = {h["date"][:4] for h in holidays}
    this_year = str(now.year)
    next_year = str(now.year + 1)
    out = []
    if this_year not in years:
        out.append(f"CN holiday list has no {this_year} entries — "
                   f"add the State Council calendar.")
    elif next_year not in years:
        jan1 = datetime(int(next_year), 1, 1, tzinfo=timezone.utc)
        if (jan1 - now).days <= 75:
            out.append(f"CN holiday list has no {next_year} entries yet — "
                       f"add them when the State Council publishes them.")
    return out


def holidays_in_year(holidays: list[dict], year: int) -> list[dict]:
    return sorted((h for h in holidays if h["date"].startswith(str(year))),
                  key=lambda h: h["date"])


def parse_at(value: str) -> datetime:
    """Parse an ISO-8601 instant; bare dates become midnight in display tz."""
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), time(0, 0))
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
