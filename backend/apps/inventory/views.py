from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.customers.models import Connection
from apps.operations.models import Complaint, Task
from apps.organization.models import Office

from . import serializers as ser
from . import services
from .models import (Equipment, InventoryItem, MaterialUsage, Purchase, ReturnRecord, StockAdjustmentRequest, StockMovement)


class _Base(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    model = None
    select = ()

    def get_queryset(self):
        qs = self.scoped(self.model.objects.select_related(*self.select))
        p = self.request.query_params
        if p.get("office"):
            qs = qs.filter(office_id=p["office"])
        if p.get("item") and hasattr(self.model, "item"):
            qs = qs.filter(item_id=p["item"])
        if p.get("status") and hasattr(self.model, "status"):
            qs = qs.filter(status=p["status"])
        return qs

    def _item(self, pk, code):
        item = self.scope(code).apply(InventoryItem.objects.select_related("office"), "office_id").filter(pk=pk).first()
        if item is None:
            raise Http404
        return item

    def _out(self, serializer, obj):
        return Response(serializer(self.get_queryset().get(pk=obj.pk)).data)


class ItemViewSet(_Base, mixins.UpdateModelMixin):
    model = InventoryItem
    select = ("office",)
    serializer_class = ser.ItemSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "create": "inventory.edit", "partial_update": "inventory.edit"}
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["name", "quantity"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("category"):
            qs = qs.filter(category=p["category"])
        if p.get("low") == "true":
            from django.db.models import F
            qs = qs.filter(minimum_stock__gt=0, quantity__lte=F("minimum_stock"))
        return qs

    @extend_schema(request=ser.ItemCreateSerializer, responses=ser.ItemSerializer)
    def create(self, request):
        s = ser.ItemCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        self.check_office(d.pop("office"))
        item = services.create_item(actor=request.user, office=Office.objects.get(pk=request.data["office"]), request=request, **d)
        return Response(ser.ItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.ItemUpdateSerializer, responses=ser.ItemSerializer)
    def partial_update(self, request, *args, **kwargs):
        item = self.get_object()
        s = ser.ItemUpdateSerializer(data=request.data, partial=True); s.is_valid(raise_exception=True)
        from apps.audit import services as audit
        before = {k: str(getattr(item, k)) for k in s.validated_data}
        for k, v in s.validated_data.items():
            setattr(item, k, v)
        item.save()
        audit.log(actor=request.user, action="inventory.item_updated", entity=item, office=item.office, request=request,
                  before=before, after={k: str(v) for k, v in s.validated_data.items()})
        return Response(ser.ItemSerializer(item).data)


class MovementViewSet(_Base):
    model = StockMovement
    select = ("item",)
    serializer_class = ser.MovementSerializer
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view"}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("kind"):
            qs = qs.filter(kind=self.request.query_params["kind"])
        return qs


class PurchaseViewSet(_Base):
    model = Purchase
    select = ("item",)
    serializer_class = ser.PurchaseSerializer
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "create": "inventory.purchase"}

    @extend_schema(request=ser.PurchaseCreateSerializer, responses=ser.PurchaseSerializer)
    def create(self, request):
        s = ser.PurchaseCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        p = services.record_purchase(actor=request.user, item=self._item(d["item"], "inventory.purchase"), quantity=d["quantity"],
                                     unit_price=d["unit_price"], purchase_date=d["purchase_date"], supplier_notes=d["supplier_notes"],
                                     serials=d.get("serials"), request=request)
        return Response(ser.PurchaseSerializer(p).data, status=status.HTTP_201_CREATED)


