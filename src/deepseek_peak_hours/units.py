"""systemd user units — a weekly ``dspeak update`` timer, installed by the CLI.

Everything here is plain text generation plus ``systemctl --user`` calls, so it
works on any systemd box (Linux) without root. ``macOS``/launchd is out of scope.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

UNIT_NAME = "deepseek-peak-hours-update"
SERVICE_NAME = f"{UNIT_NAME}.service"
TIMER_NAME = f"{UNIT_NAME}.timer"

DAY_ALIASES = {
    "mon": "Mon", "monday": "Mon",
    "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
    "wed": "Wed", "wednesday": "Wed",
    "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu",
    "fri": "Fri", "friday": "Fri",
    "sat": "Sat", "saturday": "Sat",
    "sun": "Sun", "sunday": "Sun",
}


class UnitError(RuntimeError):
    """Raised when systemd units cannot be written or driven."""


@dataclass
class UnitPaths:
    service: Path
    timer: Path

    @classmethod
    def default(cls) -> "UnitPaths":
        base = Path.home() / ".config" / "systemd" / "user"
        return cls(base / SERVICE_NAME, base / TIMER_NAME)


def normalise_day(value: str) -> str:
    key = value.strip().lower()
    if key not in DAY_ALIASES:
        raise UnitError(
            f"unknown weekday {value!r} "
            f"(use one of: Mon Tue Wed Thu Fri Sat Sun)")
    return DAY_ALIASES[key]


def normalise_time(value: str) -> str:
    """Accept ``09:00`` / ``9:00`` / ``0900`` → ``09:00``."""
    text = value.strip().replace(".", ":")
    if ":" in text:
        hh, mm = text.split(":", 1)
    elif len(text) == 4 and text.isdigit():
        hh, mm = text[:2], text[2:]
    else:
        hh, mm = text, "00"
    try:
        h, m = int(hh), int(mm)
    except ValueError:
        raise UnitError(f"invalid time {value!r} (expected HH:MM)") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise UnitError(f"invalid time {value!r} (expected 00:00–23:59)")
    return f"{h:02d}:{m:02d}"


def render_service(python_exe: str, config: Path | None,
                   holidays: Path | None) -> str:
    """The oneshot service that runs ``dspeak update``."""
    cmd = f"{python_exe} -m deepseek_peak_hours update"
    if config:
        cmd += f" --config {config}"
    if holidays:
        cmd += f" --holidays-file {holidays}"

    env_lines = []
    if config:
        env_lines.append(f'Environment="{ENV_CONFIG_NAME}={config}"')

    return "\n".join([
        "[Unit]",
        "Description=Refresh DeepSeek peak-hours config from the pricing page",
        "Documentation=https://github.com/nova-sudoku-architect/deepseek-peak-hours",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={cmd}",
        "# exit 10 = fetched and parsed fine, but the peak rule changed.",
        "# Treat it as success; the change is reported in the journal.",
        "SuccessExitStatus=0 10",
        *(env_lines or []),
        "Nice=10",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


ENV_CONFIG_NAME = "DSPEAK_CONFIG"


def render_timer(day: str, at: str, random_delay: int = 300) -> str:
    """Weekly timer — ``OnCalendar=Mon 09:00`` with a small jitter."""
    return "\n".join([
        "[Unit]",
        "Description=Weekly DeepSeek peak-hours config refresh",
        "",
        "[Timer]",
        f"OnCalendar={day} {at}",
        "# Catch up after the machine was asleep at the scheduled minute.",
        "Persistent=true",
        f"RandomizedDelaySec={random_delay}",
        "AccuracySec=1min",
        "Unit=" + SERVICE_NAME,
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def systemctl(args: list[str]) -> tuple[int, str]:
    return _run(["systemctl", "--user", *args])


def systemd_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    code, _ = systemctl(["is-system-running"])
    return code == 0


def install(day: str, at: str, config: Path | None = None,
            holidays: Path | None = None, paths: UnitPaths | None = None,
            enable: bool = True, random_delay: int = 300) -> dict:
    """Write the units and optionally ``enable --now`` the timer."""
    paths = paths or UnitPaths.default()
    day = normalise_day(day)
    at = normalise_time(at)

    paths.service.parent.mkdir(parents=True, exist_ok=True)
    paths.service.write_text(
        render_service(sys.executable, config, holidays), encoding="utf-8")
    paths.timer.write_text(
        render_timer(day, at, random_delay), encoding="utf-8")

    result = {"paths": paths, "day": day, "at": at,
              "enabled": False, "messages": []}

    if not enable:
        return result
    if os.uname().sysname != "Linux" or not shutil.which("systemctl"):
        result["messages"].append(
            "systemctl not found — units written but not enabled.")
        return result

    code, out = systemctl(["daemon-reload"])
    if code != 0:
        raise UnitError(f"systemctl --user daemon-reload failed: {out}")
    code, out = systemctl(["enable", "--now", TIMER_NAME])
    if code != 0:
        raise UnitError(f"systemctl --user enable --now {TIMER_NAME} failed: {out}")
    result["enabled"] = True
    result["messages"].append(out)

    # A user timer only fires while the user manager runs — hint at lingering.
    code, linger = _run(["loginctl", "show-user", os.environ.get("USER", ""),
                         "--property=Linger", "--value"])
    if code == 0 and linger.strip().lower() == "no":
        result["messages"].append(
            "hint: enable lingering so the timer also fires while you are "
            f"logged out →  sudo loginctl enable-linger {os.environ.get('USER', '$USER')}")
    return result


def uninstall(paths: UnitPaths | None = None,
              disable: bool = True) -> dict:
    paths = paths or UnitPaths.default()
    result = {"paths": paths, "removed": [], "messages": []}

    if disable and shutil.which("systemctl"):
        systemctl(["disable", "--now", TIMER_NAME])
        result["messages"].append("timer disabled")

    for p in (paths.timer, paths.service):
        if p.exists():
            p.unlink()
            result["removed"].append(p)

    if shutil.which("systemctl"):
        systemctl(["daemon-reload"])
    return result


def status(paths: UnitPaths | None = None) -> dict:
    paths = paths or UnitPaths.default()
    info = {
        "paths": paths,
        "installed": paths.timer.exists() and paths.service.exists(),
        "timer_enabled": None,
        "timer_active": None,
        "next_run": None,
        "last_run": None,
        "raw": "",
    }
    if not shutil.which("systemctl"):
        return info

    code, out = systemctl(["is-enabled", TIMER_NAME])
    info["timer_enabled"] = out.strip() if code == 0 else "disabled"

    code, out = systemctl(["is-active", TIMER_NAME])
    info["timer_active"] = out.strip() if code == 0 else "inactive"

    code, out = systemctl(["list-timers", TIMER_NAME, "--no-pager",
                           "--output=json"])
    if code == 0 and out:
        import json
        try:
            rows = json.loads(out)
            if rows:
                info["next_run"] = rows[0].get("next")
                info["last_run"] = rows[0].get("last")
        except (ValueError, KeyError):
            pass

    info["raw"] = out
    return info
