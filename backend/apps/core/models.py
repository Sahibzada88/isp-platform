from django.db import models


class NoDeleteMixin:
    """Business records are never deleted; deactivate/cancel/archive instead."""

    def delete(self, *args, **kwargs):
        raise models.ProtectedError(f"{type(self).__name__} records cannot be deleted.", {self})


class DocumentSequence(models.Model):
    """Concurrency-safe, never-reused counters (customer IDs, invoice numbers...)."""

    scope = models.CharField(max_length=40)  # e.g. "customer_id", "connection_id", "invoice"
    key = models.CharField(max_length=40, blank=True, default="")  # e.g. village/office code
    last_value = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scope", "key"], name="uniq_sequence_scope_key"),
        ]

    def __str__(self):
        return f"{self.scope}:{self.key}={self.last_value}"


class JobRun(models.Model):
    """Execution log for scheduled jobs (Vercel Cron -> protected endpoint)."""

    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    name = models.CharField(max_length=60, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    processed_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class SystemSetting(models.Model):
    """Owner-configurable settings (installation fee, financial-year start month, ...)."""

    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)


class BackupRequest(models.Model):
    """A manual backup asked for through the API (the backup itself runs in GitHub Actions, outside Vercel)."""

    class Status(models.TextChoices):
        REQUESTED = "requested"
        FAILED = "failed"

    requested_by = models.ForeignKey("accounts.Employee", on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    detail = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
