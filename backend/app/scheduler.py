"""
scheduler.py
------------
Background scheduler: runs auto-allocation every Monday at 04:00 local time.
Started from main.py on app startup; stopped cleanly on shutdown.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler = BackgroundScheduler(timezone="Asia/Kuala_Lumpur")


def _run_allocation_job():
    """Called by APScheduler — opens its own DB session."""
    from .database import SessionLocal
    from .alloc_engine import run_auto_allocation, get_settings

    db = SessionLocal()
    try:
        settings = get_settings(db)
        if not settings.auto_renewal_enabled:
            print("[Scheduler] Auto-allocation is disabled — skipping.")
            return
        result = run_auto_allocation(db)
        print(f"[Scheduler] Weekly allocation done: {result['allocations_set']} set, "
              f"{len(result['low_stock_skipped'])} low-stock skipped.")
    except Exception as e:
        print(f"[Scheduler] ERROR during weekly allocation: {e}")
    finally:
        db.close()


def start():
    """Start the scheduler. Call once at app startup."""
    _scheduler.add_job(
        _run_allocation_job,
        trigger=CronTrigger(day_of_week="mon", hour=4, minute=0),
        id="weekly_allocation",
        replace_existing=True,
        misfire_grace_time=3600,  # run even if server was down at 4am (up to 1h late)
    )
    _scheduler.start()
    print("[Scheduler] Started — weekly allocation runs every Monday at 04:00 MYT.")


def stop():
    """Stop the scheduler cleanly on app shutdown."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
