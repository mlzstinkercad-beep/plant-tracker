"""
Phone notifications for watering and fertilizing (free, no account).

How it works
  * The free app "ntfy" on your phone listens to a secret topic name.
  * This file sends a small message to that topic through https://ntfy.sh
    whenever a plant task is due. Your phone shows it like any notification.
  * It uses only built-in Python tools.

It runs as a background helper inside the app, so notifications are sent while
the app is running on your computer. You can also run it alone:

        python notifier.py
"""

import json
import os
import secrets
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from reminders import days_left, load_reminders, read_json, write_json_atomic
from translations import UI

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
DEFAULT_SETTINGS = {"enabled": False, "topic": "", "hour": 8, "lang": "en"}

# (short name, interval key, last-done key, translation key of the task name)
TASKS = (
    ("water", "water_every", "last_water", "rem_task_water"),
    ("fert", "fert_every", "last_fert", "rem_task_fert"),
)


# ----------------------------------------------------------------------------
# Small file helpers
# ----------------------------------------------------------------------------
_read_json = read_json
_write_json = write_json_atomic
_settings_lock = threading.Lock()  # the screen and the background helper both use settings


def load_settings(data_dir):
    """Read settings. The first time, make a hard-to-guess secret topic name."""
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True)
    path = data_dir / "settings.json"
    with _settings_lock:
        settings = {**DEFAULT_SETTINGS, **_read_json(path, {})}
        if not settings["topic"]:
            settings["topic"] = "plant-" + secrets.token_urlsafe(12)
            _write_json(path, settings)
    return settings


def save_settings(data_dir, settings):
    with _settings_lock:
        _write_json(Path(data_dir) / "settings.json", settings)


def text_in(lang, key):
    return UI.get(lang, {}).get(key) or UI["en"].get(key) or key


# ----------------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------------
def send_ntfy(topic, title, message, server=None, tags=("seedling",), priority=3, timeout=15):
    """Send one notification. Raises an error if it could not be sent."""
    payload = json.dumps(
        {
            "topic": topic,
            "title": title,
            "message": message,
            "tags": list(tags),
            "priority": priority,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        (server or NTFY_SERVER).rstrip("/") + "/",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return 200 <= response.status < 300


# ----------------------------------------------------------------------------
# What is due?
# ----------------------------------------------------------------------------
def pending_notifications(reminders, notified, now, hour, lang):
    """List of (key, plant, message) that should be sent now. Once per task per day."""
    if now.hour < hour:
        return []
    today = now.date()
    found = []
    for plant, record in reminders.items():
        for short, every_key, last_key, label_key in TASKS:
            try:
                left = days_left(record[last_key], record[every_key], today)
            except (KeyError, TypeError):
                continue
            key = f"{plant}|{short}"
            if left <= 0 and notified.get(key) != today.isoformat():
                found.append((key, plant, f"{text_in(lang, label_key)}: {plant}"))
    return found


def run_once(data_dir, now=None, sender=send_ntfy):
    """Check everything once and send what is due. Returns how many were sent."""
    data_dir = Path(data_dir)
    settings = load_settings(data_dir)
    if not settings["enabled"]:
        return 0
    now = now or datetime.now()
    reminders = load_reminders(data_dir / "reminders.json")
    notified = _read_json(data_dir / "notified.json", {})

    sent = 0
    title = text_in(settings["lang"], "notif_msg_title")
    for key, plant, message in pending_notifications(
        reminders, notified, now, int(settings["hour"]), settings["lang"]
    ):
        try:
            sender(settings["topic"], title, message)
        except Exception:
            continue  # no internet right now - try again next minute
        notified[key] = now.date().isoformat()
        sent += 1
    if sent:
        _write_json(data_dir / "notified.json", notified)
    return sent


def start_background(data_dir, interval=60):
    """Start a helper that checks every minute. Safe to leave running."""

    def loop():
        while True:
            try:
                run_once(data_dir)
            except Exception:
                pass
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="plant-notifier", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    print("Plant notifier running. Press Ctrl+C to stop.")
    start_background("data")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("Stopped.")
