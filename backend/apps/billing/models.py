from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import NoDeleteMixin
from apps.core.terms import VALIDITY_DAYS

U = settings.AUTH_USER_MODEL


class Invoice(NoDeleteMixin, models.Model):
    """States what the customer owes. Prices are a frozen snapshot; a later package price
    change never touches an existing invoice. Never edited - void and reissue instead."""

    class Kind(models.TextChoices):
        INITIAL = "initial"      # first term of a new/reactivated connection
        RENEWAL = "renewal"
        INSTALLATION = "installation"        # installation fee billed on its own (fee mode "separate")
        OPENING_BALANCE = "opening_balance"  # collects an imported, verified opening balance

    class Status(models.TextChoices):
        OPEN = "open"
        PAYMENT_PENDING = "payment_pending"  # digital payment entered, awaiting verification
        PAID = "paid"
        VOID = "void"

    number = models.CharField(max_length=30, unique=True, editable=False)
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="invoices")
    connection = models.ForeignKey("customers.Connection", on_delete=models.PROTECT, related_name="invoices")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)

    package = models.ForeignKey("packages.Package", on_delete=models.PROTECT, related_name="+")
    package_code = models.CharField(max_length=20)
    package_name = models.CharField(max_length=100)
    speed_mbps = models.PositiveIntegerField()
    package_price = models.DecimalField(max_digits=10, decimal_places=2)
    installation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # e.g. an opening balance
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    term_days = models.PositiveSmallIntegerField(default=VALIDITY_DAYS)
    currency = models.CharField(max_length=3, default="PKR")

    issued_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    supersedes = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replaced_by")
    void_reason = models.TextField(blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-issued_at", "-id"]
        indexes = [models.Index(fields=["office", "status"]), models.Index(fields=["connection", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "kind"], condition=Q(status__in=["open", "payment_pending"]),
                name="one_unsettled_invoice_per_connection_kind",
            ),
            models.CheckConstraint(condition=Q(total_amount=F("package_price") + F("installation_fee") + F("other_charges")),
                                   name="invoice_total_matches_parts"),
            models.CheckConstraint(condition=Q(total_amount__gt=0), name="invoice_total_positive"),
        ]


class InvoiceLine(models.Model):
    class Kind(models.TextChoices):
        PACKAGE = "package"
        INSTALLATION_FEE = "installation_fee"
        OPENING_BALANCE = "opening_balance"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)


class Payment(NoDeleteMixin, models.Model):
    class Method(models.TextChoices):
        CASH = "cash"
        EASYPAISA = "easypaisa"
        JAZZCASH = "jazzcash"
        BANK = "bank_transfer"

    class Status(models.TextChoices):
        PENDING_VERIFICATION = "pending_verification"
        CONFIRMED = "confirmed"   # cash recorded by authorised staff, or digital payment verified
        REJECTED = "rejected"     # digital payment that could not be verified
        REVERSED = "reversed"     # confirmed payment cancelled through an approved correction

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="payments")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="payments")
    connection = models.ForeignKey("customers.Connection", on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=16, choices=Method.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=24, choices=Status.choices, db_index=True)
    reference = models.CharField(max_length=64, blank=True)  # digital transaction id
    paid_at = models.DateTimeField()                          # when the money moved
    entered_at = models.DateTimeField(auto_now_add=True)      # when staff typed it in
    entered_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-entered_at", "-id"]
        indexes = [models.Index(fields=["office", "status"]), models.Index(fields=["office", "paid_at"])]
        constraints = [
            # Ordinary duplicate digital references are blocked by the database itself.
            models.UniqueConstraint(
                fields=["method", "reference"],
                condition=~Q(reference="") & Q(status__in=["pending_verification", "confirmed"]),
                name="unique_active_payment_reference",
            ),
            # At most one live payment per invoice (no double payment).
            models.UniqueConstraint(
                fields=["invoice"], condition=Q(status__in=["pending_verification", "confirmed"]),
                name="one_live_payment_per_invoice",
            ),
            models.CheckConstraint(condition=Q(method="cash") | ~Q(reference=""), name="digital_payment_needs_reference"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]


class Receipt(NoDeleteMixin, models.Model):
    """Records a payment. Issued immediately (even while digital verification is pending).
    A rejected payment's receipt is marked invalidated, never deleted."""

    class Status(models.TextChoices):
        VALID = "valid"
        INVALIDATED = "invalidated"

    number = models.CharField(max_length=30, unique=True, editable=False)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="receipts")
    issued_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.VALID)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_at", "-id"]


class BillingTerm(NoDeleteMixin, models.Model):
    """One paid 30-day term of a connection (history of what was paid for and when)."""

    connection = models.ForeignKey("customers.Connection", on_delete=models.PROTECT, related_name="terms")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="terms")
    package = models.ForeignKey("packages.Package", on_delete=models.PROTECT, related_name="+")
    package_price = models.DecimalField(max_digits=10, decimal_places=2)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)  # renewal of an EXPIRED connection waits for staff confirmation
    applied = models.BooleanField(default=False)               # package switched onto the connection when the term starts
    reversed_at = models.DateTimeField(null=True, blank=True)  # set when the payment is reversed

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="term_ends_after_start"),
            # a reversed payment leaves its (reversed) term behind; a re-payment gets a fresh one
            models.UniqueConstraint(fields=["invoice"], condition=Q(reversed_at__isnull=True), name="one_live_term_per_invoice"),
        ]
