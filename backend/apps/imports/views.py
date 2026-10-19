from django.db.models import Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.drf import OfficeScopedMixin, PermissionMap

from apps.core.money import money_str

from . import services
from .models import ImportBatch, ImportIssue, OpeningBalance


class BatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportBatch
        fields = ["id", "kind", "filename", "source_sha256", "source_rows", "imported_rows", "rejected_rows", "skipped_rows", "conflict_rows",
                  "review_rows", "opening_balance_verified", "opening_balance_unverified", "created_by", "created_at"]
        read_only_fields = fields


class IssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportIssue
        fields = ["id", "row_number", "level", "code", "message", "raw"]
        read_only_fields = fields


class ImportRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["packages", "customers"])
    csv = serializers.CharField(help_text="The CSV file content as text")
    filename = serializers.CharField(required=False, allow_blank=True, default="")
    dry_run = serializers.BooleanField(default=True)


class OpeningBalanceSerializer(serializers.ModelSerializer):
    customer_id = serializers.CharField(source="customer.customer_id", read_only=True)
    connection_id = serializers.CharField(source="connection.connection_id", read_only=True, default=None)
    office_code = serializers.CharField(source="office.code", read_only=True)
    invoice = serializers.CharField(source="invoice.number", read_only=True, default=None)

    class Meta:
        model = OpeningBalance
        fields = ["id", "customer_id", "connection_id", "office_code", "amount", "status", "migration_date", "source_reference", "notes", "verified_at", "settled_at", "invoice"]
        read_only_fields = fields


class ImportViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "imports.manage", "retrieve": "imports.manage", "create": "imports.manage", "issues": "imports.manage"}
    serializer_class = BatchSerializer
    queryset = ImportBatch.objects.all()

    @extend_schema(request=ImportRequestSerializer, responses=OpenApiTypes.OBJECT)
    def create(self, request):
        s = ImportRequestSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        fn = services.import_packages if d["kind"] == "packages" else services.import_customers
        report = fn(actor=request.user, csv_text=d["csv"], filename=d["filename"], dry_run=d["dry_run"], request=request)
        return Response(report, status=status.HTTP_200_OK if d["dry_run"] else status.HTTP_201_CREATED)

    @extend_schema(responses=IssueSerializer(many=True))
    @action(detail=True, methods=["get"])
    def issues(self, request, pk=None):
        qs = ImportIssue.objects.filter(batch=self.get_object())
        if request.query_params.get("level"):
            qs = qs.filter(level=request.query_params["level"])
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(IssueSerializer(page, many=True).data)


class OpeningInvoiceSerializer(serializers.Serializer):
    connection = serializers.CharField(required=False, allow_blank=True, default="", help_text="connection_id, if the balance has none")


class OpeningBalanceViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "billing.view", "retrieve": "billing.view", "verify": "imports.manage", "summary": "billing.view", "invoice": "billing.create_invoice"}
    serializer_class = OpeningBalanceSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["customer__customer_id", "customer__full_name", "source_reference"]

    def get_queryset(self):
        if self.action == "verify":
            return OpeningBalance.objects.select_related("customer", "connection", "office")
        if self.action == "invoice":
            return self.scoped(OpeningBalance.objects.select_related("customer", "connection", "office"))
        qs = self.scoped(OpeningBalance.objects.select_related("customer", "connection", "office"))
        for param in ("status", "office"):
            if self.request.query_params.get(param):
                qs = qs.filter(**{("office_id" if param == "office" else param): self.request.query_params[param]})
        return qs

    @extend_schema(request=None, responses=OpeningBalanceSerializer)
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return Response(OpeningBalanceSerializer(services.verify_opening_balance(actor=request.user, balance=self.get_object(), request=request)).data)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Opening balances are reported on their own - never added to income."""
        qs = self.get_queryset()
        by = {r["status"]: money_str(r["t"]) for r in qs.values("status").annotate(t=Sum("amount"))}
        return Response({"verified": by.get("verified", "0.00"), "unverified": by.get("unverified", "0.00"), "settled": by.get("settled", "0.00"), "count": qs.count(),
                         "note": "Opening balances are amounts owed before go-live. They are not payments and not income."})


    @extend_schema(request=OpeningInvoiceSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def invoice(self, request, pk=None):
        """Create (or reopen) the invoice that collects this verified balance."""
        from apps.billing import services as billing
        from apps.billing.serializers import InvoiceSerializer
        from apps.customers.models import Connection

        s = OpeningInvoiceSerializer(data=request.data); s.is_valid(raise_exception=True)
        ob = self.get_object()
        conn = None
        if s.validated_data["connection"]:
            conn = Connection.objects.filter(connection_id=s.validated_data["connection"].upper()).first()
            if conn is None or conn.office_id not in self.scope():
                from django.http import Http404
                raise Http404
        invoice, created = billing.create_opening_balance_invoice(actor=request.user, balance=ob, connection=conn, request=request)
        data = InvoiceSerializer(invoice).data
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
