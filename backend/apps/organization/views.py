from rest_framework import mixins, serializers, status, viewsets
from rest_framework.response import Response

from apps.accounts import services
from apps.accounts.drf import PermissionMap
from apps.accounts.models import OfficeAssignment
from apps.audit import services as audit

from .models import Office, Village


class OfficeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Office
        fields = ["id", "code", "name", "is_head_office", "is_active"]


class VillageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Village
        fields = ["id", "code", "name", "office", "is_active"]


def _visible_office_ids(user):
    if user.is_owner or services.has_permission(user, "offices.create"):
        return None
    return list(OfficeAssignment.objects.filter(employee=user, is_active=True).values_list("office_id", flat=True))


class _Base(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    http_method_names = ["get", "post", "patch", "head", "options"]
    audit_name = ""

    def perform_create(self, serializer):
        obj = serializer.save()
        audit.log(actor=self.request.user, action=f"{self.audit_name}.created", entity=obj, request=self.request,
                  after=serializer.data)

    def perform_update(self, serializer):
        before = self.get_serializer(serializer.instance).data
        obj = serializer.save()
        audit.log(actor=self.request.user, action=f"{self.audit_name}.updated", entity=obj, request=self.request,
                  before=before, after=serializer.data)


class OfficeViewSet(_Base):
    serializer_class = OfficeSerializer
    audit_name = "office"
    permission_map = {"list": "customers.view", "retrieve": "customers.view", "create": "offices.create",
                      "partial_update": "offices.create"}

    def get_queryset(self):
        ids = _visible_office_ids(self.request.user)
        qs = Office.objects.all()
        return qs if ids is None else qs.filter(pk__in=ids)

    def get_permissions(self):
        # Any authenticated employee may list the offices they are assigned to.
        if self.action in {"list", "retrieve"}:
            from rest_framework.permissions import IsAuthenticated
            return [IsAuthenticated()]
        return super().get_permissions()


class VillageViewSet(_Base):
    serializer_class = VillageSerializer
    audit_name = "village"
    permission_map = {"list": "customers.view", "retrieve": "customers.view", "create": "offices.create",
                      "partial_update": "offices.create"}

    def get_queryset(self):
        ids = _visible_office_ids(self.request.user)
        qs = Village.objects.select_related("office")
        return qs if ids is None else qs.filter(office_id__in=ids)

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            from rest_framework.permissions import IsAuthenticated
            return [IsAuthenticated()]
        return super().get_permissions()
