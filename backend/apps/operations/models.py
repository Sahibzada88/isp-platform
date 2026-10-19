from django.conf import settings
from django.db import models

from apps.core.models import NoDeleteMixin

U = settings.AUTH_USER_MODEL


class Complaint(NoDeleteMixin, models.Model):
    class Category(models.TextChoices):
        INTERNET_DOWN = "internet_down", "Internet down"
        SLOW_SPEED = "slow_speed", "Slow speed"
        CABLE_DAMAGE = "cable_damage", "Cable damage"
        ROUTER_ISSUE = "router_issue", "Router issue"
        PAYMENT_ISSUE = "payment_issue", "Payment-related issue"
        NEW_CONNECTION = "new_connection", "New connection / installation request"
        OTHER = "other", "Other technical problem"

    class Priority(models.TextChoices):
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"
        URGENT = "urgent"

    class Status(models.TextChoices):
        NEW = "new"
        ASSIGNED = "assigned"
        IN_PROGRESS = "in_progress"
        WAITING = "waiting"
        RESOLVED = "resolved"
        CLOSED = "closed"
        REOPENED = "reopened"

    class Source(models.TextChoices):
        OFFICE = "office"
        PHONE = "phone"
        WHATSAPP = "whatsapp"   # recorded manually, no API integration
        PORTAL = "portal"

    number = models.CharField(max_length=30, unique=True, editable=False)
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="complaints")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="complaints")
    connection = models.ForeignKey("customers.Connection", null=True, blank=True, on_delete=models.PROTECT, related_name="complaints")
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.TextField()
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.OFFICE)
    waiting_reason = models.TextField(blank=True)
    resolution_notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_complaints")
    created_by = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")  # null: portal / system
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reopen_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["office", "status"]), models.Index(fields=["assigned_to", "status"])]


class ComplaintEvent(models.Model):
    """Append-only history of everything that happens to a complaint."""

    complaint = models.ForeignKey(Complaint, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=20)  # created/assigned/status/priority/work_log/reopened/unassigned
    actor = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actor_label = models.CharField(max_length=150, blank=True)
    from_value = models.CharField(max_length=60, blank=True)
    to_value = models.CharField(max_length=60, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class ComplaintWorkLog(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.PROTECT, related_name="work_logs")
    author = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    notes = models.TextField()
    materials_used = models.TextField(blank=True)   # free-text note; stock is deducted via the inventory module
    equipment_used = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Task(NoDeleteMixin, models.Model):
    class Kind(models.TextChoices):
        COMPLAINT = "complaint"
        INSTALLATION = "installation"
        CABLE_REPAIR = "cable_repair"
        EQUIPMENT_DELIVERY = "equipment_delivery"
        STOCK_DELIVERY = "stock_delivery"
        OFFICE_WORK = "office_work"
        OTHER = "other"

    class Status(models.TextChoices):
        TODO = "todo"
        IN_PROGRESS = "in_progress"
        DONE = "done"
        CANCELLED = "cancelled"

    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    priority = models.CharField(max_length=8, choices=Complaint.Priority.choices, default=Complaint.Priority.MEDIUM)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO, db_index=True)
    assigned_to = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_tasks")
    due_date = models.DateField(null=True, blank=True)
    complaint = models.ForeignKey(Complaint, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    connection = models.ForeignKey("customers.Connection", null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    completion_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["office", "status"]), models.Index(fields=["assigned_to", "status"])]
