from django.conf import settings
from django.db import models
from django.db.models import F, Q

U = settings.AUTH_USER_MODEL


class Attendance(models.Model):
    employee = models.ForeignKey(U, on_delete=models.PROTECT, related_name="attendance")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="attendance")
    date = models.DateField()  # local (Pakistan) calendar day
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "employee_id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_per_employee_per_day"),
            models.CheckConstraint(condition=Q(check_out__isnull=True) | Q(check_in__isnull=True) | Q(check_out__gte=F("check_in")),
                                   name="attendance_out_after_in"),
        ]


class AttendanceChange(models.Model):
    """Every manual correction: who, why, previous and new values."""

    attendance = models.ForeignKey(Attendance, on_delete=models.PROTECT, related_name="changes")
    changed_by = models.ForeignKey(U, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField()
    old_check_in = models.DateTimeField(null=True)
    old_check_out = models.DateTimeField(null=True)
    new_check_in = models.DateTimeField(null=True)
    new_check_out = models.DateTimeField(null=True)
    changed_at = models.DateTimeField(auto_now_add=True)


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"

    employee = models.ForeignKey(U, on_delete=models.PROTECT, related_name="leave_requests")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(U, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="leave_end_after_start")]
