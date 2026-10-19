from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.billing.models import Invoice, Payment
from apps.organization.models import Office

from apps.core.money import money_str

from . import serializers as ser
from . import services
from .models import CashDeposit, DepositItem, OverpaymentException, PaymentCorrection, Refund


class _Base(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    model = None

    def get_queryset(self):
        qs = self.scoped(self.model.objects.all())
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("office"):
            qs = qs.filter(office_id=p["office"])
        return qs

    def _out(self, obj):
        return Response(self.get_serializer(self.get_queryset().get(pk=obj.pk)).data)

    def _payment_in_scope(self, pk, code):
        payment = self.scope(code).apply(Payment.objects.select_related("invoice")).filter(pk=pk).first()
        if payment is None:
            raise Http404
        return payment


class CorrectionViewSet(_Base):
    model = PaymentCorrection
    serializer_class = ser.CorrectionSerializer
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "create": "payments.request_correction",
                      "approve": "payments.approve_correction", "reject": "payments.approve_correction"}

    def get_queryset(self):
        return super().get_queryset().select_related("payment__invoice")

    @extend_schema(request=ser.CorrectionCreateSerializer, responses=ser.CorrectionSerializer)
    def create(self, request):
        s = ser.CorrectionCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payment = self._payment_in_scope(s.validated_data["payment"], "payments.request_correction")
        corr = services.request_correction(actor=request.user, payment=payment, reason=s.validated_data["reason"], request=request)
        return Response(ser.CorrectionSerializer(corr).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.NoteSerializer, responses=ser.CorrectionSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = ser.NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        corr = services.approve_correction(actor=request.user, correction=self.get_object(), note=s.validated_data["note"], request=request)
        return self._out(corr)

    @extend_schema(request=ser.RequiredNoteSerializer, responses=ser.CorrectionSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = ser.RequiredNoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        corr = services.reject_correction(actor=request.user, correction=self.get_object(), note=s.validated_data["note"], request=request)
        return self._out(corr)


class RefundViewSet(_Base):
    model = Refund
    serializer_class = ser.RefundSerializer
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "create": "refunds.request",
                      "approve": "refunds.approve", "reject": "refunds.approve", "mark_paid": "refunds.pay"}

    def get_queryset(self):
        return super().get_queryset().select_related("payment__invoice")

    @extend_schema(request=ser.RefundCreateSerializer, responses=ser.RefundSerializer)
    def create(self, request):
        s = ser.RefundCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        payment = self._payment_in_scope(d["payment"], "refunds.request")
        r = services.request_refund(actor=request.user, payment=payment, amount=d["amount"], reason=d["reason"], request=request)
        return Response(ser.RefundSerializer(r).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.NoteSerializer, responses=ser.RefundSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = ser.NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.approve_refund(actor=request.user, refund=self.get_object(), note=s.validated_data["note"], request=request))

    @extend_schema(request=ser.RequiredNoteSerializer, responses=ser.RefundSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = ser.RequiredNoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.reject_refund(actor=request.user, refund=self.get_object(), note=s.validated_data["note"], request=request))

    @extend_schema(request=ser.RefundPaySerializer, responses=ser.RefundSerializer)
    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        s = ser.RefundPaySerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        return self._out(services.mark_refund_paid(actor=request.user, refund=self.get_object(), method=d.get("method"),
                                                   exception_reason=d["exception_reason"], refunded_on=d.get("refunded_on"), request=request))


class OverpaymentViewSet(_Base):
    model = OverpaymentException
    serializer_class = ser.OverpaymentSerializer
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "create": "payments.record_exception",
                      "decide": "refunds.approve", "mark_refunded": "refunds.pay"}

    @extend_schema(request=ser.OverpaymentCreateSerializer, responses=ser.OverpaymentSerializer)
    def create(self, request):
        s = ser.OverpaymentCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        self.check_office(d["office"], "payments.record_exception")
        office = Office.objects.get(pk=d["office"])
        invoice = None
        if d["invoice"]:
            invoice = self.scope("payments.record_exception").apply(Invoice.objects.all()).filter(number=d["invoice"].upper()).first()
            if invoice is None or invoice.office_id != office.pk:
                raise Http404
        exc = services.record_overpayment(actor=request.user, office=office, method=d["method"], amount_received=d["amount_received"],
                                          transaction_at=d["transaction_at"], reference=d["reference"], invoice=invoice,
                                          notes=d["notes"], request=request)
        return Response(ser.OverpaymentSerializer(exc).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.OverpaymentDecisionSerializer, responses=ser.OverpaymentSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        s = ser.OverpaymentDecisionSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.decide_overpayment(actor=request.user, exception=self.get_object(),
                                                     decision=s.validated_data["decision"], note=s.validated_data["note"], request=request))

    @extend_schema(request=ser.RefundedSerializer, responses=ser.OverpaymentSerializer)
    @action(detail=True, methods=["post"], url_path="mark-refunded")
    def mark_refunded(self, request, pk=None):
        s = ser.RefundedSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.mark_overpayment_refunded(actor=request.user, exception=self.get_object(),
                                                            method=s.validated_data["method"], request=request))


