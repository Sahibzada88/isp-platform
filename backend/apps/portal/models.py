from django.conf import settings
from django.db import models

from apps.core.models import NoDeleteMixin


class CustomerAccount(NoDeleteMixin, models.Model):
    """Portal login for a customer. Created and reset by staff only; customers cannot self-register."""

    customer = models.OneToOneField("customers.Customer", on_delete=models.PROTECT, related_name="portal_account")
    username = models.CharField(max_length=60, unique=True)
    password = models.CharField(max_length=128)  # Django password hash
    is_active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=True)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
