"""APScheduler dispatcher for the SQLite job queue (FR-10).

The ``jobs`` table is the queue: any process (CLI, MCP, API) can add rows, and
the dispatcher runs in the web server (``smo.server.app``) or in ``smo daemon``.
A job is claimed atomically before it runs, so a publication fires at most once
even when several dispatchers run at the same time.
"""
import logging
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from ..services.metrics_service import run_metrics_poll
from ..services.scheduling_service import run_due_jobs

logger = logging.getLogger(__name__)

DISPATCH_INTERVAL_SECONDS = 30
DISPATCH_JOB_ID = "smo-dispatch-due-jobs"
METRICS_INTERVAL_HOURS = 6
METRICS_JOB_ID = "smo-poll-metrics"

_lock = threading.Lock()
_scheduler: Optional[BackgroundScheduler] = None


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_due_jobs,
        trigger="interval",
        seconds=DISPATCH_INTERVAL_SECONDS,
        id=DISPATCH_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_metrics_poll,
        trigger="interval",
        hours=METRICS_INTERVAL_HOURS,
        id=METRICS_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    with _lock:
        if _scheduler is None:
            _scheduler = build_scheduler()
        if not _scheduler.running:
            _scheduler.start()
            logger.info("Scheduler started (dispatch every %ss)", DISPATCH_INTERVAL_SECONDS)
        return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        _scheduler = None
