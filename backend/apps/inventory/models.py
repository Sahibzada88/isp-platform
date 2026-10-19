from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import NoDeleteMixin

U = settings.AUTH_USER_MODEL


def _fk(to, **kw):
    return models.ForeignKey(to, on_delete=models.PROTECT, related_name="+", **kw)


class InventoryItem(NoDeleteMixin, models.Model):
    class Category(models.TextChoices):
        ROUTER_ONU = "router_onu", "Routers and ONUs"
        WIFI = "wifi", "Wi-Fi devices"
        CABLE = "cable", "Fiber and LAN cables"
        CONNECTOR = "connector", "Connectors and patch cords"
        JOINTING = "jointing", "Jointing materials"
        POLE_CLAMP = "pole_clamp", "Poles, clamps and boxes"
        INSTALL = "install", "Installation materials"
        TOOLS = "tools", "Tools and testing equipment"
        COMPUTER = "computer", "Computers and printers"
        OFFICE = "office", "Office equipment"
        OTHER = "other", "Other"

    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="inventory_items")
    category = models.CharField(max_length=16, choices=Category.choices)
    name = models.CharField(max_length=150)
    unit = models.CharField(max_length=20, default="pcs")
    is_serialized = models.BooleanField(default=False)          # tracked per device via Equipment
    minimum_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)          # usable stock
    faulty_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)   # kept apart from usable stock
    low_stock_alerted = models.BooleanField(default=False)
    low_stock_episodes = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["office_id", "name"]
        constraints = [
            models.UniqueConstraint(fields=["office", "name"], name="unique_item_name_per_office"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="no_negative_stock"),
            models.CheckConstraint(condition=Q(faulty_quantity__gte=0), name="no_negative_faulty_stock"),
        ]


class StockMovement(models.Model):
    """Append-only ledger. `item.quantity` always equals the sum of its movements."""

    class Kind(models.TextChoices):
        PURCHASE = "purchase"
        USAGE = "usage"
        USAGE_REVERSAL = "usage_reversal"
        ADJUSTMENT = "adjustment"
        RETURN_GOOD = "return_good"
        RETURN_FAULTY = "return_faulty"
        EQUIPMENT_SYNC = "equipment_sync"

    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    office = _fk("organization.Office")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=2)
    faulty_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    actor = _fk(U, null=True)
    note = models.TextField(blank=True)
    complaint = _fk("operations.Complaint", null=True, blank=True)
    task = _fk("operations.Task", null=True, blank=True)
    connection = _fk("customers.Connection", null=True, blank=True)
    idempotency_key = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["item", "idempotency_key"], condition=~Q(idempotency_key=""), name="unique_movement_key"),
        ]


class Purchase(NoDeleteMixin, models.Model):
    office = _fk("organization.Office")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="purchases")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    purchase_date = models.DateField()
    supplier_notes = models.TextField(blank=True)
    entered_by = _fk(U)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-purchase_date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="purchase_qty_positive")]


class MaterialUsage(NoDeleteMixin, models.Model):
    office = _fk("organization.Office")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="usages")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    used_by = _fk(U)
    complaint = _fk("operations.Complaint", null=True, blank=True)
    task = _fk("operations.Task", null=True, blank=True)
    connection = _fk("customers.Connection", null=True, blank=True)
    idempotency_key = models.CharField(max_length=80)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["item", "idempotency_key"], name="unique_usage_key"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="usage_qty_positive"),
        ]


class StockAdjustmentRequest(NoDeleteMixin, models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    office = _fk("organization.Office")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="adjustments")
    requested_delta = models.DecimalField(max_digits=12, decimal_places=2)
    approved_delta = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = _fk(U)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = _fk(U, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [models.CheckConstraint(condition=~Q(requested_delta=0), name="adjustment_not_zero")]


class ReturnRecord(NoDeleteMixin, models.Model):
    """Returned materials are inspected before they can rejoin usable stock."""

    class Status(models.TextChoices):
        PENDING = "pending_inspection"
        INSPECTED = "inspected"

    office = _fk("organization.Office")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="returns")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    returned_by = _fk(U)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    condition = models.CharField(max_length=10, blank=True)  # good / faulty after inspection
    inspected_by = _fk(U, null=True, blank=True)
    inspected_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="return_qty_positive")]


class Equipment(NoDeleteMixin, models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available"
        ASSIGNED = "assigned"
        FAULTY = "faulty"
        LOST = "lost"
        UNDER_REPAIR = "under_repair"
        PENDING_RETURN = "pending_return"

    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="equipment")
    office = _fk("organization.Office")
    serial_number = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE, db_index=True)
    condition = models.CharField(max_length=120, blank=True)
    assigned_connection = _fk("customers.Connection", null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["office_id", "serial_number"]
        constraints = [
            models.CheckConstraint(condition=(Q(status="assigned") & Q(assigned_connection__isnull=False))
                                   | (~Q(status="assigned") & Q(assigned_connection__isnull=True)),
                                   name="assigned_status_matches_connection"),
        ]


class EquipmentAssignment(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name="assignments")
    connection = _fk("customers.Connection")
    assigned_by = _fk(U)
    assigned_at = models.DateTimeField(auto_now_add=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-assigned_at", "-id"]
        constraints = [
            # one device can never be on two active connections
            models.UniqueConstraint(fields=["equipment"], condition=Q(returned_at__isnull=True), name="one_active_assignment_per_device"),
        ]
