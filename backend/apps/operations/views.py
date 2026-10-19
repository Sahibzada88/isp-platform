from django.db.models import Q
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.accounts.models import Employee
from apps.customers.models import Connection, Customer
from apps.organization.models import Office

from . import serializers as ser
from . import services
from .models import Complaint, Task


def _visible(user, qs, all_code, own_code):
    """Everything in offices where `all_code` is held, plus own assigned items where `own_code` is held."""
    full, own = acl.office_scope(user, all_code), acl.office_scope(user, own_code)
    if full.everything:
        return qs
    q = Q(office_id__in=full.ids)
    if own.everything or own.ids:
        q |= Q(assigned_to=user) & (Q() if own.everything else Q(office_id__in=own.ids))
    return qs.filter(q)


class ComplaintViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_map = {
        "list": "complaints.view_assigned", "retrieve": "complaints.view_assigned", "create": "complaints.create",
        "assign": "complaints.assign", "priority": "complaints.assign", "start": "complaints.work", "wait": "complaints.work",
        "resume": "complaints.work", "work_log": "complaints.work", "resolve": "complaints.work",
        "close": "complaints.close", "reopen": "complaints.reopen",
    }
    lookup_field = "number"
    lookup_value_regex = r"[A-Za-z0-9]+-CMP-\d+"
    serializer_class = ser.ComplaintSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["number", "customer__customer_id", "customer__full_name", "description"]
    ordering_fields = ["created_at", "priority", "status"]

    def get_permissions(self):
        from rest_framework.permissions import BasePermission

        class AnyViewCode(BasePermission):
            def has_permission(_, request, view):
                u = request.user
                if not (u and u.is_authenticated and u.is_active):
                    return False
                if view.action in ("list", "retrieve"):
                    return acl.has_permission(u, "complaints.view") or acl.has_permission(u, "complaints.view_assigned")
                code = view.permission_map.get(view.action)
                return bool(code) and acl.has_permission(u, code)
        return [AnyViewCode()]

    def get_queryset(self):
        base = Complaint.objects.select_related("customer", "connection", "office", "assigned_to")
        user = self.request.user
        if self.action in ("list", "retrieve"):
            qs = _visible(user, base, "complaints.view", "complaints.view_assigned")
            if self.action == "retrieve":
                qs = qs.prefetch_related("events", "work_logs__author")
        else:
            code = self.permission_map[self.action]
            qs = self.scope(code).apply(base)
            if code == "complaints.work":  # technicians act only on their own complaints; managers on any in their office
                managers = acl.office_scope(user, "complaints.assign")
                qs = qs if managers.everything else qs.filter(Q(assigned_to=user) | Q(office_id__in=managers.ids))
        p = self.request.query_params
        for param, field in (("status", "status"), ("priority", "priority"), ("category", "category"), ("office", "office_id"),
                             ("assigned_to", "assigned_to_id"), ("customer", "customer__customer_id")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        return qs

    def get_serializer_class(self):
        return ser.ComplaintDetailSerializer if self.action == "retrieve" else ser.ComplaintSerializer

    def _out(self, c):
        return Response(ser.ComplaintDetailSerializer(Complaint.objects.get(pk=c.pk)).data)

    @extend_schema(request=ser.ComplaintCreateSerializer, responses=ser.ComplaintSerializer)
    def create(self, request):
        s = ser.ComplaintCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        customer = acl.office_scope(request.user, "customers.view").apply(Customer.objects.select_related("office")) \
            .filter(customer_id=d["customer"].upper()).first()
        if customer is None:
            raise Http404
        connection = None
        if d["connection"]:
            connection = Connection.objects.select_related("office").filter(connection_id=d["connection"].upper(), customer=customer).first()
            if connection is None:
                raise Http404
        self.check_office((connection.office_id if connection else customer.office_id))
        c = services.create_complaint(actor=request.user, customer=customer, connection=connection, category=d["category"],
                                      description=d["description"], priority=d["priority"], source=d["source"], request=request)
        return Response(ser.ComplaintSerializer(c).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.AssignSerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, number=None):
        s = ser.AssignSerializer(data=request.data); s.is_valid(raise_exception=True)
        tech = Employee.objects.filter(pk=s.validated_data["technician"]).first()
        if tech is None:
            raise Http404
        return self._out(services.assign(actor=request.user, complaint=self.get_object(), technician=tech, request=request))

    @extend_schema(request=ser.PrioritySerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def priority(self, request, number=None):
        s = ser.PrioritySerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.set_priority(actor=request.user, complaint=self.get_object(), priority=s.validated_data["priority"], request=request))

    @extend_schema(request=None, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def start(self, request, number=None):
        return self._out(services.start_work(actor=request.user, complaint=self.get_object(), request=request))

    @extend_schema(request=ser.ReasonSerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def wait(self, request, number=None):
        s = ser.ReasonSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.set_waiting(actor=request.user, complaint=self.get_object(), reason=s.validated_data["reason"], request=request))

    @extend_schema(request=None, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def resume(self, request, number=None):
        return self._out(services.resume(actor=request.user, complaint=self.get_object(), request=request))

    @extend_schema(request=ser.WorkLogCreateSerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"], url_path="work-log")
    def work_log(self, request, number=None):
        s = ser.WorkLogCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        c = self.get_object()
        services.add_work_log(actor=request.user, complaint=c, notes_text=d["notes"], materials_used=d["materials_used"],
                              equipment_used=d["equipment_used"], request=request)
        return self._out(c)

    @extend_schema(request=ser.ResolveSerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def resolve(self, request, number=None):
        s = ser.ResolveSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.resolve(actor=request.user, complaint=self.get_object(), resolution_notes=s.validated_data["resolution_notes"], request=request))

    @extend_schema(request=ser.NoteSerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, number=None):
        s = ser.NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.close(actor=request.user, complaint=self.get_object(), note=s.validated_data["note"], request=request))

    @extend_schema(request=ser.ReasonSerializer, responses=ser.ComplaintDetailSerializer)
    @action(detail=True, methods=["post"])
    def reopen(self, request, number=None):
        s = ser.ReasonSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.reopen(actor=request.user, complaint=self.get_object(), reason=s.validated_data["reason"], request=request))


class TaskViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ser.TaskSerializer
    permission_map = {"list": "tasks.work", "retrieve": "tasks.work", "create": "tasks.manage", "assign": "tasks.manage",
                      "start": "tasks.work", "complete": "tasks.work", "cancel": "tasks.manage"}
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "due_date", "priority"]

    def get_permissions(self):
        from rest_framework.permissions import BasePermission

        class AnyTaskCode(BasePermission):
            def has_permission(_, request, view):
                u = request.user
                if not (u and u.is_authenticated and u.is_active):
                    return False
                if view.action in ("list", "retrieve"):
                    return acl.has_permission(u, "tasks.view") or acl.has_permission(u, "tasks.work") or acl.has_permission(u, "tasks.manage")
                code = view.permission_map.get(view.action)
                return bool(code) and (acl.has_permission(u, code) or (code == "tasks.work" and acl.has_permission(u, "tasks.manage")))
        return [AnyTaskCode()]

    def get_queryset(self):
        base = Task.objects.select_related("office", "assigned_to", "complaint")
        user = self.request.user
        if self.action in ("list", "retrieve"):
            qs = _visible(user, base, "tasks.view", "tasks.work")
            full_manage = acl.office_scope(user, "tasks.manage")
            if full_manage:
                qs = qs | _visible(user, base, "tasks.manage", "tasks.manage")
        else:
            qs = base.filter(Q(assigned_to=user) | Q(office_id__in=acl.office_scope(user, "tasks.manage").ids)) \
                if not acl.office_scope(user, "tasks.manage").everything else base
        p = self.request.query_params
        for param, field in (("status", "status"), ("office", "office_id"), ("assigned_to", "assigned_to_id"), ("kind", "kind")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        if p.get("mine") == "true":
            qs = qs.filter(assigned_to=user)
        return qs.distinct()

    def _out(self, t):
        return Response(ser.TaskSerializer(Task.objects.select_related("office", "assigned_to", "complaint").get(pk=t.pk)).data)

    @extend_schema(request=ser.TaskCreateSerializer, responses=ser.TaskSerializer)
    def create(self, request):
        s = ser.TaskCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        self.check_office(d.pop("office"))
        office = Office.objects.get(pk=request.data["office"])
        assignee = Employee.objects.filter(pk=d.pop("assigned_to")).first() if d.get("assigned_to") else None
        d.pop("assigned_to", None)
        number = d.pop("complaint", "")
        complaint = Complaint.objects.filter(number=number.upper(), office=office).first() if number else None
        if number and complaint is None:
            raise Http404
        t = services.create_task(actor=request.user, office=office, assigned_to=assignee, complaint=complaint, request=request, **d)
        return Response(ser.TaskSerializer(t).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.AssignSerializer, responses=ser.TaskSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        s = ser.AssignSerializer(data=request.data); s.is_valid(raise_exception=True)
        emp = Employee.objects.filter(pk=s.validated_data["technician"]).first()
        if emp is None:
            raise Http404
        return self._out(services.assign_task(actor=request.user, task=self.get_object(), employee=emp, request=request))

    @extend_schema(request=None, responses=ser.TaskSerializer)
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._out(services.start_task(actor=request.user, task=self.get_object(), request=request))

    @extend_schema(request=ser.CompleteSerializer, responses=ser.TaskSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        s = ser.CompleteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.complete_task(actor=request.user, task=self.get_object(), notes_text=s.validated_data["notes"], request=request))

    @extend_schema(request=ser.ReasonSerializer, responses=ser.TaskSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        s = ser.ReasonSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.cancel_task(actor=request.user, task=self.get_object(), reason=s.validated_data["reason"], request=request))
