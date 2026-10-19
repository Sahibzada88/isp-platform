from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    OFFICE_STAFF = "office_staff", "Office Staff"
    TECHNICIAN = "technician", "Technician"
    LABOUR = "labour", "Labour"
    COLLECTOR = "collector", "Collector"


class Employee(AbstractUser):
    """Staff account. Role is a *template*; actual access comes from PermissionGrant rows."""

    employee_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OFFICE_STAFF)
    joining_date = models.DateField(null=True, blank=True)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    disabled_at = models.DateTimeField(null=True, blank=True)
    disabled_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    REQUIRED_FIELDS = ["email"]

    @property
    def is_owner(self):
        return self.role == Role.OWNER and self.is_active

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Employees are disabled, never deleted.", {self})


class OfficeAssignment(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assignments")
    office = models.ForeignKey("organization.Office", on_delete=models.PROTECT, related_name="assignments")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "office"], name="uniq_employee_office")]


class PermissionGrant(models.Model):
    """A permission held by an employee in ONE office (office=NULL => global permission)."""

    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="permission_grants")
    office = models.ForeignKey("organization.Office", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=60)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "office", "code"], name="uniq_grant_office"),
            models.UniqueConstraint(
                fields=["employee", "code"], condition=Q(office__isnull=True), name="uniq_grant_global"
            ),
        ]
