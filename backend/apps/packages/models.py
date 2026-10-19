from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import NoDeleteMixin
from apps.core.terms import VALIDITY_DAYS


class Package(NoDeleteMixin, models.Model):
    """Central catalogue shared by all offices. Packages are deactivated, never deleted.
    Changing speed/price here never changes router config nor historical invoices
    (invoices will store their own snapshot)."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    speed_mbps = models.PositiveIntegerField()
    price_pkr = models.DecimalField(max_digits=10, decimal_places=2)
    validity_days = models.PositiveSmallIntegerField(default=VALIDITY_DAYS, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["speed_mbps", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(price_pkr__gt=0), name="package_price_positive"),
            models.CheckConstraint(condition=Q(speed_mbps__gt=0), name="package_speed_positive"),
            models.CheckConstraint(condition=Q(validity_days=VALIDITY_DAYS), name="package_validity_30_days"),
        ]

    def __str__(self):
        return f"{self.code} ({self.speed_mbps} Mbps, PKR {self.price_pkr})"
