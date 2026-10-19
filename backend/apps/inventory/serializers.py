from rest_framework import serializers

from .models import (Equipment, InventoryItem, MaterialUsage, Purchase, ReturnRecord, StockAdjustmentRequest, StockMovement)


class ItemSerializer(serializers.ModelSerializer):
    office_code = serializers.CharField(source="office.code", read_only=True)
    low_stock = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = ["id", "office", "office_code", "category", "name", "unit", "is_serialized", "minimum_stock", "quantity",
                  "faulty_quantity", "low_stock", "is_active"]
        read_only_fields = ["id", "office", "office_code", "quantity", "faulty_quantity", "low_stock", "is_serialized"]

    def get_low_stock(self, obj):
        return obj.minimum_stock > 0 and obj.quantity <= obj.minimum_stock


class ItemCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField()
    name = serializers.CharField(max_length=150)
    category = serializers.ChoiceField(choices=InventoryItem.Category.choices)
    unit = serializers.CharField(max_length=20, default="pcs")
    is_serialized = serializers.BooleanField(default=False)
    minimum_stock = serializers.DecimalField(max_digits=12, decimal_places=2, default=0, min_value=0)


class ItemUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    unit = serializers.CharField(max_length=20, required=False)
    minimum_stock = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    is_active = serializers.BooleanField(required=False)


class MovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = ["id", "item", "item_name", "office", "kind", "quantity_delta", "faulty_delta", "balance_after", "actor", "note",
                  "complaint", "task", "connection", "created_at"]
        read_only_fields = fields


class PurchaseSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = Purchase
        fields = ["id", "office", "item", "item_name", "quantity", "unit_price", "total_amount", "purchase_date",
                  "supplier_notes", "entered_by", "created_at"]
        read_only_fields = fields


class PurchaseCreateSerializer(serializers.Serializer):
    item = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    purchase_date = serializers.DateField()
    supplier_notes = serializers.CharField(required=False, allow_blank=True, default="")
    serials = serializers.ListField(child=serializers.CharField(), required=False)


class UsageSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = MaterialUsage
        fields = ["id", "office", "item", "item_name", "quantity", "used_by", "complaint", "task", "connection",
                  "idempotency_key", "reversed_at", "reversal_reason", "created_at"]
        read_only_fields = fields


class UsageCreateSerializer(serializers.Serializer):
    item = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    idempotency_key = serializers.CharField(max_length=80)
    complaint = serializers.CharField(required=False, allow_blank=True, default="")
    task = serializers.IntegerField(required=False, allow_null=True)
    connection = serializers.CharField(required=False, allow_blank=True, default="")


class AdjustmentSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = StockAdjustmentRequest
        fields = ["id", "office", "item", "item_name", "requested_delta", "approved_delta", "reason", "status", "requested_by",
                  "requested_at", "decided_by", "decided_at", "decision_note"]
        read_only_fields = fields


class AdjustmentCreateSerializer(serializers.Serializer):
    item = serializers.IntegerField()
    delta = serializers.DecimalField(max_digits=12, decimal_places=2)
    reason = serializers.CharField(max_length=500)


class AdjustmentDecisionSerializer(serializers.Serializer):
    approved_delta = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ReturnSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = ReturnRecord
        fields = ["id", "office", "item", "item_name", "quantity", "returned_by", "status", "condition", "inspected_by",
                  "inspected_at", "note", "created_at"]
        read_only_fields = fields


class ReturnCreateSerializer(serializers.Serializer):
    item = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class InspectSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(choices=["good", "faulty"])
    note = serializers.CharField(required=False, allow_blank=True, default="")


class EquipmentSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    connection = serializers.CharField(source="assigned_connection.connection_id", read_only=True, default=None)
    office_code = serializers.CharField(source="office.code", read_only=True)

    class Meta:
        model = Equipment
        fields = ["id", "item", "item_name", "office", "office_code", "serial_number", "status", "condition", "connection",
                  "assigned_at", "returned_at"]
        read_only_fields = fields


class AssignSerializer(serializers.Serializer):
    connection = serializers.CharField(help_text="connection_id, e.g. CON-00001")


class EquipmentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["faulty", "lost", "under_repair", "available"])
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
