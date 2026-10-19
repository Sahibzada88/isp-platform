from decimal import Decimal

from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts import services
from apps.accounts.drf import PermissionMap
from apps.audit import services as audit

from .models import Package

FIELDS = ["code", "name", "speed_mbps", "price_pkr", "validity_days", "is_active"]


class PackageSerializer(serializers.ModelSerializer):
    price_pkr = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("1"))
    speed_mbps = serializers.IntegerField(min_value=1)

    class Meta:
        model = Package
        fields = ["id", *FIELDS, "created_at", "updated_at"]
        read_only_fields = ["id", "validity_days", "is_active", "created_at", "updated_at"]


class PackageViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [PermissionMap]
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_map = {"list": "packages.view", "retrieve": "packages.view", "create": "packages.manage",
                      "partial_update": "packages.manage", "deactivate": "packages.manage", "activate": "packages.manage"}

    def get_queryset(self):
        qs = super().get_queryset()
        active = self.request.query_params.get("active")
        return qs.filter(is_active=active == "true") if active in {"true", "false"} else qs

    def _snap(self, p):
        return {f: str(getattr(p, f)) for f in FIELDS}

    def perform_create(self, serializer):
        if not services.has_permission(self.request.user, "packages.change_price"):
            raise PermissionDenied("Setting a package price requires the change-price permission.")
        pkg = serializer.save(created_by=self.request.user, updated_by=self.request.user)
        audit.log(actor=self.request.user, action="package.created", entity=pkg, after=self._snap(pkg), request=self.request)

    def perform_update(self, serializer):
        if "price_pkr" in serializer.validated_data and not services.has_permission(self.request.user, "packages.change_price"):
            raise PermissionDenied("Changing a package price requires the change-price permission.")
        before = self._snap(serializer.instance)
        pkg = serializer.save(updated_by=self.request.user)
        audit.log(actor=self.request.user, action="package.updated", entity=pkg, before=before, after=self._snap(pkg), request=self.request)

    def _set_active(self, request, value):
        pkg = self.get_object()
        before = self._snap(pkg)
        pkg.is_active, pkg.updated_by = value, request.user
        pkg.save(update_fields=["is_active", "updated_by", "updated_at"])
        audit.log(actor=request.user, action="package.activated" if value else "package.deactivated",
                  entity=pkg, before=before, after=self._snap(pkg), request=request)
        return Response(PackageSerializer(pkg).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._set_active(request, True)
