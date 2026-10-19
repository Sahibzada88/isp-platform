from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.billing import pdf
from apps.billing.models import Invoice, Payment, Receipt
from apps.customers.models import Connection, Customer
from apps.operations import services as ops
from apps.operations.models import Complaint

from . import serializers as ser
from . import services
from .auth import SESSION_KEY, IsPortalCustomer, PortalSessionAuthentication
from .models import CustomerAccount


def _me(account):
    return {"username": account.username, "customer_id": account.customer.customer_id, "name": account.customer.full_name,
            "must_change_password": account.must_change_password}


@method_decorator(csrf_protect, name="dispatch")
class PortalLoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=ser.PortalLoginSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        s = ser.PortalLoginSerializer(data=request.data); s.is_valid(raise_exception=True)
        try:
            acc = services.authenticate(s.validated_data["username"], s.validated_data["password"])
        except services.PortalLoginFailed:
            return Response({"error": {"code": "invalid_credentials", "message": "Invalid credentials or account locked."}}, status=401)
        request.session.flush()  # new session id, and never keep a staff identity alongside the customer one
        request.session[SESSION_KEY] = acc.pk
        return Response(_me(acc))


class PortalBase(APIView):
    authentication_classes = [PortalSessionAuthentication]
    permission_classes = [IsPortalCustomer]


class PortalLogoutView(PortalBase):
    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        request._request.session.flush()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PortalMeView(PortalBase):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(_me(request.user.account))


class PortalPasswordView(PortalBase):
    @extend_schema(request=ser.PortalPasswordSerializer, responses={204: None})
    def post(self, request):
        s = ser.PortalPasswordSerializer(data=request.data); s.is_valid(raise_exception=True)
        services.change_password(request.user.account, s.validated_data["old_password"], s.validated_data["new_password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class _Own(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    authentication_classes = [PortalSessionAuthentication]
    permission_classes = [IsPortalCustomer]
    pagination_class = None

    def customer(self):
        return self.request.user.customer


class ConnectionViewSet(_Own):
    serializer_class = ser.PortalConnectionSerializer
    lookup_field = "connection_id"
    lookup_value_regex = r"CON-\d+"

    def get_queryset(self):
        return Connection.objects.filter(customer=self.customer()).select_related("package", "next_package")


class InvoiceViewSet(_Own):
    serializer_class = ser.PortalInvoiceSerializer
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-INV-\d+"

    def get_queryset(self):
        return Invoice.objects.filter(customer=self.customer()).exclude(status="void").select_related("connection")

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def pdf(self, request, number=None):
        inv = self.get_object()
        resp = HttpResponse(pdf.invoice_pdf(inv), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{inv.number}.pdf"'
        resp["Cache-Control"] = "private, no-store"
        return resp


class PaymentViewSet(_Own):
    serializer_class = ser.PortalPaymentSerializer

    def get_queryset(self):
        return Payment.objects.filter(customer=self.customer()).select_related("invoice", "receipt")


class ReceiptViewSet(_Own):
    serializer_class = ser.PortalReceiptSerializer
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-RCP-\d+"

    def get_queryset(self):
        return Receipt.objects.filter(payment__customer=self.customer()).select_related("payment__invoice")

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def pdf(self, request, number=None):
        r = self.get_object()
        resp = HttpResponse(pdf.receipt_pdf(r), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{r.number}.pdf"'
        resp["Cache-Control"] = "private, no-store"
        return resp


class ComplaintViewSet(mixins.CreateModelMixin, _Own):
    serializer_class = ser.PortalComplaintSerializer
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-CMP-\d+"

    def get_queryset(self):
        return Complaint.objects.filter(customer=self.customer()).select_related("connection").prefetch_related("events")

    @extend_schema(request=ser.PortalComplaintCreateSerializer, responses=ser.PortalComplaintSerializer)
    def create(self, request):
        s = ser.PortalComplaintCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        conn = None
        if d["connection"]:
            conn = Connection.objects.filter(connection_id=d["connection"].upper(), customer=self.customer()).first()
            if conn is None:
                raise Http404
        c = ops.create_complaint(actor=None, customer=self.customer(), connection=conn, category=d["category"],
                                 description=d["description"], source="portal", request=request)
        return Response(ser.PortalComplaintSerializer(c).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- staff side
class AccountViewSet(OfficeScopedMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "portal.manage", "create": "portal.manage", "reset_password": "portal.manage", "disable": "portal.manage",
                      "enable": "portal.manage"}
    serializer_class = ser.AccountSerializer
    office_field = "customer__office_id"

    def get_queryset(self):
        qs = self.scoped(CustomerAccount.objects.select_related("customer"))
        if self.request.query_params.get("customer"):
            qs = qs.filter(customer__customer_id=self.request.query_params["customer"].upper())
        return qs

    @extend_schema(request=ser.AccountCreateSerializer, responses=ser.AccountSerializer)
    def create(self, request):
        s = ser.AccountCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        cust = self.scope().apply(Customer.objects.all()).filter(customer_id=d["customer"].upper()).first()
        if cust is None:
            raise Http404
        acc = services.create_account(actor=request.user, customer=cust, username=d["username"], password=d["password"], request=request)
        return Response(ser.AccountSerializer(acc).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        temp = services.reset_password(actor=request.user, account=self.get_object(), request=request)
        return Response({"temporary_password": temp, "must_change_password": True})

    @extend_schema(request=None, responses=ser.AccountSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        return Response(ser.AccountSerializer(services.set_active(actor=request.user, account=self.get_object(), active=False, request=request)).data)

    @extend_schema(request=None, responses=ser.AccountSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        return Response(ser.AccountSerializer(services.set_active(actor=request.user, account=self.get_object(), active=True, request=request)).data)
