"""
Reminder helpers (no Streamlit in here, so they are easy to test).

Each plant has a small record like:
    {"water_every": 2, "fert_every": 14,
     "last_water": "2026-11-05", "last_fert": "2026-11-05"}

The app works out when each task is due next from the "last done" date.
(Phone notifications are in notifier.py. make_ics() below is an optional
calendar file for people who prefer a calendar app.)
"""

import json
import os
import threading
import time as clock
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


def write_json_atomic(path, data):
    """
    Save a file safely: write a private temporary copy, then swap it in.
    Two parts of the app (or two browser tabs) can save at the same time without
    clashing, and nobody ever reads a half-written file.
    """
    path = Path(path)
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(temp, path)
            return
        except PermissionError:  # Windows: the file is being read right now - wait a moment
            if attempt == 7:
                temp.unlink(missing_ok=True)
                raise
            clock.sleep(0.05)


def read_json(path, default):
    """Read a JSON file. Returns `default` if it is missing or broken."""
    for attempt in range(4):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except json.JSONDecodeError:
            return default
        except OSError:  # Windows: the file is being replaced right now - try again
            clock.sleep(0.05)
    return default


def load_reminders(path):
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def save_reminders(path, data):
    write_json_atomic(path, data)


def to_int(value, default, low, high):
    """Turn the AI's answer into a whole number between low and high."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def parse_day(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return date.today()


def ensure_reminder(reminders, plant, report):
    """Create the reminder record the first time a plant is saved."""
    if plant not in reminders:
        today = date.today().isoformat()
        reminders[plant] = {
            "water_every": to_int(report.get("water_every_days"), 3, 1, 60),
            "fert_every": to_int(report.get("fertilize_every_days"), 14, 1, 120),
            "last_water": today,
            "last_fert": today,
        }
    return reminders


def days_left(last_iso, every_days, today=None):
    """Days until the next task. 0 = today, negative = overdue."""
    today = today or date.today()
    due = parse_day(last_iso) + timedelta(days=every_days)
    return (due - today).days


def _ics_escape(text):
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def make_ics(plant, reminder, water_label, fert_label):
    """Build a calendar file with two repeating events (water, fertilize)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Plant Growth Tracker//EN",
        "CALSCALE:GREGORIAN",
    ]
    tasks = [
        (reminder["last_water"], reminder["water_every"], water_label),
        (reminder["last_fert"], reminder["fert_every"], fert_label),
    ]
    for last, every, label in tasks:
        first = parse_day(last) + timedelta(days=every)
        if first < date.today():
            first = date.today()
        start = datetime.combine(first, time(8, 0))  # 8:00 AM, phone's local time
        end = start + timedelta(minutes=15)
        title = _ics_escape(f"{label} - {plant}")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4().hex}@plant-growth-tracker",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start:%Y%m%dT%H%M%S}",
            f"DTEND:{end:%Y%m%dT%H%M%S}",
            f"RRULE:FREQ=DAILY;INTERVAL={every}",
            f"SUMMARY:{title}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{title}",
            "TRIGGER:PT0M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
