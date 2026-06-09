"""
Railway entrypoint — runs the web app + daily prospect pipeline via APScheduler.
"""
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def start_scheduler():
    """Background thread: run prospect pipeline daily at 07:00 UTC."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("APScheduler not installed — daily pipeline disabled")
        return

    from scripts.scheduler import run_daily

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        run_daily,
        trigger=CronTrigger(hour=7, minute=0),
        id="dd_daily_pipeline",
        name="D&D Defense Daily Prospect Pipeline",
        max_instances=1,
    )
    sched.start()
    logger.info("Scheduler started — daily pipeline at 07:00 UTC")


def start_web():
    """Start the FastAPI web app."""
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "dd_defense.webapp:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    start_scheduler()
    start_web()
