from django.contrib.auth import login, logout, update_session_auth_hash
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.organization.models import Office

from . import services
from .drf import OfficeScopedMixin, PermissionMap
from .models import Employee, OfficeAssignment
from .permissions import PERMISSIONS
from .serializers import (ChangePasswordSerializer, DisableSerializer, EmployeeCreateSerializer, EmployeeSerializer, LoginSerializer,
                          PermissionSetSerializer, ReasonSerializer)


def _me_payload(user):
    grants = {}
    for office_code, code in user.permission_grants.values_list("office__code", "code"):
        grants.setdefault(office_code or "*", []).append(code)
    return {**EmployeeSerializer(user).data, "is_owner": user.is_owner, "permissions": grants}


@extend_schema(responses=OpenApiTypes.OBJECT)
class CsrfView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


@extend_schema(request=LoginSerializer, responses=OpenApiTypes.OBJECT)
@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            user = services.authenticate_employee(s.validated_data["username"], s.validated_data["password"], request)
        except services.LoginFailed:
            return Response({"error": {"code": "invalid_credentials", "message": "Invalid credentials or account locked."}},
                            status=status.HTTP_401_UNAUTHORIZED)
        request.session.flush()  # a browser can hold only one identity: drop any customer-portal session
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        audit.log(actor=user, action="auth.login", entity=user, request=request)
        return Response(_me_payload(user))


@extend_schema(request=None, responses={204: None})
class LogoutView(APIView):
    def post(self, request):
        audit.log(actor=request.user, action="auth.logout", entity=request.user, request=request)
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(responses=OpenApiTypes.OBJECT)
class MeView(APIView):
    def get(self, request):
        return Response(_me_payload(request.user))


@extend_schema(request=ChangePasswordSerializer, responses={204: None})
class ChangePasswordView(APIView):
    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(s.validated_data["old_password"]):
            return Response({"error": {"code": "invalid_credentials", "message": "Old password is incorrect."}}, status=400)
        user.set_password(s.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        update_session_auth_hash(request, user)
        audit.log(actor=user, action="auth.password_changed", entity=user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeViewSet(OfficeScopedMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [PermissionMap]
    permission_map = {
        "list": "employees.view", "retrieve": "employees.view", "create": "employees.manage",
        "disable": "employees.manage", "reset_password": "employees.manage", "set_permissions": "permissions.manage",
        "grants": "permissions.manage",
    }
    serializer_class = EmployeeSerializer

    def get_queryset(self):
        qs = Employee.objects.order_by("username")
        user = self.request.user
        if user.is_owner:
            return qs
        scope = self.scope()
        if scope.everything:
            return qs
        return qs.filter(assignments__office_id__in=scope.ids, assignments__is_active=True).distinct()

    def create(self, request):
        s = EmployeeCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        scope = services.office_scope(request.user, "employees.manage")
        if any(o.pk not in scope for o in d["offices"]):
            raise PermissionDenied("You cannot manage employees in one of these offices.")
        emp = services.create_employee(actor=request.user, request=request, **d)
        return Response(EmployeeSerializer(emp).data, status=status.HTTP_201_CREATED)

    def _managed(self, request):
        emp = self.get_object()
        if not services.can_manage_employee(request.user, emp):
            raise PermissionDenied("You cannot manage this employee.")
        return emp

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        emp = self._managed(request)
        s = DisableSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.disable_employee(actor=request.user, employee=emp, reason=s.validated_data["reason"],
                                  reassign_to=s.validated_data.get("reassign_to"), request=request)
        return Response(EmployeeSerializer(emp).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        emp = self._managed(request)
        temp = services.reset_password(actor=request.user, employee=emp, request=request)
        return Response({"temporary_password": temp, "must_change_password": True})

    @action(detail=True, methods=["get"])
    def grants(self, request, pk=None):
        emp = self.get_object()
        rows = emp.permission_grants.values("office__code", "code").order_by("office__code", "code")
        return Response(list(rows))

    @action(detail=True, methods=["post"], url_path="permissions")
    def set_permissions(self, request, pk=None):
        emp = self.get_object()
        s = PermissionSetSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_office_permissions(actor=request.user, employee=emp, office=s.validated_data["office"],
                                        codes=s.validated_data["permissions"], request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(responses=OpenApiTypes.OBJECT)
class PermissionCatalogView(APIView):
    def get(self, request):
        return Response([{"code": c, "label": l} for c, l in sorted(PERMISSIONS.items())])


ASSIGNABLE = {  # what is being assigned -> (permission the requester needs, permission the assignee needs)
    "complaints": ("complaints.assign", "complaints.work"),
    "tasks": ("tasks.manage", "tasks.work"),
    "leads": ("leads.manage", "leads.view"),
}


class AssignableEmployeesView(APIView):
    """Names of colleagues in ONE office who can take a given kind of work. Needs the matching assign right there;
    exposes id and name only (not the full employee directory)."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        kind = request.query_params.get("for", "complaints")
        office = request.query_params.get("office", "")
        if kind not in ASSIGNABLE or not office.isdigit():
            return Response({"error": {"code": "validation_error", "message": "Provide office and for=complaints|tasks|leads."}}, status=400)
        needs, takes = ASSIGNABLE[kind]
        if not services.has_permission(request.user, needs, int(office)):
            return Response({"error": {"code": "permission_denied", "message": "Forbidden."}}, status=403)
        people = services.employees_with_permission(takes, Office.objects.filter(pk=int(office)).first())
        return Response([{"id": e.pk, "username": e.username, "name": (e.get_full_name() or e.username), "role": e.role}
                         for e in people.order_by("username")])
