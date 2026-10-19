from rest_framework import serializers

from .models import Complaint, ComplaintEvent, ComplaintWorkLog, Task


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplaintEvent
        fields = ["id", "kind", "actor_label", "from_value", "to_value", "note", "created_at"]


class WorkLogSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.username", read_only=True)

    class Meta:
        model = ComplaintWorkLog
        fields = ["id", "author", "notes", "materials_used", "equipment_used", "created_at"]


class ComplaintSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(source="customer.customer_id", read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    connection = serializers.CharField(source="connection.connection_id", read_only=True, default=None)
    office_code = serializers.CharField(source="office.code", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)

    class Meta:
        model = Complaint
        fields = ["id", "number", "office", "office_code", "customer", "customer_name", "connection", "category",
                  "description", "priority", "status", "source", "waiting_reason", "resolution_notes", "assigned_to",
                  "assigned_to_name", "created_at", "updated_at", "resolved_at", "closed_at", "reopen_count"]
        read_only_fields = fields


class ComplaintDetailSerializer(ComplaintSerializer):
    events = EventSerializer(many=True, read_only=True)
    work_logs = WorkLogSerializer(many=True, read_only=True)

    class Meta(ComplaintSerializer.Meta):
        fields = ComplaintSerializer.Meta.fields + ["events", "work_logs"]
        read_only_fields = fields


class ComplaintCreateSerializer(serializers.Serializer):
    customer = serializers.CharField()
    connection = serializers.CharField(required=False, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=Complaint.Category.choices)
    description = serializers.CharField()
    priority = serializers.ChoiceField(choices=Complaint.Priority.choices, default="medium")
    source = serializers.ChoiceField(choices=["office", "phone", "whatsapp"], default="office")


class AssignSerializer(serializers.Serializer):
    technician = serializers.IntegerField()


class PrioritySerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=Complaint.Priority.choices)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")


class ResolveSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField()


class WorkLogCreateSerializer(serializers.Serializer):
    notes = serializers.CharField()
    materials_used = serializers.CharField(required=False, allow_blank=True, default="")
    equipment_used = serializers.CharField(required=False, allow_blank=True, default="")


class TaskSerializer(serializers.ModelSerializer):
    office_code = serializers.CharField(source="office.code", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)
    complaint_number = serializers.CharField(source="complaint.number", read_only=True, default=None)

    class Meta:
        model = Task
        fields = ["id", "office", "office_code", "title", "description", "kind", "priority", "status", "assigned_to",
                  "assigned_to_name", "due_date", "complaint", "complaint_number", "completion_notes", "created_at",
                  "updated_at", "completed_at"]
        read_only_fields = fields


class TaskCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField()
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    kind = serializers.ChoiceField(choices=Task.Kind.choices, default="other")
    priority = serializers.ChoiceField(choices=Complaint.Priority.choices, default="medium")
    assigned_to = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    complaint = serializers.CharField(required=False, allow_blank=True, default="")


class CompleteSerializer(serializers.Serializer):
    notes = serializers.CharField()
