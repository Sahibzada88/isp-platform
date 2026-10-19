from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import NoDeleteMixin

U = settings.AUTH_USER_MODEL


class Expense(NoDeleteMixin, models.Model):
    class Category(models.TextChoices):
        RENT = "rent"
        ELECTRICITY = "electricity"
        FUEL = "fuel"
        TRANSPORT = "transport"
        REPAIR = "repair"
        SUPPLIES = "supplies"
        EQUIPMENT = "equipment"
        OFFICE = "office"
        OTHER = "other"

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"  # stays visible with its reason

    number = models.CharField(max_length=30, unique=True, editable=False)
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="expenses")
    category = models.CharField(max_length=12, choices=Category.choices)
    description = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    expense_date = models.DateField()
    payment_method = models.CharField(max_length=16)  # cash / easypaisa / jazzcash / bank_transfer
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    entered_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [models.Index(fields=["office", "status", "expense_date"])]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive")]
