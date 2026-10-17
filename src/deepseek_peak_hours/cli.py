"""Command-line interface for deepseek-peak-hours."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import units
from .core import (
    BUNDLED_CONFIG,
    BUNDLED_HOLIDAYS,
    PRICING_URL,
    build_config,
    config_age_days,
    evaluate,
    holidays_in_year,
    humanize_delta,
    load_config,
    next_boundary,
    parse_at,
    parse_footnote,
    parse_holidays_txt,
    peak_ranges,
    resolve_config_path,
    schedule,
    staleness_warnings,
    user_config_path,
    weekdays_label,
    window_minutes,
    writable_config_path,
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

EXIT_PEAK = 0
EXIT_OFFPEAK = 1
EXIT_ERROR = 2
EXIT_CHANGED = 10


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fmt_window(w: str) -> str:
    s, e = w.split("-")
    return f"{s}–{e}"


def _windows_summary(cfg: dict) -> str:
    return " & ".join(_fmt_window(w) for w in cfg["peak"]["windows_utc"])


def _holiday_source(cfg: dict) -> str:
    return "yes" if cfg["peak"]["exclude_cn_holidays"] else "no"


def _collect_at(args) -> datetime | None:
    if not args.at:
        return None
    try:
        return parse_at(args.at)
    except ValueError as exc:
        raise SystemExit(f"invalid --at value {args.at!r}: {exc}") from None


def _print_result(res: dict, cfg: dict) -> None:
    icon = "🔴 PEAK — 2× rates" if res["is_peak"] else "🟢 OFF-PEAK — half price"
    print(icon)
    print(f"  at      : {res['at_display']}")
    print(f"            {res['at_utc']}")
    print(f"  reason  : {res['reason']}")
    print(f"  peak    : {_fmt_window(cfg['peak']['windows_utc'][0])}", end="")
    for w in cfg["peak"]["windows_utc"][1:]:
        print(f" & {_fmt_window(w)}", end="")
    print(f" UTC, {weekdays_label(cfg['peak']['weekdays'])}, "
          f"CN holidays excluded: {_holiday_source(cfg)}")


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    cfg = load_config(resolve_config_path(args.config))
    at = _collect_at(args)
    res = evaluate(cfg, at)

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return EXIT_PEAK if res["is_peak"] else EXIT_OFFPEAK

    if args.quiet:
        print("peak" if res["is_peak"] else "off-peak")
        return EXIT_PEAK if res["is_peak"] else EXIT_OFFPEAK

    _print_result(res, cfg)

    if args.explain:
        nxt = next_boundary(cfg, at)
        if nxt:
            print(f"  next    : {nxt['change']} in {humanize_delta(nxt['delta_seconds'])} "
                  f"→ {nxt['at_display']}")
    return EXIT_PEAK if res["is_peak"] else EXIT_OFFPEAK


def cmd_show(args) -> int:
    path = resolve_config_path(args.config)
    cfg = load_config(path)
    peak = cfg["peak"]
    print(f"config      : {path}")
    print(f"updated_at  : {cfg.get('updated_at')}")
    age = config_age_days(cfg)
    if age is not None:
        fresh = "stale — run `dspeak update`" if age > 45 else "fresh"
        print(f"age         : {age:.1f} days ({fresh})")
    print(f"peak (UTC)  : {' & '.join(peak['windows_utc'])}")
    print(f"peak days   : {weekdays_label(peak['weekdays'])}")
    print(f"CN holidays : {len(cfg['cn_holidays'])} "
          f"({'off-peak in full' if peak['exclude_cn_holidays'] else 'IGNORED'})")
    print(f"display tz  : {cfg['display_timezone']}")
    src = cfg.get("source") or {}
    if src.get("url"):
        print(f"source      : {src['url']}")
    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


def cmd_holidays(args) -> int:
    cfg = load_config(resolve_config_path(args.config))
    rows = holidays_in_year(cfg["cn_holidays"], args.year) if args.year \
        else sorted(cfg["cn_holidays"], key=lambda h: h["date"])
    if not rows:
        print(f"no holidays configured for {args.year}")
        return 0
    for h in rows:
        print(f"{h['date']}  {h.get('name', '')}".rstrip())
    return 0


def cmd_next(args) -> int:
    cfg = load_config(resolve_config_path(args.config))
    at = _collect_at(args)
    nxt = next_boundary(cfg, at)
    if not nxt:
        print("no peak/off-peak change within the horizon")
        return 0
    if args.json:
        print(json.dumps(nxt, ensure_ascii=False))
        return 0
    print(f"{nxt['change']} in {humanize_delta(nxt['delta_seconds'])}")
    print(f"  at : {nxt['at_display']}  ({nxt['at_utc']})")
    return 0


def cmd_schedule(args) -> int:
    """Print the peak schedule for the next N days."""
    from datetime import date as _date
    from zoneinfo import ZoneInfo

    cfg = load_config(resolve_config_path(args.config))
    tz = ZoneInfo("UTC" if args.utc else cfg["display_timezone"])

    start = None
    if args.from_date:
        try:
            start = parse_at(args.from_date).date()
        except ValueError as exc:
            raise SystemExit(f"invalid --from value {args.from_date!r}: {exc}") from None
    days = schedule(cfg, start, args.days, tz)

    if args.json:
        print(json.dumps({"timezone": str(tz), "days": days}, ensure_ascii=False))
        return 0

    label = "UTC" if args.utc else cfg["display_timezone"]
    print(f"DeepSeek peak schedule — {label}")
    print(f"rule: {' & '.join(_fmt_window(w) for w in cfg['peak']['windows_utc'])} "
          f"UTC · {weekdays_label(cfg['peak']['weekdays'])} · "
          f"CN holidays off-peak in full")
    print()

    for day in days:
        stamp = _date.fromisoformat(day["date"]).strftime("%a %Y-%m-%d")
        if not day["has_peak"]:
            note = "CN holiday" if day["is_cn_holiday"] else "off-peak all day"
            print(f"  {stamp}  {note:<40} 🟢 —")
            continue
        spans = ", ".join(f"{r['start_local']}–{r['end_local']}"
                          for r in day["ranges"])
        flag = " (CN holiday)" if day["is_cn_holiday"] else ""
        print(f"  {stamp}  {spans + flag:<40} 🔴 {day['peak_duration']}")

    total = sum(d["peak_seconds"] for d in days)
    print()
    print(f"  total peak time: {humanize_delta(total)} over {len(days)} days")
    return 0


def _units_cfg(args):
    from .units import UnitPaths
    paths = UnitPaths(Path(args.service_file), Path(args.timer_file)) \
        if args.service_file else None
    return paths


def cmd_service(args) -> int:
    """Install / remove / inspect the weekly refresh timer."""
    action = args.service_action or "status"
    paths = _units_cfg(args)

    if action == "show":
        cfg_path = writable_config_path(args.config)
        print(units.render_service(sys.executable, cfg_path, None).rstrip())
        print()
        print(units.render_timer(args.day, units.normalise_time(args.at)).rstrip())
        return 0

    if action == "install":
        cfg_path = writable_config_path(args.config)
        try:
            res = units.install(args.day, args.at, config=cfg_path,
                                paths=paths, enable=not args.no_enable,
                                random_delay=args.random_delay)
        except units.UnitError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

        print(f"📝 wrote {res['paths'].service}")
        print(f"📝 wrote {res['paths'].timer}")
        print(f"⏰ schedule: {res['day']} {res['at']} "
              f"(±{args.random_delay}s jitter), Persistent=true")
        if res["enabled"]:
            print("✅ timer enabled and started")
        elif args.no_enable:
            print("ℹ️  --no-enable: units written but not activated")
        for msg in res["messages"]:
            if msg:
                print(f"   {msg}")
        print("\ncheck it with:  dspeak service status")
        print("trigger once:   systemctl --user start "
              f"{units.SERVICE_NAME}")
        return 0

    if action == "uninstall":
        res = units.uninstall(paths, disable=not args.no_disable)
        for p in res["removed"]:
            print(f"🗑  removed {p}")
        if not res["removed"]:
            print("nothing to remove")
        for msg in res["messages"]:
            print(f"   {msg}")
        return 0

    if action == "run":
        code, out = units.systemctl(["start", units.SERVICE_NAME])
        if code != 0:
            print(f"error: {out}", file=sys.stderr)
            return EXIT_ERROR
        print("▶️  triggered a refresh; follow it with:")
        print(f"   journalctl --user -u {units.SERVICE_NAME} -n 30")
        return 0

    # status
    info = units.status(paths)
    if not info["installed"]:
        print("no units installed — run: dspeak service install")
        return EXIT_ERROR
    print(f"service : {info['paths'].service}")
    print(f"timer   : {info['paths'].timer}")
    print(f"enabled : {info['timer_enabled']}")
    print(f"active  : {info['timer_active']}")
    if info["next_run"]:
        print(f"next    : {info['next_run']}")
    if info["last_run"]:
        print(f"last    : {info['last_run']}")
    return 0


def _fetch(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def cmd_update(args) -> int:
    """Refresh the config from the pricing page (or a saved HTML dump)."""
    if args.html:
        raw = Path(args.html).read_text(encoding="utf-8", errors="replace")
    else:
        if args.url != PRICING_URL:
            target_url = args.url
        else:
            target_url = PRICING_URL
        try:
            raw = _fetch(target_url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"⚠️ could not fetch the pricing page ({exc}) — "
                  f"config left unchanged.")
            return EXIT_ERROR

    parsed = parse_footnote(raw)
    if not parsed:
        print("⚠️ could not find a 'Peak hours are …' footnote on the page — "
              "config left unchanged (page layout may have changed).")
        return EXIT_ERROR

    cfg_path = writable_config_path(args.config)
    holidays_file = (Path(args.holidays_file).expanduser() if args.holidays_file
                     else BUNDLED_HOLIDAYS)

    prev: dict = {}
    if cfg_path.exists():
        try:
            prev = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}

    holidays = parse_holidays_txt(holidays_file)
    cfg = build_config(prev, parsed, holidays)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    old_windows = (prev.get("peak") or {}).get("windows_utc")
    old_weekdays = (prev.get("peak") or {}).get("weekdays")
    new_windows = cfg["peak"]["windows_utc"]
    new_weekdays = cfg["peak"]["weekdays"]

    changed = False
    if old_windows is None:
        print(f"📌 config created: {' & '.join(new_windows)} UTC, "
              f"{weekdays_label(new_weekdays)}; "
              f"{len(cfg['cn_holidays'])} CN holidays → {cfg_path}")
    elif old_windows != new_windows or old_weekdays != new_weekdays:
        changed = True
        print("🚨 peak hours CHANGED — config updated.")
        print(f"   old: {' & '.join(old_windows or [])} UTC, "
              f"{weekdays_label(old_weekdays or [])}")
        print(f"   new: {' & '.join(new_windows)} UTC, "
              f"{weekdays_label(new_weekdays)}")
        print(f"   footnote: {parsed['footnote']}")
        print("   → update any peak-hour reminders you keep in docs/agent config.")
    else:
        print(f"✅ peak hours unchanged ({' & '.join(new_windows)} UTC, "
              f"{weekdays_label(new_weekdays)}); config refreshed → {cfg_path}")

    for warning in staleness_warnings(cfg["cn_holidays"]):
        print(f"⚠️ {warning}")

    return EXIT_CHANGED if changed else 0


def cmd_init(args) -> int:
    """Write a user config (copy of the bundled one) to $XDG_CONFIG_HOME."""
    dest = Path(args.config).expanduser() if args.config else user_config_path()
    if dest.exists() and not args.force:
        print(f"config already exists: {dest} (use --force to overwrite)")
        return EXIT_ERROR
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLED_CONFIG, dest)
    print(f"wrote {dest}")
    print(f"holidays template: {BUNDLED_HOLIDAYS}")
    return 0


def cmd_version(_args) -> int:
    print(f"deepseek-peak-hours {__version__}")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", metavar="PATH",
                        help="config JSON (default: $DSPEAK_CONFIG, then "
                             "~/.config/deepseek-peak-hours/config.json, "
                             "then the bundled copy)")

    ap = argparse.ArgumentParser(
        prog="dspeak",
        description="Is this instant inside DeepSeek's 2x peak-rate window? "
                    "Exit 0 = peak, 1 = off-peak, 2 = error.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  dspeak                                  # am I in peak right now?\n"
            "  dspeak --at 2026-10-19T09:30:00+08:00   # what about next Monday?\n"
            "  dspeak --json                           # machine-readable\n"
            "  dspeak next                             # when does it flip?\n"
            "  dspeak update                           # refresh from the docs\n"
        ),
    )
    ap.add_argument("--version", action="version",
                    version=f"deepseek-peak-hours {__version__}")
    # Top-level check flags (so bare `dspeak --json` works).
    ap.add_argument("--at", default="",
                    help="ISO-8601 instant to test (default: now)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true",
                    help="print only 'peak' or 'off-peak'")
    ap.add_argument("--explain", action="store_true",
                    help="also show when the next flip happens")
    ap.add_argument("--config", metavar="PATH",
                    help="config JSON path (see subcommands for the default)")

    sub = ap.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", parents=[common],
                             help="decide for now (default)")
    p_check.add_argument("--at", default="",
                         help="ISO-8601 instant to test (default: now)")
    p_check.add_argument("--json", action="store_true")
    p_check.add_argument("--quiet", action="store_true")
    p_check.add_argument("--explain", action="store_true")

    sub.add_parser("show", parents=[common], help="print the loaded config")
    p_hol = sub.add_parser("holidays", parents=[common],
                           help="list configured CN holidays")
    p_hol.add_argument("--year", type=int, help="only this year")

    p_next = sub.add_parser("next", parents=[common],
                            help="when does peak/off-peak flip?")
    p_next.add_argument("--at", default="")
    p_next.add_argument("--json", action="store_true")

    p_upd = sub.add_parser("update", parents=[common],
                           help="refresh config from the pricing page")
    p_upd.add_argument("--html", help="parse a saved pricing-page HTML dump")
    p_upd.add_argument("--url", default=PRICING_URL, help="pricing page URL")
    p_upd.add_argument("--holidays-file",
                       help="CN holiday list to import "
                            "(default: the bundled cn_holidays.txt)")

    p_init = sub.add_parser("init", parents=[common],
                           help="write a user config from the bundled one")
    p_init.add_argument("--force", action="store_true", help="overwrite")

    p_sched = sub.add_parser("schedule", parents=[common],
                             help="print the peak schedule for the next days")
    p_sched.add_argument("--days", type=int, default=7,
                         help="how many days to show (default: 7)")
    p_sched.add_argument("--from", dest="from_date", default="",
                         help="start date YYYY-MM-DD (default: today)")
    p_sched.add_argument("--utc", action="store_true",
                         help="show times in UTC instead of the display tz")
    p_sched.add_argument("--json", action="store_true")

    p_svc = sub.add_parser("service", parents=[common],
                           help="install/remove the weekly refresh systemd timer")
    p_svc.add_argument("service_action", nargs="?", default="status",
                       choices=["install", "uninstall", "status", "run", "show"],
                       help="default: status")
    p_svc.add_argument("--day", default="Mon",
                       help="weekday for the weekly run (default: Mon)")
    p_svc.add_argument("--at", default="09:00",
                       help="local time HH:MM (default: 09:00)")
    p_svc.add_argument("--random-delay", type=int, default=300,
                       help="RandomizedDelaySec (default: 300)")
    p_svc.add_argument("--no-enable", action="store_true",
                       help="write the units without enabling them")
    p_svc.add_argument("--no-disable", action="store_true",
                       help="uninstall: leave the timer enabled")
    p_svc.add_argument("--service-file", help=argparse.SUPPRESS)
    p_svc.add_argument("--timer-file", help=argparse.SUPPRESS)

    sub.add_parser("version", help="print the version")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    handlers = {
        None: cmd_check,
        "check": cmd_check,
        "show": cmd_show,
        "holidays": cmd_holidays,
        "next": cmd_next,
        "schedule": cmd_schedule,
        "service": cmd_service,
        "update": cmd_update,
        "init": cmd_init,
        "version": cmd_version,
    }

    # Bare invocation carries its own flags; subcommands do too.
    for attr, default in (("at", ""), ("json", False),
                          ("quiet", False), ("explain", False)):
        if not hasattr(args, attr):
            setattr(args, attr, default)

    try:
        return handlers[args.cmd](args)
    except SystemExit as exc:                        # config errors
        if isinstance(exc.code, str):
            print(f"error: {exc.code}", file=sys.stderr)
            return EXIT_ERROR
        raise
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
