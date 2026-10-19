from django.conf import settings
from django.db import models

from apps.core.models import NoDeleteMixin


class Attachment(NoDeleteMixin, models.Model):
    """Metadata only. The Base64 content lives in AttachmentData so listings never load it."""

    class Kind(models.TextChoices):
        EXPENSE_RECEIPT = "expense_receipt"
        PAYMENT_EVIDENCE = "payment_evidence"
        EQUIPMENT_PHOTO = "equipment_photo"
        INSTALLATION_PHOTO = "installation_photo"
        COMPLAINT_ATTACHMENT = "complaint_attachment"
        OTHER = "other"

    entity_type = models.CharField(max_length=20)
    entity_id = models.BigIntegerField()
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.OTHER)
    filename = models.CharField(max_length=200)  # as supplied by the client; never used as a path
    mime_type = models.CharField(max_length=40)  # of the processed image
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()   # processed image, before Base64
    thumb_size_bytes = models.PositiveIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    original_format = models.CharField(max_length=10)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]


class AttachmentData(models.Model):
    attachment = models.OneToOneField(Attachment, on_delete=models.PROTECT, related_name="data", primary_key=True)
    data_b64 = models.TextField()    # processed image, Base64
    thumb_b64 = models.TextField()   # 256 px thumbnail, Base64
