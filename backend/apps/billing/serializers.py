from rest_framework import serializers

from .models import Invoice, InvoiceLine, Payment, Receipt


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["kind", "description", "amount"]


class InvoiceSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(source="customer.customer_id", read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    connection = serializers.CharField(source="connection.connection_id", read_only=True)
    office_code = serializers.CharField(source="office.code", read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    supersedes = serializers.CharField(source="supersedes.number", read_only=True, default=None)

    class Meta:
        model = Invoice
        fields = ["id", "number", "status", "kind", "office", "office_code", "customer", "customer_name", "connection",
                  "package_code", "package_name", "speed_mbps", "package_price", "installation_fee", "total_amount",
                  "term_days", "currency", "issued_at", "supersedes", "void_reason", "voided_at", "lines"]
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    invoice = serializers.CharField(source="invoice.number", read_only=True)
    customer = serializers.CharField(source="customer.customer_id", read_only=True)
    connection = serializers.CharField(source="connection.connection_id", read_only=True)
    receipt = serializers.CharField(source="receipt.number", read_only=True, default=None)
    receipt_status = serializers.CharField(source="receipt.status", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ["id", "invoice", "customer", "connection", "office", "method", "amount", "status", "reference",
                  "paid_at", "entered_at", "entered_by", "confirmed_at", "verified_by", "verified_at",
                  "rejection_reason", "reversed_at", "reversal_reason", "receipt", "receipt_status"]
        read_only_fields = fields


class ReceiptSerializer(serializers.ModelSerializer):
    payment_status = serializers.CharField(source="payment.status", read_only=True)
    amount = serializers.DecimalField(source="payment.amount", max_digits=10, decimal_places=2, read_only=True)
    method = serializers.CharField(source="payment.method", read_only=True)
    invoice = serializers.CharField(source="payment.invoice.number", read_only=True)
    customer = serializers.CharField(source="payment.customer.customer_id", read_only=True)
    connection = serializers.CharField(source="payment.connection.connection_id", read_only=True)

    class Meta:
        model = Receipt
        fields = ["number", "status", "issued_at", "office", "payment_status", "amount", "method", "invoice",
                  "customer", "connection", "invalidated_at", "invalidation_reason"]
        read_only_fields = fields


class InvoiceCreateSerializer(serializers.Serializer):
    connection = serializers.CharField(help_text="connection_id, e.g. CON-00001")


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class PaymentCreateSerializer(serializers.Serializer):
    invoice = serializers.CharField(help_text="invoice number, e.g. KHT-INV-000001")
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    paid_at = serializers.DateTimeField(required=False)
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)


class BillingSettingsSerializer(serializers.Serializer):
    installation_fee_pkr = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    installation_fee_mode = serializers.ChoiceField(choices=["included", "separate"], required=False,
                                                    help_text="included: fee on the first bill; separate: its own invoice, paid first")
    financial_year_start_month = serializers.IntegerField(min_value=1, max_value=12, required=False)
