import re
import uuid
from datetime import datetime, timedelta

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
_confirm = None

# tasks that touch other people or your data need approval at schedule time
RISKY_WORDS = re.compile(
    r"\b(e-?mail|mail|send|message|msg|text|whatsapp|discord|telegram|call|calendar|event|delete|remove)\b", re.I)


def start(runner, confirm=None):
    """runner(task, approved) runs a job. confirm(question) -> bool asks the user (attended only)."""
    global _runner, _confirm
    _runner, _confirm = runner, confirm
    if not _sched.running:
        _sched.start()


# module level so SQLite can store it; default keeps old jobs working
def _fire(task: str, approved: bool = False):
    if _runner:
        _runner(task, approved)


def _next_run_time(job):
    """Return a job's next run time, including while APScheduler has it pending."""
    try:
        return job.next_run_time
    except AttributeError:
        now = datetime.now(job.trigger.timezone)
        return job.trigger.get_next_fire_time(None, now)


@tool
def schedule_task(task: str, when: str, repeat: str = "") -> str:
    """Schedule an action for later. task = full, COMPLETE, self-contained instruction WITHOUT the time words
    (e.g. 'Play Sailor Song on Spotify'). Never schedule with a missing detail: ask the user first.
    when = the time as the user said it ('11 pm', 'tomorrow 7am').
    repeat = '' (once), 'daily', 'weekdays' or 'weekly'."""
    t = dateparser.parse(when, settings={"PREFER_DATES_FROM": "future"})
    if t is None:
        return "Error: I couldn't understand that time."
    if not repeat and t <= datetime.now():
        # a time-only request means the next occurrence
        t = t + timedelta(days=1)

    # Approval is requested while the user is present, never decided by the model.
    approved = False
    risky = bool(RISKY_WORDS.search(task))
    if risky and _confirm is None:
        return "I can't schedule this risky action because no attended approval UI is available."
    if risky:
        approved = bool(_confirm(
            f"Schedule '{task}' for {t:%a %d %b, %I:%M %p}? Nobody will be asked again when it runs, "
            f"so it may send or act on its own. Allow that?"))
        if not approved:
            return "Not scheduled because you declined approval."

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
                         task, approved], id=uuid.uuid4().hex[:8], name=task)
    next_run = _next_run_time(job)
    return f"Scheduled '{task}' for {next_run:%a %d %b, %I:%M %p}."


@tool
def list_scheduled() -> str:
    """List all scheduled tasks."""
    jobs = _sched.get_jobs()
    return "\n".join(
        f"{j.id}: {j.name} at {_next_run_time(j):%a %d %b %I:%M %p}" for j in jobs
    ) or "Nothing scheduled."


@tool
def cancel_scheduled(job_id: str) -> str:
    """Cancel a scheduled task by its id (call list_scheduled first)."""
    try:
        _sched.remove_job(job_id)
        return "Cancelled."
    except Exception:
        return "Error: no task with that id."
