from rest_framework import serializers

from .models import Attendance, AttendanceChange, LeaveRequest


class ChangeSerializer(serializers.ModelSerializer):
    changed_by = serializers.CharField(source="changed_by.username")

    class Meta:
        model = AttendanceChange
        fields = ["changed_by", "reason", "old_check_in", "old_check_out", "new_check_in", "new_check_out", "changed_at"]


class AttendanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.username", read_only=True)
    office_code = serializers.CharField(source="office.code", read_only=True)
    changes = ChangeSerializer(many=True, read_only=True)

    class Meta:
        model = Attendance
        fields = ["id", "employee", "employee_name", "office", "office_code", "date", "check_in", "check_out", "changes"]
        read_only_fields = fields


class LeaveSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.username", read_only=True)
    office_code = serializers.CharField(source="office.code", read_only=True)

    class Meta:
        model = LeaveRequest
        fields = ["id", "employee", "employee_name", "office", "office_code", "start_date", "end_date", "reason", "status",
                  "created_at", "decided_by", "decided_at", "decision_note"]
        read_only_fields = fields


class CheckInSerializer(serializers.Serializer):
    office = serializers.IntegerField(required=False, allow_null=True)


class CorrectSerializer(serializers.Serializer):
    check_in = serializers.DateTimeField(allow_null=True)
    check_out = serializers.DateTimeField(allow_null=True)
    reason = serializers.CharField(max_length=500)


class LeaveCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField(required=False, allow_null=True)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(max_length=500)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
