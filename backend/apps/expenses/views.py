from decimal import Decimal

from django.db.models import Count, Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.organization.models import Office

from apps.core.money import money_str

from . import services
from .models import Expense


class ExpenseSerializer(serializers.ModelSerializer):
    office_code = serializers.CharField(source="office.code", read_only=True)
    entered_by_name = serializers.CharField(source="entered_by.username", read_only=True)

    class Meta:
        model = Expense
        fields = ["id", "number", "office", "office_code", "category", "description", "amount", "expense_date", "payment_method",
                  "status", "entered_by", "entered_by_name", "created_at", "decided_by", "decided_at", "decision_reason"]
        read_only_fields = fields


class ExpenseCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField()
    category = serializers.ChoiceField(choices=Expense.Category.choices)
    description = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    expense_date = serializers.DateField()
    payment_method = serializers.ChoiceField(choices=services.METHODS)


class DecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


def filtered(qs, p):
    for param, field in (("status", "status"), ("category", "category"), ("office", "office_id"), ("payment_method", "payment_method"),
                         ("date_from", "expense_date__gte"), ("date_to", "expense_date__lte")):
        if p.get(param):
            qs = qs.filter(**{field: p[param]})
    return qs


class ExpenseViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "expenses.view", "retrieve": "expenses.view", "create": "expenses.record", "approve": "expenses.approve",
                      "reject": "expenses.approve", "summary": "expenses.view"}
    serializer_class = ExpenseSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["number", "description"]
    ordering_fields = ["expense_date", "amount"]

    def get_queryset(self):
        return filtered(self.scoped(Expense.objects.select_related("office", "entered_by")), self.request.query_params)

    @extend_schema(request=ExpenseCreateSerializer, responses=ExpenseSerializer)
    def create(self, request):
        s = ExpenseCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        self.check_office(d.pop("office"))
        exp = services.create_expense(actor=request.user, office=Office.objects.get(pk=request.data["office"]), request=request, **d)
        return Response(ExpenseSerializer(exp).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DecisionSerializer, responses=ExpenseSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = DecisionSerializer(data=request.data); s.is_valid(raise_exception=True)
        return Response(ExpenseSerializer(services.decide_expense(actor=request.user, expense=self.get_object(), approve=True, reason=s.validated_data["reason"], request=request)).data)

    @extend_schema(request=DecisionSerializer, responses=ExpenseSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = DecisionSerializer(data=request.data); s.is_valid(raise_exception=True)
        return Response(ExpenseSerializer(services.decide_expense(actor=request.user, expense=self.get_object(), approve=False, reason=s.validated_data["reason"], request=request)).data)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Approved, pending and rejected totals are always reported separately."""
        qs = self.get_queryset()
        zero = Decimal("0.00")
        by_status = {r["status"]: r for r in qs.values("status").annotate(total=Sum("amount"), n=Count("id"))}
        totals = {s: {"total": money_str(by_status.get(s, {}).get("total")), "count": by_status.get(s, {}).get("n", 0)}
                  for s in ("approved", "pending", "rejected")}
        approved = qs.filter(status="approved")
        return Response({
            "totals": totals,
            "approved_by_category": {r["category"]: money_str(r["t"]) for r in approved.values("category").annotate(t=Sum("amount"))},
            "approved_by_method": {r["payment_method"]: money_str(r["t"]) for r in approved.values("payment_method").annotate(t=Sum("amount"))},
            "approved_by_office": {r["office__code"]: money_str(r["t"]) for r in approved.values("office__code").annotate(t=Sum("amount"))},
        })
