import threading
import winsound
import time
from datetime import datetime, timedelta


def _ring(label: str, repeat: int = 3):
    print(f"\n⏰ {label}")
    notes = [523, 659, 784]
    for _ in range(repeat):
        for note in notes:
            winsound.Beep(note, 300)
            time.sleep(0.05)
        time.sleep(0.3)


def run_timer(seconds: int = 0, minutes: int = 0, label: str = "Timer") -> str:
    total = (minutes * 60) + seconds
    if total <= 0:
        return "Please provide a valid time."

    def run():
        time.sleep(total)
        _ring(f"TIMER DONE: {label}")

    threading.Thread(target=run, daemon=True).start()

    mins = total // 60
    secs = total % 60

    if mins > 0 and secs > 0:
        return f"Timer set: {mins}m {secs}s — '{label}'"
    elif mins > 0:
        return f"Timer set: {mins}m — '{label}'"
    return f"Timer set: {secs}s — '{label}'"


def run_alarm(alarm_time: str, label: str = "Alarm") -> str:
    try:
        now = datetime.now()
        target = datetime.strptime(alarm_time, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day)

        if target <= now:
            target += timedelta(days=1)

        wait_secs = (target - now).total_seconds()
        hours = int(wait_secs // 3600)
        mins = int((wait_secs % 3600) // 60)

        def run():
            time.sleep(wait_secs)
            _ring(f"ALARM: {label} — {alarm_time}", repeat=5)

        threading.Thread(target=run, daemon=True).start()

        if hours > 0:
            return f"Alarm set for {alarm_time} — '{label}' (in {hours}h {mins}m)"
        return f"Alarm set for {alarm_time} — '{label}' (in {mins}m)"

    except ValueError:
        return "Invalid time format. Use HH:MM e.g. 07:30"
