from django.http import Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.audit import services as audit
from apps.core.settings_store import get_setting, set_setting
from apps.customers.models import Connection

from . import pdf, services
from . import serializers as ser
from .models import Invoice, Payment, Receipt


def _pdf_response(data, name):
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    resp["Cache-Control"] = "private, no-store"
    return resp


class InvoiceViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "pdf": "billing.view",
                      "create": "billing.create_invoice", "void": "billing.void_invoice",
                      "reissue": "billing.void_invoice"}
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-INV-\d+"
    serializer_class = ser.InvoiceSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["number", "customer__customer_id", "customer__full_name", "connection__connection_id"]
    ordering_fields = ["issued_at", "number", "total_amount"]

    def get_queryset(self):
        qs = self.scoped(Invoice.objects.select_related("customer", "connection", "office", "supersedes")
                         .prefetch_related("lines"))
        p = self.request.query_params
        for param, field in (("status", "status"), ("office", "office_id"), ("kind", "kind"),
                             ("connection", "connection__connection_id"), ("customer", "customer__customer_id")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        return qs

    def _out(self, invoice):
        return self.get_serializer(self.get_queryset().get(pk=invoice.pk)).data

    @extend_schema(request=ser.InvoiceCreateSerializer, responses=ser.InvoiceSerializer)
    def create(self, request):
        s = ser.InvoiceCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        conn = self.scope().apply(Connection.objects.select_related("office", "customer", "package")) \
            .filter(connection_id=s.validated_data["connection"].upper()).first()
        if conn is None:
            raise Http404
        invoice, created = services.create_invoice(actor=request.user, connection=conn, request=request)
        return Response(self._out(invoice), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(request=ser.ReasonSerializer, responses=ser.InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def void(self, request, number=None):
        s = ser.ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        inv = services.void_invoice(actor=request.user, invoice=self.get_object(), reason=s.validated_data["reason"], request=request)
        return Response(self._out(inv))

    @extend_schema(request=ser.ReasonSerializer, responses=ser.InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def reissue(self, request, number=None):
        s = ser.ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        inv = services.reissue_invoice(actor=request.user, invoice=self.get_object(), reason=s.validated_data["reason"], request=request)
        return Response(self._out(inv), status=status.HTTP_201_CREATED)

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def pdf(self, request, number=None):
        inv = self.get_object()
        return _pdf_response(pdf.invoice_pdf(inv), inv.number)


class PaymentViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "create": "payments.record",
                      "verify": "payments.verify", "reject": "payments.verify"}
    serializer_class = ser.PaymentSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["reference", "invoice__number", "customer__customer_id", "receipt__number"]
    ordering_fields = ["entered_at", "paid_at", "amount"]

    def get_queryset(self):
        qs = self.scoped(Payment.objects.select_related("invoice", "customer", "connection", "receipt"))
        p = self.request.query_params
        for param in ("status", "method"):
            if p.get(param):
                qs = qs.filter(**{param: p[param]})
        if p.get("office"):
            qs = qs.filter(office_id=p["office"])
        return qs

    def _out(self, payment):
        return self.get_serializer(self.get_queryset().get(pk=payment.pk)).data

    @extend_schema(request=ser.PaymentCreateSerializer, responses=ser.PaymentSerializer)
    def create(self, request):
        s = ser.PaymentCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        inv = self.scope().apply(Invoice.objects.all()).filter(number=d["invoice"].upper()).first()
        if inv is None:
            raise Http404
        payment = services.record_payment(actor=request.user, invoice=inv, method=d["method"], amount=d["amount"],
                                          paid_at=d.get("paid_at"), reference=d["reference"], request=request)
        return Response(self._out(payment), status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=ser.PaymentSerializer)
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        p = services.verify_payment(actor=request.user, payment=self.get_object(), request=request)
        return Response(self._out(p))

    @extend_schema(request=ser.ReasonSerializer, responses=ser.PaymentSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = ser.ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        p = services.reject_payment(actor=request.user, payment=self.get_object(), reason=s.validated_data["reason"], request=request)
        return Response(self._out(p))


class ReceiptViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "pdf": "billing.view"}
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-RCP-\d+"
    serializer_class = ser.ReceiptSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["number", "payment__customer__customer_id", "payment__invoice__number"]

    def get_queryset(self):
        return self.scoped(Receipt.objects.select_related("payment__invoice", "payment__customer", "payment__connection"))

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def pdf(self, request, number=None):
        r = self.get_object()
        return _pdf_response(pdf.receipt_pdf(r), r.number)


class BillingSettingsView(APIView):
    """Owner-level configuration (requires the global `system.manage` permission)."""

    DEFAULTS = {"installation_fee_pkr": "0", "installation_fee_mode": "included", "financial_year_start_month": 7}

    def _current(self):
        return {k: get_setting(k, d) for k, d in self.DEFAULTS.items()}

    def _denied(self):
        return Response({"error": {"code": "permission_denied", "message": "Forbidden."}}, status=403)

    @extend_schema(responses=ser.BillingSettingsSerializer)
    def get(self, request):
        if not acl.has_permission(request.user, "system.manage"):
            return self._denied()
        return Response(self._current())

    @extend_schema(request=ser.BillingSettingsSerializer, responses=ser.BillingSettingsSerializer)
    def patch(self, request):
        if not acl.has_permission(request.user, "system.manage"):
            return self._denied()
        s = ser.BillingSettingsSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        before = self._current()
        for key, value in s.validated_data.items():
            set_setting(key, str(value) if key == "installation_fee_pkr" else value)
        after = self._current()
        audit.log(actor=request.user, action="settings.changed", entity=("system_setting", "billing"), before=before, after=after, request=request)
        return Response(after)
