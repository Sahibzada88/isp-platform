from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import NoDeleteMixin

U = settings.AUTH_USER_MODEL


def _fk(to, name, **kw):
    return models.ForeignKey(to, on_delete=models.PROTECT, related_name="+", **kw)


class PaymentCorrection(NoDeleteMixin, models.Model):
    """Request to cancel (reverse) a confirmed payment. Needs a different, authorised approver."""

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    payment = models.ForeignKey("billing.Payment", on_delete=models.PROTECT, related_name="corrections")
    office = _fk("organization.Office", "office")
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = _fk(U, "requested_by")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = _fk(U, "decided_by", null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["payment"], condition=Q(status="pending"), name="one_pending_correction_per_payment"),
        ]


class Refund(NoDeleteMixin, models.Model):
    """Money returned to a customer. The OWNER approves every refund."""

    class Status(models.TextChoices):
        REQUESTED = "requested"
        APPROVED = "approved"
        PAID = "paid"
        REJECTED = "rejected"

    payment = models.ForeignKey("billing.Payment", on_delete=models.PROTECT, related_name="refunds")
    office = _fk("organization.Office", "office")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    requested_by = _fk(U, "requested_by")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = _fk(U, "decided_by", null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    method = models.CharField(max_length=16, blank=True)          # set when paid; defaults to the original method
    exception_reason = models.TextField(blank=True)               # required if method differs from the original
    refunded_on = models.DateField(null=True, blank=True)
    paid_by = _fk(U, "paid_by", null=True, blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive")]


class OverpaymentException(NoDeleteMixin, models.Model):
    """A real-world transfer larger than the invoice. Never discarded, never auto-credited."""

    class Status(models.TextChoices):
        OPEN = "open"
        REFUND_APPROVED = "refund_approved"
        SETTLEMENT_APPROVED = "settlement_approved"
        REFUNDED = "refunded"

    office = _fk("organization.Office", "office")
    invoice = _fk("billing.Invoice", "invoice", null=True, blank=True)
    customer = _fk("customers.Customer", "customer", null=True, blank=True)
    method = models.CharField(max_length=16)
    reference = models.CharField(max_length=64, blank=True)
    amount_received = models.DecimalField(max_digits=10, decimal_places=2)
    amount_expected = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    transaction_at = models.DateTimeField()
    notes = models.TextField(blank=True)  # evidence description
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN, db_index=True)
    recorded_by = _fk(U, "recorded_by")
    recorded_at = models.DateTimeField(auto_now_add=True)
    decided_by = _fk(U, "decided_by", null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    refunded_by = _fk(U, "refunded_by", null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refund_method = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["method", "reference"], condition=~Q(reference=""),
                                    name="unique_overpayment_reference"),
            models.CheckConstraint(condition=Q(amount_received__gt=0), name="overpayment_amount_positive"),
        ]


class CashDeposit(NoDeleteMixin, models.Model):
    """A collector hands cash to the office; the manager counts it and records any variance."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted"
        RECONCILED = "reconciled"
        CANCELLED = "cancelled"

    number = models.CharField(max_length=30, unique=True, editable=False)
    office = _fk("organization.Office", "office")
    collector = _fk(U, "collector")
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2)   # sum of included receipts
    declared_amount = models.DecimalField(max_digits=12, decimal_places=2)   # what the collector says they handed in
    received_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # what the manager counted
    variance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)         # received - expected
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reconciled_by = _fk(U, "reconciled_by", null=True, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    variance_note = models.TextField(blank=True)
    cancelled_by = _fk(U, "cancelled_by", null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["office", "status"])]


class DepositItem(models.Model):
    deposit = models.ForeignKey(CashDeposit, on_delete=models.PROTECT, related_name="items")
    payment = models.ForeignKey("billing.Payment", on_delete=models.PROTECT, related_name="deposit_items")
    amount = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot
    released_at = models.DateTimeField(null=True, blank=True)       # set when the deposit is cancelled

    class Meta:
        constraints = [
            # A payment can be counted in at most one live deposit => no double counting.
            models.UniqueConstraint(fields=["payment"], condition=Q(released_at__isnull=True), name="payment_in_one_live_deposit"),
        ]
