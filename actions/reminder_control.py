import json
import os
import threading
import time
from datetime import datetime

REMINDER_FILE = os.path.expanduser("~/.jarvis_reminders.json")

def _load():
    if not os.path.exists(REMINDER_FILE):
        return []
    with open(REMINDER_FILE) as f:
        return json.load(f)

def _save(data):
    with open(REMINDER_FILE, "w") as f:
        json.dump(data, f)

def reminder_control(parameters=None):
    params = parameters or {}
    action = (params.get("action") or "add").lower()
    reminders = _load()

    if action == "add":
        text = params.get("text", "")
        when = params.get("when", "")  # expects "YYYY-MM-DD HH:MM"
        reminders.append({"text": text, "when": when, "fired": False})
        _save(reminders)
        return f"Reminder set: '{text}' at {when}"
    elif action == "list":
        pending = [r for r in reminders if not r["fired"]]
        if not pending:
            return "No pending reminders."
        return "\n".join(f"- {r['text']} at {r['when']}" for r in pending)
    return "Unknown reminder action."


def start_reminder_poller(on_fire_callback):
    def loop():
        while True:
            reminders = _load()
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            changed = False
            for r in reminders:
                if not r["fired"] and r["when"] <= now:
                    r["fired"] = True
                    changed = True
                    on_fire_callback(r["text"])
            if changed:
                _save(reminders)
            time.sleep(30)
    threading.Thread(target=loop, daemon=True).start()