class DepositViewSet(_Base):
    model = CashDeposit
    serializer_class = ser.DepositSerializer
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-DEP-\d+"
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "create": "cash.deposit",
                      "reconcile": "cash.reconcile", "cancel": "cash.reconcile"}

    def get_queryset(self):
        return super().get_queryset().select_related("collector").prefetch_related("items")

    def _out(self, obj):
        return Response(self.get_serializer(self.get_queryset().get(pk=obj.pk)).data)

    @extend_schema(request=ser.DepositCreateSerializer, responses=ser.DepositSerializer)
    def create(self, request):
        s = ser.DepositCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        self.check_office(d["office"], "cash.deposit")
        dep = services.create_deposit(actor=request.user, office=Office.objects.get(pk=d["office"]),
                                      payment_ids=d.get("payments"), declared_amount=d.get("declared_amount"), request=request)
        return Response(self.get_serializer(self.get_queryset().get(pk=dep.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.ReconcileSerializer, responses=ser.DepositSerializer)
    @action(detail=True, methods=["post"])
    def reconcile(self, request, number=None):
        s = ser.ReconcileSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.reconcile_deposit(actor=request.user, deposit=self.get_object(),
                                                    received_amount=s.validated_data["received_amount"],
                                                    variance_note=s.validated_data["variance_note"], request=request))

    @extend_schema(request=ser.ReasonSerializer, responses=ser.DepositSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, number=None):
        s = ser.ReasonSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.cancel_deposit(actor=request.user, deposit=self.get_object(),
                                                 reason=s.validated_data["reason"], request=request))


class CashSummaryView(APIView):
    """Per-office daily figures (local Pakistan day) built from real records.
    Pending/unverified/reversed amounts are shown separately, never mixed into 'collected'."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        if not acl.has_permission(request.user, "billing.view"):
            return Response({"error": {"code": "permission_denied", "message": "Forbidden."}}, status=403)
        scope = acl.office_scope(request.user, "billing.view")
        raw = request.query_params.get("date")
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date() if raw else timezone.localdate()
        except ValueError:
            raise ValidationError({"date": "Use YYYY-MM-DD."})
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(day, time.min), tz)
        end = start + timedelta(days=1)
        offices = scope.apply(Office.objects.all(), "id")
        if request.query_params.get("office"):
            offices = offices.filter(pk=request.query_params["office"])
        zero = Decimal("0.00")
        out = []
        for office in offices.order_by("code"):
            pays = Payment.objects.filter(office=office)
            day_pays = pays.filter(confirmed_at__gte=start, confirmed_at__lt=end, status=Payment.Status.CONFIRMED)
            tot = lambda qs: qs.aggregate(t=Sum("amount"))["t"] or zero
            held = DepositItem.objects.filter(released_at__isnull=True).values("payment_id")
            deps = CashDeposit.objects.filter(office=office)
            reconciled = deps.filter(status="reconciled", reconciled_at__gte=start, reconciled_at__lt=end)
            out.append({
                "office": office.code, "date": str(day),
                "cash_collected": money_str(tot(day_pays.filter(method="cash"))),
                "digital_verified": money_str(tot(day_pays.exclude(method="cash"))),
                "digital_pending_verification": money_str(tot(pays.filter(status="pending_verification"))),
                "cash_undeposited_total": money_str(tot(pays.filter(method="cash", status="confirmed").exclude(pk__in=held))),
                "deposits_submitted_awaiting_count": deps.filter(status="submitted").count(),
                "deposits_submitted_awaiting_amount": money_str(deps.filter(status="submitted").aggregate(t=Sum("expected_amount"))["t"]),
                "deposits_reconciled_expected": money_str(reconciled.aggregate(t=Sum("expected_amount"))["t"]),
                "deposits_reconciled_received": money_str(reconciled.aggregate(t=Sum("received_amount"))["t"]),
                "cash_variance": money_str(reconciled.aggregate(t=Sum("variance"))["t"]),
                "reversed_today": money_str(tot(pays.filter(status="reversed", reversed_at__gte=start, reversed_at__lt=end))),
            })
        return Response({"results": out})
