"""
scheduler.py
------------
Best-effort in-app automation for the daily stock sync and the weekly allocation reset.

Because the server is started manually (`/start`) and isn't guaranteed to be running at
any particular time, this scheduler is designed to be forgiving:

  * On startup and then once an hour, it runs any job that is DUE and hasn't run yet
    today. So if the machine was off at the scheduled time, the job simply catches up
    the next time the server is running.
  * Each job self-guards against running twice in the same day (via last_stock_sync_at
    / last_run_at), so the hourly re-check is safe.

Jobs:
  1. Daily stock sync  — `sync_stock` (physical stock only; never touches allocations).
  2. Weekly reset      — on the configured reset_day, auto-calculate + apply presets.

For true 24/7 automation on an always-on server, a Windows Task Scheduler task running
`python -m app.import_stock` is the alternative (see backend/README.md).
"""

import asyncio
from datetime import date

from .database import SessionLocal
from . import models


def run_due_jobs_sync() -> dict:
    """Blocking: run the daily stock sync + weekly preset apply if they are due today.
    Uses its own DB session. Safe to call repeatedly — each job guards against re-running."""
    db = SessionLocal()
    summary = {"stock_synced": False, "weekly_applied": False}
    try:
        settings = db.query(models.AllocationSettings).first()
        today = date.today()

        # 1) Daily stock sync — once per day.
        last_sync = settings.last_stock_sync_at if settings else None
        if last_sync is None or last_sync.date() < today:
            from .import_stock import sync_stock
            res = sync_stock(db)
            summary["stock_synced"] = bool(res.get("ok"))
            if not res.get("ok"):
                print(f"[SCHEDULER] stock sync skipped: {res.get('error')}")
            settings = db.query(models.AllocationSettings).first()   # refresh after commit

        # 2) Weekly reset — only on the configured reset_day, once that day.
        if settings and settings.reset_day is not None and today.weekday() == settings.reset_day:
            last_run = settings.last_run_at
            if last_run is None or last_run.date() < today:
                from .routers.allocations import auto_calculate_presets, apply_presets
                auto_calculate_presets(db)   # sets presets (and pools for new products)
                apply_presets(db)            # applies them + updates last_run_at
                summary["weekly_applied"] = True
                print("[SCHEDULER] weekly preset reset applied.")
    except Exception as e:   # never let a scheduler hiccup crash the server
        print(f"[SCHEDULER] due-jobs error: {e}")
    finally:
        db.close()
    return summary


async def daily_loop():
    """Run due jobs now (startup catch-up), then re-check every hour while the server runs.
    Blocking work is pushed to a thread so it never stalls the web server."""
    while True:
        try:
            await asyncio.to_thread(run_due_jobs_sync)
        except Exception as e:
            print(f"[SCHEDULER] loop error: {e}")
        await asyncio.sleep(3600)   # re-check hourly; jobs self-guard against re-running