class UsageViewSet(_Base):
    model = MaterialUsage
    select = ("item",)
    serializer_class = ser.UsageSerializer
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "create": "inventory.use_materials", "reverse": "inventory.edit"}

    @extend_schema(request=ser.UsageCreateSerializer, responses=ser.UsageSerializer)
    def create(self, request):
        s = ser.UsageCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        item = self._item(d["item"], "inventory.use_materials")
        complaint = task = conn = None
        if d["complaint"]:
            complaint = Complaint.objects.filter(number=d["complaint"].upper(), office=item.office).first() or self._missing()
        if d.get("task"):
            task = Task.objects.filter(pk=d["task"], office=item.office).first() or self._missing()
        if d["connection"]:
            conn = Connection.objects.filter(connection_id=d["connection"].upper(), office=item.office).first() or self._missing()
        usage, created = services.use_materials(actor=request.user, item=item, quantity=d["quantity"], idempotency_key=d["idempotency_key"],
                                                complaint=complaint, task=task, connection=conn, request=request)
        return Response(ser.UsageSerializer(usage).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def _missing(self):
        raise Http404

    @extend_schema(request=ser.ReasonSerializer, responses=ser.UsageSerializer)
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        s = ser.ReasonSerializer(data=request.data); s.is_valid(raise_exception=True)
        u = services.reverse_usage(actor=request.user, usage=self.get_object(), reason=s.validated_data["reason"], request=request)
        return Response(ser.UsageSerializer(u).data)


class AdjustmentViewSet(_Base):
    model = StockAdjustmentRequest
    select = ("item",)
    serializer_class = ser.AdjustmentSerializer
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "create": "inventory.request_adjustment",
                      "approve": "inventory.approve_adjustment", "reject": "inventory.approve_adjustment"}

    @extend_schema(request=ser.AdjustmentCreateSerializer, responses=ser.AdjustmentSerializer)
    def create(self, request):
        s = ser.AdjustmentCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        r = services.request_adjustment(actor=request.user, item=self._item(d["item"], "inventory.request_adjustment"),
                                        delta=d["delta"], reason=d["reason"], request=request)
        return Response(ser.AdjustmentSerializer(r).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.AdjustmentDecisionSerializer, responses=ser.AdjustmentSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = ser.AdjustmentDecisionSerializer(data=request.data); s.is_valid(raise_exception=True)
        r = services.decide_adjustment(actor=request.user, adjustment=self.get_object(), approve=True,
                                       approved_delta=s.validated_data.get("approved_delta"), note=s.validated_data["note"], request=request)
        return self._out(ser.AdjustmentSerializer, r)

    @extend_schema(request=ser.AdjustmentDecisionSerializer, responses=ser.AdjustmentSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = ser.AdjustmentDecisionSerializer(data=request.data); s.is_valid(raise_exception=True)
        r = services.decide_adjustment(actor=request.user, adjustment=self.get_object(), approve=False, note=s.validated_data["note"], request=request)
        return self._out(ser.AdjustmentSerializer, r)


class ReturnViewSet(_Base):
    model = ReturnRecord
    select = ("item",)
    serializer_class = ser.ReturnSerializer
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "create": "inventory.use_materials", "inspect": "inventory.inspect_returns"}

    @extend_schema(request=ser.ReturnCreateSerializer, responses=ser.ReturnSerializer)
    def create(self, request):
        s = ser.ReturnCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        r = services.submit_return(actor=request.user, item=self._item(d["item"], "inventory.use_materials"), quantity=d["quantity"], note=d["note"], request=request)
        return Response(ser.ReturnSerializer(r).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.InspectSerializer, responses=ser.ReturnSerializer)
    @action(detail=True, methods=["post"])
    def inspect(self, request, pk=None):
        s = ser.InspectSerializer(data=request.data); s.is_valid(raise_exception=True)
        r = services.inspect_return(actor=request.user, record=self.get_object(), condition=s.validated_data["condition"], note=s.validated_data["note"], request=request)
        return self._out(ser.ReturnSerializer, r)


class EquipmentViewSet(_Base):
    model = Equipment
    select = ("item", "office", "assigned_connection")
    serializer_class = ser.EquipmentSerializer
    permission_map = {"list": "inventory.view", "retrieve": "inventory.view", "assign": "inventory.use_materials",
                      "unassign": "inventory.use_materials", "inspect": "inventory.inspect_returns", "set_status": "inventory.inspect_returns"}
    filter_backends = [filters.SearchFilter]
    search_fields = ["serial_number", "item__name"]

    @extend_schema(request=ser.AssignSerializer, responses=ser.EquipmentSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        s = ser.AssignSerializer(data=request.data); s.is_valid(raise_exception=True)
        eq = self.get_object()
        conn = Connection.objects.filter(connection_id=s.validated_data["connection"].upper(), office=eq.office).first()
        if conn is None:
            raise Http404
        return self._out(ser.EquipmentSerializer, services.assign_equipment(actor=request.user, equipment=eq, connection=conn, request=request))

    @extend_schema(request=None, responses=ser.EquipmentSerializer)
    @action(detail=True, methods=["post"])
    def unassign(self, request, pk=None):
        return self._out(ser.EquipmentSerializer, services.unassign_equipment(actor=request.user, equipment=self.get_object(), request=request))

    @extend_schema(request=ser.InspectSerializer, responses=ser.EquipmentSerializer)
    @action(detail=True, methods=["post"])
    def inspect(self, request, pk=None):
        s = ser.InspectSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(ser.EquipmentSerializer, services.inspect_equipment(actor=request.user, equipment=self.get_object(),
                                                                              result=s.validated_data["condition"], condition=s.validated_data["note"], request=request))

    @extend_schema(request=ser.EquipmentStatusSerializer, responses=ser.EquipmentSerializer)
    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        s = ser.EquipmentStatusSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(ser.EquipmentSerializer, services.set_equipment_status(actor=request.user, equipment=self.get_object(),
                                                                                 status=s.validated_data["status"], note=s.validated_data["note"], request=request))
