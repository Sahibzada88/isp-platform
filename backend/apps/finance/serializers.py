from rest_framework import serializers

from apps.billing.models import Payment

from .models import CashDeposit, OverpaymentException, PaymentCorrection, Refund


class CorrectionSerializer(serializers.ModelSerializer):
    payment_id = serializers.IntegerField(source="payment.pk", read_only=True)
    invoice = serializers.CharField(source="payment.invoice.number", read_only=True)
    amount = serializers.DecimalField(source="payment.amount", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = PaymentCorrection
        fields = ["id", "payment_id", "invoice", "amount", "office", "reason", "status", "requested_by", "requested_at",
                  "decided_by", "decided_at", "decision_note"]
        read_only_fields = fields


class RefundSerializer(serializers.ModelSerializer):
    payment_id = serializers.IntegerField(source="payment.pk", read_only=True)
    invoice = serializers.CharField(source="payment.invoice.number", read_only=True)
    original_method = serializers.CharField(source="payment.method", read_only=True)

    class Meta:
        model = Refund
        fields = ["id", "payment_id", "invoice", "original_method", "office", "amount", "reason", "status", "requested_by",
                  "requested_at", "decided_by", "decided_at", "decision_note", "method", "exception_reason",
                  "refunded_on", "paid_by"]
        read_only_fields = fields


class OverpaymentSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default=None)
    excess = serializers.SerializerMethodField()

    class Meta:
        model = OverpaymentException
        fields = ["id", "office", "invoice_number", "method", "reference", "amount_received", "amount_expected", "excess",
                  "transaction_at", "notes", "status", "recorded_by", "recorded_at", "decided_by", "decided_at",
                  "decision_note", "refunded_by", "refunded_at", "refund_method"]
        read_only_fields = fields

    def get_excess(self, obj):
        return str(obj.amount_received - obj.amount_expected) if obj.amount_expected is not None else None


class DepositSerializer(serializers.ModelSerializer):
    collector_name = serializers.CharField(source="collector.username", read_only=True)
    payment_ids = serializers.SerializerMethodField()

    class Meta:
        model = CashDeposit
        fields = ["id", "number", "office", "collector", "collector_name", "expected_amount", "declared_amount",
                  "received_amount", "variance", "status", "created_at", "reconciled_by", "reconciled_at",
                  "variance_note", "cancelled_at", "cancel_reason", "payment_ids"]
        read_only_fields = fields

    def get_payment_ids(self, obj):
        return [i.payment_id for i in obj.items.all() if i.released_at is None]


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class RequiredNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=500)


class CorrectionCreateSerializer(serializers.Serializer):
    payment = serializers.IntegerField()
    reason = serializers.CharField(max_length=500)


class RefundCreateSerializer(serializers.Serializer):
    payment = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    reason = serializers.CharField(max_length=500)


class RefundPaySerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)
    exception_reason = serializers.CharField(required=False, allow_blank=True, default="")
    refunded_on = serializers.DateField(required=False)


class OverpaymentCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField()
    invoice = serializers.CharField(required=False, allow_blank=True, default="")
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    amount_received = serializers.DecimalField(max_digits=10, decimal_places=2)
    transaction_at = serializers.DateTimeField()
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class OverpaymentDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["refund", "settlement"])
    note = serializers.CharField(max_length=500)


class RefundedSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.Method.choices)


class DepositCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField()
    payments = serializers.ListField(child=serializers.IntegerField(), required=False)
    declared_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)


class ReconcileSerializer(serializers.Serializer):
    received_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    variance_note = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)
