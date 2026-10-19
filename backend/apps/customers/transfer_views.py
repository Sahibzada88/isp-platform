from django.db.models import Q
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.organization.models import Village

from . import transfers as tr
from .models import Connection, Customer, OfficeTransfer, OwnershipTransfer


class OfficeTransferSerializer(serializers.ModelSerializer):
    connection_id = serializers.CharField(source="connection.connection_id", read_only=True)
    customer_id = serializers.CharField(source="customer.customer_id", read_only=True)
    from_office_code = serializers.CharField(source="from_office.code", read_only=True)
    to_office_code = serializers.CharField(source="to_office.code", read_only=True)
    to_village_code = serializers.CharField(source="to_village.code", read_only=True)
    relocation_task_id = serializers.IntegerField(source="relocation_task.pk", read_only=True, default=None)

    class Meta:
        model = OfficeTransfer
        fields = ["id", "connection_id", "customer_id", "from_office_code", "to_office_code", "to_village_code", "old_address",
                  "new_installation_address", "move_customer", "reason", "status", "requested_by", "requested_at", "decided_by",
                  "decided_at", "decision_note", "relocation_task_id"]
        read_only_fields = fields


class OwnershipTransferSerializer(serializers.ModelSerializer):
    connection_id = serializers.CharField(source="connection.connection_id", read_only=True)
    from_customer_id = serializers.CharField(source="from_customer.customer_id", read_only=True)
    to_customer_id = serializers.CharField(source="to_customer.customer_id", read_only=True)

    class Meta:
        model = OwnershipTransfer
        fields = ["id", "connection_id", "from_customer_id", "to_customer_id", "effective_date", "balance_handling", "handling_note",
                  "reason", "status", "requested_by", "requested_at", "decided_by", "decided_at", "decision_note"]
        read_only_fields = fields


class OfficeCreate(serializers.Serializer):
    connection = serializers.CharField()
    to_village = serializers.IntegerField()
    new_installation_address = serializers.CharField()
    reason = serializers.CharField(max_length=500)
    move_customer = serializers.BooleanField(default=False)


class OwnershipCreate(serializers.Serializer):
    connection = serializers.CharField()
    new_customer = serializers.CharField(help_text="customer_id of the new owner")
    effective_date = serializers.DateField()
    balance_handling = serializers.ChoiceField(choices=OwnershipTransfer.Handling.choices)
    handling_note = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(max_length=500)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class RequiredNote(serializers.Serializer):
    note = serializers.CharField(max_length=500)


def _ids(user):
    req, app = acl.office_scope(user, "transfers.request"), acl.office_scope(user, "transfers.approve")
    return req, app


class _Base(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "transfers.request", "retrieve": "transfers.request", "create": "transfers.request",
                      "approve": "transfers.approve", "reject": "transfers.approve"}

    def get_permissions(self):
        from rest_framework.permissions import BasePermission

        class Either(BasePermission):
            def has_permission(_, request, view):
                u = request.user
                if not (u and u.is_authenticated and u.is_active):
                    return False
                code = view.permission_map.get(view.action)
                if code is None:
                    return False
                if view.action in ("list", "retrieve"):
                    return acl.has_permission(u, "transfers.request") or acl.has_permission(u, "transfers.approve")
                return acl.has_permission(u, code)
        return [Either()]

    def _status(self, qs):
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    def _find_connection(self, cid, code):
        conn = self.scope(code).apply(Connection.objects.select_related("office", "village", "customer")).filter(connection_id=cid.upper()).first()
        if conn is None:
            raise Http404
        return conn


class OfficeTransferViewSet(_Base):
    serializer_class = OfficeTransferSerializer

    def get_queryset(self):
        base = OfficeTransfer.objects.select_related("connection", "customer", "from_office", "to_office", "to_village", "relocation_task")
        req, app = _ids(self.request.user)
        if self.action in ("approve", "reject"):
            return base if app.everything else base.filter(to_office_id__in=app.ids)
        if req.everything or app.everything:
            return self._status(base)
        return self._status(base.filter(Q(from_office_id__in=req.ids | app.ids) | Q(to_office_id__in=app.ids)))

    def _out(self, t):
        return Response(OfficeTransferSerializer(self.get_queryset().model.objects.get(pk=t.pk)).data)

    @extend_schema(request=OfficeCreate, responses=OfficeTransferSerializer)
    def create(self, request):
        s = OfficeCreate(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        conn = self._find_connection(d["connection"], "transfers.request")
        village = Village.objects.filter(pk=d["to_village"]).first() or self._missing()
        t = tr.request_office_transfer(actor=request.user, connection=conn, to_village=village, new_address=d["new_installation_address"],
                                       reason=d["reason"], move_customer=d["move_customer"], request=request)
        return Response(OfficeTransferSerializer(t).data, status=status.HTTP_201_CREATED)

    def _missing(self):
        raise Http404

    @extend_schema(request=NoteSerializer, responses=OfficeTransferSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(tr.approve_office_transfer(actor=request.user, transfer=self.get_object(), note=s.validated_data["note"], request=request))

    @extend_schema(request=RequiredNote, responses=OfficeTransferSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = RequiredNote(data=request.data); s.is_valid(raise_exception=True)
        return self._out(tr.reject_office_transfer(actor=request.user, transfer=self.get_object(), note=s.validated_data["note"], request=request))


class OwnershipTransferViewSet(_Base):
    serializer_class = OwnershipTransferSerializer
    office_field = "connection__office_id"

    def get_queryset(self):
        base = OwnershipTransfer.objects.select_related("connection", "from_customer", "to_customer")
        req, app = _ids(self.request.user)
        if self.action in ("approve", "reject"):
            return app.apply(base, "connection__office_id")
        if req.everything or app.everything:
            return self._status(base)
        return self._status(base.filter(connection__office_id__in=req.ids | app.ids))

    def _out(self, t):
        return Response(OwnershipTransferSerializer(OwnershipTransfer.objects.get(pk=t.pk)).data)

    @extend_schema(request=OwnershipCreate, responses=OwnershipTransferSerializer)
    def create(self, request):
        s = OwnershipCreate(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        conn = self._find_connection(d["connection"], "transfers.request")
        new = Customer.objects.filter(customer_id=d["new_customer"].upper(), office=conn.office).first()
        if new is None:
            raise Http404
        t = tr.request_ownership_transfer(actor=request.user, connection=conn, new_customer=new, effective_date=d["effective_date"],
                                          balance_handling=d["balance_handling"], handling_note=d["handling_note"], reason=d["reason"], request=request)
        return Response(OwnershipTransferSerializer(t).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=NoteSerializer, responses=OwnershipTransferSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(tr.approve_ownership_transfer(actor=request.user, transfer=self.get_object(), note=s.validated_data["note"], request=request))

    @extend_schema(request=RequiredNote, responses=OwnershipTransferSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = RequiredNote(data=request.data); s.is_valid(raise_exception=True)
        return self._out(tr.reject_ownership_transfer(actor=request.user, transfer=self.get_object(), note=s.validated_data["note"], request=request))
