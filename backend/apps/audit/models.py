from django.conf import settings
from django.db import models


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit records are immutable.")

    def delete(self):
        raise PermissionError("Audit records cannot be deleted.")


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actor_label = models.CharField(max_length=150, blank=True)  # username snapshot ("system" for jobs)
    action = models.CharField(max_length=80, db_index=True)
    entity_type = models.CharField(max_length=60)
    entity_id = models.CharField(max_length=64)
    office = models.ForeignKey("organization.Office", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField(blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["office", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit records cannot be deleted.")
