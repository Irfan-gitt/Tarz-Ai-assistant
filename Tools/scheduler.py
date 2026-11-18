from datetime import datetime
import uuid
import dateparser
from langchain_core.tools import tool
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

_sched = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url="sqlite:///tarz_jobs.db")},
    job_defaults={"misfire_grace_time": 900, "coalesce": True})  # late up to 15 min, run once
_runner = None


def start(runner):            # call once from main(), before the loop
    global _runner
    _runner = runner
    _sched.start()


def _fire(task: str):         # module-level so SQLite can store the reference
    if _runner:
        _runner(task)


@tool
def schedule_task(task: str, when: str, repeat: str = "") -> str:
    """Schedule an action for later. task = full self-contained instruction WITHOUT the time words
    (e.g. 'Play Sailor Song on Spotify'). when = the time as the user said it ('11 pm', 'tomorrow 7am').
    repeat = '' (once), 'daily', 'weekdays' or 'weekly'."""
    t = dateparser.parse(when, settings={"PREFER_DATES_FROM": "future"})
    if t is None:
        return "Error: I couldn't understand that time."
    if not repeat and t <= datetime.now():
        return "Error: that time already passed."
    if repeat == "daily":
        trig = CronTrigger(hour=t.hour, minute=t.minute)
    elif repeat == "weekdays":
        trig = CronTrigger(day_of_week="mon-fri", hour=t.hour, minute=t.minute)
    elif repeat == "weekly":
        trig = CronTrigger(day_of_week=t.strftime(
            "%a").lower(), hour=t.hour, minute=t.minute)
    else:
        trig = DateTrigger(t)
    job = _sched.add_job(_fire, trig, args=[
                         task], id=uuid.uuid4().hex[:8], name=task)
    return f"Scheduled '{task}' for {job.next_run_time:%a %d %b, %I:%M %p}."


@tool
def list_scheduled() -> str:
    """List all scheduled tasks."""
    jobs = _sched.get_jobs()
    return "\n".join(f"{j.id}: {j.name} at {j.next_run_time:%a %d %b %I:%M %p}" for j in jobs) or "Nothing scheduled."


@tool
def cancel_scheduled(job_id: str) -> str:
    """Cancel a scheduled task by its id (call list_scheduled first)."""
    try:
        _sched.remove_job(job_id)
        return "Cancelled."
    except Exception:
        return "Error: no task with that id."
