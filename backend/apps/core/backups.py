import json
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError

from .models import BackupRequest


class BackupNotConfigured(Exception):
    pass


def request_backup(*, actor, request=None):
    """Triggers the GitHub 'workflow_dispatch' for the backup workflow. The database dump itself never runs here."""
    if not (settings.GITHUB_BACKUP_TOKEN and settings.GITHUB_REPO):
        raise BackupNotConfigured("Manual backups are not configured on this server (GITHUB_BACKUP_TOKEN / GITHUB_REPO).")
    recent = BackupRequest.objects.filter(status="requested", requested_at__gte=timezone.now() - timedelta(seconds=settings.BACKUP_MIN_INTERVAL_SECONDS)).exists()
    if recent:
        raise BusinessRuleError("A backup was requested a moment ago. Wait a few minutes before requesting another.")
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/{settings.GITHUB_BACKUP_WORKFLOW}/dispatches"
    req = urllib.request.Request(url, data=json.dumps({"ref": settings.GITHUB_BACKUP_REF}).encode(), method="POST", headers={
        "Authorization": f"Bearer {settings.GITHUB_BACKUP_TOKEN}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json", "User-Agent": "isp-platform"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok, detail = resp.status in (200, 204), f"GitHub responded {resp.status}"
    except urllib.error.HTTPError as e:
        ok, detail = False, f"GitHub responded {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        ok, detail = False, "Could not reach GitHub"
    rec = BackupRequest.objects.create(requested_by=actor, status="requested" if ok else "failed", detail=detail)
    audit.log(actor=actor, action="backup.requested" if ok else "backup.request_failed", entity=rec, request=request, after={"detail": detail})
    return rec  # the token is never logged or returned
