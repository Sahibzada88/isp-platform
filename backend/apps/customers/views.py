from django.http import Http404
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap

from . import serializers as ser
from . import services
from .models import Connection, Customer


class CustomerViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                      mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "customers.view", "retrieve": "customers.view", "create": "customers.create",
                      "partial_update": "customers.edit"}
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_field = "customer_id"
    lookup_value_regex = "[A-Za-z0-9]+"
    serializer_class = ser.CustomerSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["customer_id", "full_name", "phone"]
    ordering_fields = ["customer_id", "full_name", "registered_on"]

    def get_queryset(self):
        qs = self.scoped(Customer.objects.select_related("village", "office"))
        p = self.request.query_params
        if p.get("office"):
            qs = qs.filter(office_id=p["office"])
        if p.get("village"):
            qs = qs.filter(village_id=p["village"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.request and self.request.user.is_authenticated:
            ctx["cnic_scope"] = acl.office_scope(self.request.user, "customers.view_cnic")
        return ctx

    def create(self, request):
        s = ser.CustomerCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        confirm, reason = d.pop("confirm_duplicates"), d.pop("override_reason")
        d["office"] = d.get("office") or d["village"].office
        self.check_office(d["office"].pk)
        customer = services.register_customer(actor=request.user, data=d, confirm_duplicates=confirm,
                                              reason=reason, request=request)
        return Response(self.get_serializer(customer).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        customer = self.get_object()
        s = ser.CustomerUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        customer = services.update_customer(actor=request.user, customer=customer, changes=s.validated_data, request=request)
        return Response(self.get_serializer(customer).data)


class ConnectionViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {
        "list": "connections.view", "retrieve": "connections.view", "create": "connections.create",
        "complete_installation": "connections.complete_installation", "approve_activation": "connections.approve_activation",
        "suspend": "connections.suspend", "cancel": "connections.cancel", "reactivate": "connections.reactivate",
        "change_address": "connections.change_address", "schedule_package": "connections.schedule_package",
        "confirm_renewal": "connections.approve_activation", "waive_installation_fee": "billing.waive_installation_fee",
    }
    lookup_field = "connection_id"
    lookup_value_regex = r"CON-\d+"
    serializer_class = ser.ConnectionSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["connection_id", "customer__customer_id", "customer__full_name", "customer__phone"]
    ordering_fields = ["connection_id", "expires_at", "created_at"]

    def get_queryset(self):
        qs = self.scoped(Connection.objects.select_related("customer", "package", "next_package", "office", "village"))
        p = self.request.query_params
        for param, field in (("office", "office_id"), ("village", "village_id"), ("status", "status"),
                             ("package", "package_id"), ("customer", "customer__customer_id")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        return qs

    def create(self, request):
        s = ser.ConnectionCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        customer = acl.office_scope(request.user, "customers.view").apply(Customer.objects.select_related("village__office")) \
            .filter(customer_id=d["customer"].upper()).first()
        if customer is None:
            raise Http404
        village = d.get("village") or customer.village
        office = d.get("office") or village.office
        self.check_office(office.pk)
        conn = services.create_connection(actor=request.user, customer=customer, package=d["package"],
                                          installation_address=d["installation_address"], village=village,
                                          office=office, request=request)
        return Response(self.get_serializer(conn).data, status=status.HTTP_201_CREATED)

    def _respond(self, conn):
        return Response(self.get_serializer(Connection.objects.select_related("customer", "package", "next_package", "office", "village").get(pk=conn.pk)).data)

    @action(detail=True, methods=["post"], url_path="complete-installation")
    def complete_installation(self, request, connection_id=None):
        return self._respond(services.complete_installation(actor=request.user, connection=self.get_object(), request=request))

    @action(detail=True, methods=["post"], url_path="approve-activation")
    def approve_activation(self, request, connection_id=None):
        return self._respond(services.approve_activation(actor=request.user, connection=self.get_object(), request=request))

    @action(detail=True, methods=["post"])
    def suspend(self, request, connection_id=None):
        s = ser.ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._respond(services.suspend_connection(actor=request.user, connection=self.get_object(),
                                                         reason=s.validated_data["reason"], request=request))

    @action(detail=True, methods=["post"])
    def cancel(self, request, connection_id=None):
        s = ser.CancelSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._respond(services.cancel_connection(
            actor=request.user, connection=self.get_object(), reason=s.validated_data["reason"],
            equipment_unreturned=s.validated_data["equipment_unreturned"], request=request))

    @action(detail=True, methods=["post"])
    def reactivate(self, request, connection_id=None):
        s = ser.ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._respond(services.reactivate_connection(actor=request.user, connection=self.get_object(),
                                                            reason=s.validated_data["reason"], request=request))

    @action(detail=True, methods=["post"], url_path="change-address")
    def change_address(self, request, connection_id=None):
        s = ser.AddressSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._respond(services.change_installation_address(
            actor=request.user, connection=self.get_object(), new_address=s.validated_data["new_address"],
            reason=s.validated_data["reason"], request=request))

    @action(detail=True, methods=["post"], url_path="schedule-package")
    def schedule_package(self, request, connection_id=None):
        s = ser.PackageChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._respond(services.schedule_package_change(actor=request.user, connection=self.get_object(),
                                                              package=s.validated_data["package"], request=request))

    @action(detail=True, methods=["post"], url_path="confirm-renewal")
    def confirm_renewal(self, request, connection_id=None):
        from apps.billing import services as billing

        return self._respond(billing.confirm_renewal(actor=request.user, connection=self.get_object(), request=request))

    @action(detail=True, methods=["post"], url_path="waive-installation-fee")
    def waive_installation_fee(self, request, connection_id=None):
        from apps.billing import services as billing

        s = ser.ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self._respond(billing.waive_installation_fee(
            actor=request.user, connection=self.get_object(), reason=s.validated_data["reason"], request=request))
