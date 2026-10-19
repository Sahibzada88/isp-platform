from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import NoDeleteMixin

U = settings.AUTH_USER_MODEL


class ImportBatch(NoDeleteMixin, models.Model):
    class Kind(models.TextChoices):
        PACKAGES = "packages"
        CUSTOMERS = "customers"

    kind = models.CharField(max_length=12, choices=Kind.choices)
    filename = models.CharField(max_length=200, blank=True)
    source_sha256 = models.CharField(max_length=64)
    source_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    rejected_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)       # already imported by an earlier run (idempotent re-run)
    conflict_rows = models.PositiveIntegerField(default=0)
    review_rows = models.PositiveIntegerField(default=0)        # imported, but a human should look at them
    opening_balance_verified = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_balance_unverified = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ImportIssue(models.Model):
    class Level(models.TextChoices):
        ERROR = "error"        # row rejected
        CONFLICT = "conflict"  # row rejected, clashes with existing/duplicate data
        REVIEW = "review"      # row imported, needs manual review

    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="issues")
    row_number = models.PositiveIntegerField()
    level = models.CharField(max_length=10, choices=Level.choices)
    code = models.CharField(max_length=40)
    message = models.TextField()
    raw = models.JSONField(default=dict)

    class Meta:
        ordering = ["row_number", "id"]


class OpeningBalance(NoDeleteMixin, models.Model):
    """Money owed before go-live. It is NOT income and never mixes with payments; no invoices/payments are invented."""

    class Status(models.TextChoices):
        UNVERIFIED = "unverified"
        VERIFIED = "verified"
        SETTLED = "settled"  # collected through an opening-balance invoice

    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="opening_balances")
    connection = models.ForeignKey("customers.Connection", null=True, blank=True, on_delete=models.PROTECT, related_name="opening_balances")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UNVERIFIED, db_index=True)
    migration_date = models.DateField()
    source_reference = models.CharField(max_length=80)  # old-system reference, e.g. the legacy connection ID
    notes = models.TextField(blank=True)
    batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="opening_balances")
    verified_by = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    invoice = models.OneToOneField("billing.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="opening_balance")
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-migration_date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="opening_balance_positive"),
            models.UniqueConstraint(fields=["customer", "source_reference"], name="one_opening_balance_per_source"),  # re-runs cannot duplicate it
        ]
