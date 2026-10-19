from django.conf import settings
from django.db import models

from apps.core.models import NoDeleteMixin

U = settings.AUTH_USER_MODEL


class Lead(NoDeleteMixin, models.Model):
    class Source(models.TextChoices):
        OFFICE = "office"
        PHONE = "phone"

    class Status(models.TextChoices):
        NEW = "new"
        CONTACTED = "contacted"
        INTERESTED = "interested"
        CONVERTED = "converted"
        LOST = "lost"

    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="leads")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    address = models.TextField(blank=True)  # village / address as told by the contact
    interested_package = models.ForeignKey("packages.Package", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.OFFICE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.TextField(blank=True)
    lost_reason = models.TextField(blank=True)
    converted_customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    converted_connection = models.ForeignKey("customers.Connection", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["office", "status"])]


class LeadNote(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name="follow_ups")
    author = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
