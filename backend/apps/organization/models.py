from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from apps.core.models import NoDeleteMixin

code_validator = RegexValidator(r"^[A-Z0-9]{2,10}$", "Use 2-10 uppercase letters/digits.")


class Office(NoDeleteMixin, models.Model):
    code = models.CharField(max_length=10, unique=True, validators=[code_validator])
    # Frozen at creation: invoice/receipt numbers use this, so renaming `code` never alters
    # (or collides with) existing document numbers.
    doc_prefix = models.CharField(max_length=10, unique=True, editable=False)
    name = models.CharField(max_length=120)
    is_head_office = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def save(self, *args, **kwargs):
        if not self.doc_prefix:
            prefix, i = self.code, 1
            while Office.objects.filter(doc_prefix=prefix).exists():  # old code reused by a new office
                i += 1
                prefix = f"{self.code[:8]}{i}"
            self.doc_prefix = prefix
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Village(NoDeleteMixin, models.Model):
    """`code` is the prefix of customer IDs (KHT -> KHT001). It is frozen once any
    customer exists in the village, so IDs never collide or change meaning."""

    code = models.CharField(max_length=6, unique=True, validators=[code_validator])
    name = models.CharField(max_length=120)
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="villages")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self.pk:
            old = Village.objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if old and old != self.code and self.customers.exists():
                raise ValidationError({"code": "Village code cannot change after customers exist."})
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"
