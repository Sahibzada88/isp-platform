import re

from rest_framework import serializers

from apps.organization.models import Office, Village
from apps.packages.models import Package

from .models import Connection, Customer, mask_cnic


def normalize_cnic(value):
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if len(digits) != 13:
        raise serializers.ValidationError("CNIC must contain 13 digits.")
    return f"{digits[:5]}-{digits[5:12]}-{digits[12]}"


class CustomerSerializer(serializers.ModelSerializer):
    """CNIC is masked unless the caller holds customers.view_cnic for that customer's office."""

    village_code = serializers.CharField(source="village.code", read_only=True)
    office_code = serializers.CharField(source="office.code", read_only=True)
    cnic = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "customer_id", "full_name", "phone", "cnic", "permanent_address", "village", "village_code",
                  "office", "office_code", "status", "registered_on", "notes", "created_at", "updated_at"]
        read_only_fields = fields

    def get_cnic(self, obj):
        scope = self.context.get("cnic_scope")
        return obj.cnic if scope is not None and obj.office_id in scope else mask_cnic(obj.cnic)


class CustomerCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=20)
    cnic = serializers.CharField(required=False, allow_blank=True, default="")
    permanent_address = serializers.CharField()
    village = serializers.PrimaryKeyRelatedField(queryset=Village.objects.filter(is_active=True))
    office = serializers.PrimaryKeyRelatedField(queryset=Office.objects.filter(is_active=True), required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    confirm_duplicates = serializers.BooleanField(required=False, default=False, write_only=True)
    override_reason = serializers.CharField(required=False, allow_blank=True, default="", write_only=True)

    def validate_cnic(self, value):
        return normalize_cnic(value)


class CustomerUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=20, required=False)
    cnic = serializers.CharField(required=False, allow_blank=True)
    permanent_address = serializers.CharField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_cnic(self, value):
        return normalize_cnic(value)


class ConnectionSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(source="customer.customer_id", read_only=True)
    package_code = serializers.CharField(source="package.code", read_only=True)
    next_package_code = serializers.CharField(source="next_package.code", read_only=True, default=None)
    office_code = serializers.CharField(source="office.code", read_only=True)
    village_code = serializers.CharField(source="village.code", read_only=True)

    class Meta:
        model = Connection
        fields = ["id", "connection_id", "customer", "office", "office_code", "village", "village_code",
                  "installation_address", "package", "package_code", "next_package", "next_package_code", "status",
                  "expires_at", "initial_payment_confirmed_at", "installation_completed_at", "activation_approved_at",
                  "activated_at", "installation_fee_waived", "cancelled_at", "cancellation_reason", "equipment_return_status", "created_at"]
        read_only_fields = fields


class ConnectionCreateSerializer(serializers.Serializer):
    customer = serializers.CharField(help_text="customer_id, e.g. KHT001")
    package = serializers.PrimaryKeyRelatedField(queryset=Package.objects.all())
    installation_address = serializers.CharField()
    village = serializers.PrimaryKeyRelatedField(queryset=Village.objects.filter(is_active=True), required=False)
    office = serializers.PrimaryKeyRelatedField(queryset=Office.objects.filter(is_active=True), required=False)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class CancelSerializer(ReasonSerializer):
    equipment_unreturned = serializers.BooleanField(required=False, default=False)


class AddressSerializer(serializers.Serializer):
    new_address = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class PackageChangeSerializer(serializers.Serializer):
    package = serializers.PrimaryKeyRelatedField(queryset=Package.objects.all())
