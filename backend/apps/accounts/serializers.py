from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.organization.models import Office

from .models import Employee, Role
from .permissions import PERMISSIONS


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class EmployeeSerializer(serializers.ModelSerializer):
    offices = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = ["id", "employee_code", "username", "first_name", "last_name", "email", "phone",
                  "role", "is_active", "joining_date", "must_change_password", "offices"]
        read_only_fields = fields

    def get_offices(self, obj):
        return list(obj.assignments.filter(is_active=True).values_list("office__code", flat=True))


class EmployeeCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)
    first_name = serializers.CharField(required=False, allow_blank=True, default="")
    last_name = serializers.CharField(required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=Role.choices)
    offices = serializers.PrimaryKeyRelatedField(queryset=Office.objects.filter(is_active=True), many=True, allow_empty=False)

    def validate_username(self, value):
        if Employee.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username already in use.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value


class PermissionSetSerializer(serializers.Serializer):
    office = serializers.PrimaryKeyRelatedField(queryset=Office.objects.all(), allow_null=True)
    permissions = serializers.ListField(child=serializers.ChoiceField(choices=sorted(PERMISSIONS)), allow_empty=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class DisableSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
    reassign_to = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True), required=False, allow_null=True)


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value
