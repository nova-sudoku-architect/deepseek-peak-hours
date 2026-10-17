# deepseek-peak-hours

[![tests](https://github.com/novaclawhk/deepseek-peak-hours/actions/workflows/tests.yml/badge.svg)](https://github.com/novaclawhk/deepseek-peak-hours/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![deps](https://img.shields.io/badge/dependencies-zero-brightgreen)](#install)

**Is this instant inside DeepSeek's 2× peak-rate window?** One small CLI, driven by a JSON config, that knows about weekends and Chinese public holidays.

```console
$ dspeak
🟢 OFF-PEAK — half price
  at      : Sat 2026-10-17 12:30 HKT
            Sat 2026-10-17 04:30 UTC
  reason  : weekend/off-day — peak runs Mon–Fri only
  peak    : 01:00–04:00 & 06:00–10:00 UTC, Mon–Fri, CN holidays excluded: yes
```

DeepSeek bills **2× during peak windows** and **half price otherwise**. The catch: the peak windows are stated in **UTC**, and Chinese public holidays are off-peak **in full** — so a naive "it's 10am, I'm in peak" guess is wrong twice over. This tool answers the question from data, not from memory.

- **Exit code is the answer** — `0` peak, `1` off-peak, `2` error. Drop it straight into a shell conditional.
- **Rule lives in JSON**, not in prose — one config file, every caller agrees.
- **CN holidays built in** — 2026 calendar bundled; weekends and holidays are fully off-peak.
- **Self-refreshing** — `dspeak update` re-parses the official pricing footnote and rewrites the config.
- **Zero dependencies** — pure standard library, Python ≥ 3.9.

---

## Key concepts

Read these five and the rest of the tool is obvious.

### 1. There are exactly two billing states

Every instant on the calendar is either **peak** (2× rate) or **off-peak** (half price). There is no third state, no partial day, no per-model exception in the rule. That binary is the whole product — which is why the answer can be an exit code.

### 2. The rule is data, not code

The windows live in `config.json`, never in the Python. Code reads the config; it does not know that peak "is" 01:00–04:00. If DeepSeek changes the footnote, **one file changes** and every consumer — CLI, shell script, AI agent, cron job — agrees again. Hard-coded hours are the bug this tool exists to prevent.

### 3. Three independent gates, in order

A peak instant must pass **all three**, checked in this order. Any failure → off-peak, and the tool tells you *which* gate failed:

| # | Gate | Question | If it fails |
|---|---|---|---|
| 1 | **Holiday** | Is today a Chinese public holiday? | Off-peak **for the whole day** — stop here |
| 2 | **Weekday** | Is it Mon–Fri in UTC? | Off-peak (weekend) |
| 3 | **Window** | Is the UTC clock inside a peak window? | Off-peak (the 04:00–06:00 UTC gap) |

Order matters. A CN holiday outranks the window check: 02:00 UTC on National Day is *off-peak* even though the clock says peak.

### 4. Everything is evaluated in UTC — with one CN exception

Peak windows are UTC. Weekdays are UTC weekdays (a Monday 23:00 HKT is Monday 15:00 UTC — still a peak day). The **only** place a non-UTC calendar matters is the holiday gate, which must ask "is today a holiday **in China**?" — so it converts to `Asia/Shanghai` first. Get that boundary wrong and you misclassify the first 8 hours of every Chinese holiday.

### 5. Display timezone is presentation only

`display_timezone` (default `Asia/Hong_Kong`) changes how the answer is *printed*, never *computed*. A naive `--at` with no offset is read in that zone, because that's what a human typing `--at 09:30` means.

---

## How it works

### Decision flow — one `dspeak` check

```
                        ┌─────────────────────────┐
                        │   dspeak [--at ...]     │
                        └────────────┬────────────┘
                                     │
                          ┌──────────▼──────────┐
                          │ resolve config path │
                          │ --config → $DSPEAK_ │
                          │ CONFIG → XDG → pkg  │
                          └──────────┬──────────┘
                                     │  config.json
                          ┌──────────▼──────────┐
                          │  normalise instant  │
                          │ naive → display tz  │
                          │        → UTC        │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
     ┌────────▼────────┐   ┌─────────▼─────────┐   ┌────────▼────────┐
     │ GATE 1 holiday  │   │ GATE 2 weekday    │   │ GATE 3 window   │
     │ CN date in      │   │ UTC isoweekday    │   │ UTC minute in   │
     │ cn_holidays ?   │   │ ∈ weekdays ?      │   │ windows_utc ?   │
     └────────┬────────┘   └─────────┬─────────┘   └────────┬────────┘
              │ yes                  │ no                   │ no
              └──────────┬───────────┴──────────┬───────────┘
                         │                      │
                ┌────────▼────────┐    ┌────────▼─────────┐
                │   OFF-PEAK      │    │      PEAK        │
                │   exit 1        │    │      exit 0      │
                └─────────────────┘    └──────────────────┘
```

### Refresh flow — `dspeak update`

This is how the rule stays correct without anyone editing code.

```
  api-docs.deepseek.com/quick_start/pricing/
                 │
                 │  HTTP GET (urllib, browser UA)
                 ▼
        ┌─────────────────┐
        │  pricing HTML   │
        └────────┬────────┘
                 │  parse_footnote(): find the "Peak hours are …"
                 │  paragraph, extract HH:MM-HH:MM pairs + weekday phrase
                 ▼
        ┌─────────────────────────────────┐
        │  cn_holidays.txt  ──────────────┼──┐   human-edited list
        └────────┬────────────────────────┘  │   (State Council calendar)
                 │                            │
                 ▼                            ▼
        ┌──────────────────────────────────────────┐
        │  old config  ──▶ merge ──▶  new config   │
        └────────────────────┬─────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        unchanged         CHANGED        unparsable
        exit 0            exit 10        exit 2
        ✅ refreshed      🚨 diff shown   ⚠️ config untouched
```

The `exit 10` path is the interesting one: the fetch and parse **succeeded**, but the rule moved. A weekly cron can treat `0` as silence and `10` as "tell a human".

---

## Key terminology

| Term | Meaning |
|---|---|
| **Peak hour** | An instant billed at **2×** the base rate. The only state that costs extra. |
| **Off-peak** | Every other instant — billed at **half** the peak rate. Includes **all** weekends and **all** Chinese public holidays, in full. |
| **Peak window** | A half-open UTC interval `[start, end)` such as `01:00-04:00`. End-exclusive: 04:00 sharp is already off-peak. |
| **Window gap** | Space *between* windows on a peak day — e.g. `04:00–06:00` UTC. Weekday, but off-peak. Frequently mistaken for peak. |
| **Peak day** | A weekday listed in `weekdays` (ISO 1 = Mon … 7 = Sun). Default Mon–Fri, evaluated in **UTC**. |
| **CN public holiday** | A date on the State Council calendar. When `exclude_cn_holidays` is true, the **entire day** is off-peak regardless of the clock. |
| **Holiday gate** | The first check, evaluated in `Asia/Shanghai` — the only non-UTC calendar in the system. |
| **Display timezone** | `display_timezone` (default `Asia/Hong_Kong`). Formats output and interprets naive `--at` values. Never affects the computation. |
| **Config** | `config.json` holding `peak.windows_utc`, `peak.weekdays`, `peak.exclude_cn_holidays`, `cn_holidays`. The single source of truth. |
| **Bundled config** | The read-only copy shipped inside the package, used when the user has no config of their own. |
| **Footer / footnote** | The pricing-page paragraph beginning *"Peak hours are …"*. The machine-parsed authority for windows and weekdays. |
| **Exit contract** | `0` peak · `1` off-peak · `2` error · `10` refreshed-but-changed. Scripts should branch on these, not on stdout. |
| **Boundary** | The next instant where the state flips. Produced by `dspeak next`. |

---

## Install

```bash
# any of these works
pipx install deepseek-peak-hours
uv tool install deepseek-peak-hours
pip install deepseek-peak-hours

# or straight from source
git clone https://github.com/novaclawhk/deepseek-peak-hours
cd deepseek-peak-hours && pipx install .
```

This puts two identical commands on your `PATH`: **`dspeak`** and `deepseek-peak-hours`.

Run it without installing:

```bash
python3 -m deepseek_peak_hours --help
```

---

## Usage

### Check an instant

```bash
dspeak                                   # right now
dspeak --at 2026-10-19T09:30:00+08:00    # a specific moment
dspeak --at 2026-12-25                   # a bare date = local midnight
dspeak --quiet                           # just "peak" / "off-peak"
dspeak --json                            # machine-readable
dspeak --explain                         # + when it next flips
```

`--at` accepts ISO-8601 with an offset (`Z` is accepted too). A **naive** timestamp is interpreted in the configured display timezone (`Asia/Hong_Kong` by default), not UTC.

### When does it flip?

```bash
$ dspeak next
peak starts in 1d 20h 30m
  at : Mon 2026-10-19 09:00 HKT  (Mon 2026-10-19 01:00 UTC)
```

### Inspect the config

```bash
dspeak show          # human summary
dspeak show --json   # full config dump
dspeak holidays      # all configured CN holidays
dspeak holidays --year 2026
```

### Scripting

Exit codes make it a one-liner:

```bash
if dspeak --quiet >/dev/null; then
  echo "peak rates — deferring batch job"
else
  echo "off-peak — running now"
fi
```

Or in Python:

```python
from deepseek_peak_hours import load_config, evaluate, resolve_config_path

cfg = load_config(resolve_config_path())
print(evaluate(cfg)["is_peak"])     # True / False
```

---

## Refreshing the rule

DeepSeek occasionally edits the pricing page. Re-parse it instead of hand-editing:

```bash
dspeak update
```

It fetches <https://api-docs.deepseek.com/quick_start/pricing/>, pulls the *"Peak hours are …"* footnote, and rewrites your config.

| Result | Printed | Exit |
| --- | --- | --- |
| peak hours unchanged | `✅ peak hours unchanged …` | `0` |
| peak hours **changed** | `🚨 peak hours CHANGED` + old/new diff | **`10`** |
| page unreachable / layout changed | `⚠️ …` | `2` |

The distinct exit code `10` means "rerun succeeded, but the rule moved" — useful for a weekly cron that should ping you only when something actually changed.

```bash
# weekly, Mondays 09:00, alert only on change
0 9 * * 1  dspeak update; [ $? -eq 10 ] && notify-send "DeepSeek peak hours changed"
```

Offline / air-gapped:

```bash
curl -sL https://api-docs.deepseek.com/quick_start/pricing/ > pricing.html
dspeak update --html pricing.html
```

---

## The rule as data

`config.json` ships with the package:

```json
{
  "peak": {
    "windows_utc": ["01:00-04:00", "06:00-10:00"],
    "weekdays": [1, 2, 3, 4, 5],
    "exclude_cn_holidays": true
  },
  "cn_holidays": [{ "date": "2026-10-01", "name": "National Day" }]
}
```

- `windows_utc` — `HH:MM-HH:MM` pairs, end-exclusive, midnight-crossing supported.
- `weekdays` — ISO weekday numbers (1 = Monday … 7 = Sunday).
- `cn_holidays` — dates that make the whole day off-peak when `exclude_cn_holidays` is true.

**Config resolution order** (first hit wins):

1. `--config PATH`
2. `$DSPEAK_CONFIG`
3. `~/.config/deepseek-peak-hours/config.json` (`$XDG_CONFIG_HOME` honoured)
4. the copy bundled inside the package

Start a private copy with `dspeak init` (add `--force` to overwrite).

### Chinese holidays

The list is **human-maintained** — the State Council publishes each year's calendar, usually in early November. `dspeak update` warns when the current or next year is missing. Edit the `cn_holidays` array in your config (or `src/deepseek_peak_hours/data/cn_holidays.txt` for a fork) and re-run `dspeak update` to import it:

```
2026-10-01  # National Day
```

---

## Why a config instead of hard-coded hours

Because the hours *have* changed, and copies of a rule drift. With this tool, the AGENTS.md/reminder text points at `dspeak`, and a pricing edit propagates everywhere in one refresh — no grep-and-patch across docs.

---

## Development

```bash
git clone https://github.com/novaclawhk/deepseek-peak-hours
cd deepseek-peak-hours
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Layout:

```
src/deepseek_peak_hours/
  core.py        # config loading, window maths, evaluation, footnote parsing
  cli.py         # argparse front-end, subcommands, exit codes
  data/          # bundled config.json + cn_holidays.txt
tests/           # 35 offline tests — no network
```

Tests are pure and need no network — the parser is exercised against a captured footnote, and the evaluator against synthetic instants.

## Licence

MIT — see [LICENSE](LICENSE).
