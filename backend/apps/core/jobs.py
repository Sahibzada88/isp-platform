import logging

from django.utils import timezone

from .models import JobRun

log = logging.getLogger(__name__)


def run_job(name, func):
    """Run `func()` (returns processed count) and record the outcome. Never raises."""
    run = JobRun.objects.create(name=name)
    try:
        count = func()
    except Exception as exc:  # noqa: BLE001 - job failures must be logged, not crash the cron
        log.exception("job %s failed", name)
        run.status, run.error = JobRun.Status.FAILED, str(exc)[:2000]
    else:
        run.status, run.processed_count = JobRun.Status.SUCCEEDED, count
    run.finished_at = timezone.now()
    run.save()
    return run
