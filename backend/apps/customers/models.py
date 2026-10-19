from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import NoDeleteMixin

cnic_validator = RegexValidator(r"^\d{5}-\d{7}-\d$", "CNIC must look like 12345-1234567-1.")


def mask_cnic(value):
    return "*****-*******-" + value[-1] if value else ""


def _today():
    return timezone.localdate()


class Customer(NoDeleteMixin, models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    customer_id = models.CharField(max_length=20, unique=True, editable=False)  # permanent, never reused
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    cnic = models.CharField(max_length=15, blank=True, validators=[cnic_validator])
    permanent_address = models.TextField()
    village = models.ForeignKey("organization.Village", on_delete=models.PROTECT, related_name="customers")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="customers")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    registered_on = models.DateField(default=_today)
    notes = models.TextField(blank=True)
    legacy_id = models.CharField(max_length=40, blank=True)  # customer ID in the old system (migration reference only)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer_id"]
        indexes = [models.Index(fields=["office", "status"]), models.Index(fields=["phone"]), models.Index(fields=["cnic"])]
        constraints = [models.UniqueConstraint(fields=["legacy_id"], condition=~Q(legacy_id=""), name="unique_customer_legacy_id")]

    def __str__(self):
        return f"{self.customer_id} {self.full_name}"


class CustomerAddressHistory(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="address_history")
    old_address = models.TextField()
    new_address = models.TextField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)


class Connection(NoDeleteMixin, models.Model):
    class Status(models.TextChoices):
        PENDING_INSTALLATION = "pending_installation"
        PENDING_APPROVAL = "pending_approval"
        ACTIVE = "active"
        EXPIRED = "expired"
        SUSPENDED = "suspended"
        CANCELLED = "cancelled"

    class EquipmentReturn(models.TextChoices):
        NOT_APPLICABLE = "not_applicable"
        PENDING_RETURN = "pending_return"
        RETURNED = "returned"

    connection_id = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="connections")
    village = models.ForeignKey("organization.Village", on_delete=models.PROTECT, related_name="connections")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="connections")
    installation_address = models.TextField()
    package = models.ForeignKey("packages.Package", on_delete=models.PROTECT, related_name="connections")
    next_package = models.ForeignKey("packages.Package", null=True, blank=True, on_delete=models.PROTECT, related_name="+")  # applies at next renewal
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_INSTALLATION, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # The three activation gates. `initial_payment_confirmed_at` is written ONLY by the billing module.
    initial_payment_confirmed_at = models.DateTimeField(null=True, blank=True)
    installation_completed_at = models.DateTimeField(null=True, blank=True)
    activation_approved_at = models.DateTimeField(null=True, blank=True)
    activation_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    activated_at = models.DateTimeField(null=True, blank=True)

    installation_fee_waived = models.BooleanField(default=False)
    installation_fee_waiver_reason = models.TextField(blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    equipment_return_status = models.CharField(max_length=16, choices=EquipmentReturn.choices, default=EquipmentReturn.NOT_APPLICABLE)

    legacy_id = models.CharField(max_length=40, blank=True)  # connection ID in the old system
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["connection_id"]
        indexes = [models.Index(fields=["office", "status"]), models.Index(fields=["status", "expires_at"])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="active") | Q(expires_at__isnull=False), name="active_connection_has_expiry",
            ),
            models.UniqueConstraint(fields=["legacy_id"], condition=~Q(legacy_id=""), name="unique_connection_legacy_id"),
        ]

    def __str__(self):
        return self.connection_id


class ConnectionAddressHistory(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name="address_history")
    old_address = models.TextField()
    new_address = models.TextField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)


class OfficeTransfer(NoDeleteMixin, models.Model):
    """Moves a connection to another village/office after approval. Customer and connection IDs never change."""

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name="office_transfers")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="office_transfers")
    from_office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="+")
    to_office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="+")
    from_village = models.ForeignKey("organization.Village", on_delete=models.PROTECT, related_name="+")
    to_village = models.ForeignKey("organization.Village", on_delete=models.PROTECT, related_name="+")
    old_address = models.TextField()
    new_installation_address = models.TextField()
    move_customer = models.BooleanField(default=False)  # also move the customer record's home village/office
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    relocation_task = models.ForeignKey("operations.Task", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["connection"], condition=Q(status="pending"), name="one_pending_office_transfer")]


class OwnershipTransfer(NoDeleteMixin, models.Model):
    """New owner for a house/shop connection. The old customer and all their history stay untouched."""

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    class Handling(models.TextChoices):
        NONE_OUTSTANDING = "none_outstanding"          # nothing unpaid: the system verifies it
        OLD_OWNER_RESPONSIBLE = "old_owner_responsible"  # unpaid amounts stay with the previous owner (never moved silently)

    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name="ownership_transfers")
    from_customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="+")
    to_customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="+")
    effective_date = models.DateField()
    balance_handling = models.CharField(max_length=24, choices=Handling.choices)
    handling_note = models.TextField(blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["connection"], condition=Q(status="pending"), name="one_pending_ownership_transfer")]
