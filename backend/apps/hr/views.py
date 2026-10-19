from django.db.models import Q
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.organization.models import Office

from . import serializers as ser
from . import services
from .models import Attendance, LeaveRequest


def _office(pk):
    if pk is None:
        return None
    office = Office.objects.filter(pk=pk).first()
    if office is None:
        raise Http404
    return office


class AttendanceViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "attendance.self", "retrieve": "attendance.self", "check_in": "attendance.self",
                      "check_out": "attendance.self", "correct": "attendance.correct"}
    serializer_class = ser.AttendanceSerializer

    def get_queryset(self):
        user, code = self.request.user, self.permission_map[self.action]
        base = Attendance.objects.select_related("employee", "office").prefetch_related("changes__changed_by")
        if self.action == "correct":
            qs = self.scope(code).apply(base)
        else:  # everyone sees their own; supervisors also see their offices
            view = acl.office_scope(user, "attendance.view")
            qs = base if view.everything else base.filter(Q(employee=user) | Q(office_id__in=view.ids))
        p = self.request.query_params
        for param, field in (("office", "office_id"), ("employee", "employee_id"), ("date", "date"),
                             ("date_from", "date__gte"), ("date_to", "date__lte")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        return qs

    @extend_schema(request=ser.CheckInSerializer, responses=ser.AttendanceSerializer)
    @action(detail=False, methods=["post"], url_path="check-in")
    def check_in(self, request):
        s = ser.CheckInSerializer(data=request.data); s.is_valid(raise_exception=True)
        rec = services.check_in(actor=request.user, office=_office(s.validated_data.get("office")), request=request)
        return Response(ser.AttendanceSerializer(rec).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=ser.AttendanceSerializer)
    @action(detail=False, methods=["post"], url_path="check-out")
    def check_out(self, request):
        return Response(ser.AttendanceSerializer(services.check_out(actor=request.user, request=request)).data)

    @extend_schema(request=ser.CorrectSerializer, responses=ser.AttendanceSerializer)
    @action(detail=True, methods=["post"])
    def correct(self, request, pk=None):
        s = ser.CorrectSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        rec = services.correct_attendance(actor=request.user, attendance=self.get_object(), check_in=d["check_in"],
                                          check_out=d["check_out"], reason=d["reason"], request=request)
        return Response(ser.AttendanceSerializer(Attendance.objects.get(pk=rec.pk)).data)


class LeaveViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "leave.request", "retrieve": "leave.request", "create": "leave.request", "cancel": "leave.request",
                      "approve": "leave.approve", "reject": "leave.approve"}
    serializer_class = ser.LeaveSerializer

    def get_queryset(self):
        user = self.request.user
        base = LeaveRequest.objects.select_related("employee", "office")
        if self.action in ("approve", "reject"):
            qs = self.scope("leave.approve").apply(base)
        elif self.action == "cancel":
            qs = base.filter(employee=user)
        else:
            appr = acl.office_scope(user, "leave.approve")
            qs = base if appr.everything else base.filter(Q(employee=user) | Q(office_id__in=appr.ids))
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    @extend_schema(request=ser.LeaveCreateSerializer, responses=ser.LeaveSerializer)
    def create(self, request):
        s = ser.LeaveCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        leave = services.request_leave(actor=request.user, start_date=d["start_date"], end_date=d["end_date"],
                                       reason=d["reason"], office=_office(d.get("office")), request=request)
        return Response(ser.LeaveSerializer(leave).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ser.NoteSerializer, responses=ser.LeaveSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = ser.NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return Response(ser.LeaveSerializer(services.decide_leave(actor=request.user, leave=self.get_object(), approve=True, note=s.validated_data["note"], request=request)).data)

    @extend_schema(request=ser.NoteSerializer, responses=ser.LeaveSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = ser.NoteSerializer(data=request.data); s.is_valid(raise_exception=True)
        return Response(ser.LeaveSerializer(services.decide_leave(actor=request.user, leave=self.get_object(), approve=False, note=s.validated_data["note"], request=request)).data)

    @extend_schema(request=None, responses=ser.LeaveSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(ser.LeaveSerializer(services.cancel_leave(actor=request.user, leave=self.get_object(), request=request)).data)
