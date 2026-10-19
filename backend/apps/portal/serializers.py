from rest_framework import serializers

from apps.operations.models import Complaint

from . import services
from .models import CustomerAccount


class PortalLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class PortalPasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField()


class PortalConnectionSerializer(serializers.Serializer):
    connection_id = serializers.CharField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()
    installation_address = serializers.CharField()
    package_name = serializers.CharField(source="package.name")
    speed_mbps = serializers.IntegerField(source="package.speed_mbps")
    package_price = serializers.DecimalField(source="package.price_pkr", max_digits=10, decimal_places=2)
    next_package_name = serializers.CharField(source="next_package.name", default=None)
    charges = serializers.SerializerMethodField()

    def get_charges(self, obj):
        return services.charges_for(obj)


class PortalInvoiceSerializer(serializers.Serializer):
    number = serializers.CharField()
    status = serializers.CharField()
    connection = serializers.CharField(source="connection.connection_id")
    package_name = serializers.CharField()
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    issued_at = serializers.DateTimeField()


class PortalPaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    invoice = serializers.CharField(source="invoice.number")
    method = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    status = serializers.CharField()
    paid_at = serializers.DateTimeField()
    receipt = serializers.CharField(source="receipt.number", default=None)


class PortalReceiptSerializer(serializers.Serializer):
    number = serializers.CharField()
    status = serializers.CharField()
    invoice = serializers.CharField(source="payment.invoice.number")
    amount = serializers.DecimalField(source="payment.amount", max_digits=10, decimal_places=2)
    method = serializers.CharField(source="payment.method")
    issued_at = serializers.DateTimeField()


class PortalComplaintSerializer(serializers.ModelSerializer):
    connection = serializers.CharField(source="connection.connection_id", default=None)
    updates = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = ["number", "category", "description", "priority", "status", "connection", "created_at", "resolution_notes", "updates"]

    def get_updates(self, obj):
        # customers see status progress only; staff notes, work logs and assignee names stay internal
        return [{"at": e.created_at, "status": e.to_value} for e in obj.events.all() if e.kind in ("created", "status", "reopened") and e.to_value]


class PortalComplaintCreateSerializer(serializers.Serializer):
    connection = serializers.CharField(required=False, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=Complaint.Category.choices)
    description = serializers.CharField(max_length=2000)


class AccountSerializer(serializers.ModelSerializer):
    customer_id = serializers.CharField(source="customer.customer_id", read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)

    class Meta:
        model = CustomerAccount
        fields = ["id", "customer_id", "customer_name", "username", "is_active", "must_change_password", "last_login", "created_at"]
        read_only_fields = fields


class AccountCreateSerializer(serializers.Serializer):
    customer = serializers.CharField(help_text="customer_id")
    username = serializers.CharField(max_length=60)
    password = serializers.CharField()
