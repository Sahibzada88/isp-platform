from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts import services as acl
from apps.accounts.drf import OfficeScopedMixin, PermissionMap
from apps.accounts.models import Employee
from apps.customers.models import Customer
from apps.organization.models import Office, Village
from apps.packages.models import Package

from . import services
from .models import Lead, LeadNote


class NoteSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.username")

    class Meta:
        model = LeadNote
        fields = ["id", "author", "note", "created_at"]


class LeadSerializer(serializers.ModelSerializer):
    office_code = serializers.CharField(source="office.code", read_only=True)
    package_code = serializers.CharField(source="interested_package.code", read_only=True, default=None)
    customer_id = serializers.CharField(source="converted_customer.customer_id", read_only=True, default=None)
    connection_id = serializers.CharField(source="converted_connection.connection_id", read_only=True, default=None)
    follow_ups = NoteSerializer(many=True, read_only=True)

    class Meta:
        model = Lead
        fields = ["id", "office", "office_code", "name", "phone", "address", "interested_package", "package_code", "source", "status",
                  "assigned_to", "notes", "lost_reason", "customer_id", "connection_id", "created_at", "follow_ups"]
        read_only_fields = fields


class LeadCreateSerializer(serializers.Serializer):
    office = serializers.IntegerField()
    name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=20)
    source = serializers.ChoiceField(choices=Lead.Source.choices, default="office")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    interested_package = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    assigned_to = serializers.IntegerField(required=False, allow_null=True)


class LeadUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=20, required=False)
    address = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["new", "contacted", "interested", "converted", "lost"], required=False)
    assigned_to = serializers.IntegerField(required=False, allow_null=True)


class NoteCreateSerializer(serializers.Serializer):
    note = serializers.CharField()


class LostSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class ConvertSerializer(serializers.Serializer):
    village = serializers.IntegerField()
    package = serializers.IntegerField()
    installation_address = serializers.CharField()
    permanent_address = serializers.CharField(required=False, allow_blank=True, default="")
    full_name = serializers.CharField(required=False, allow_blank=True, default="")
    cnic = serializers.CharField(required=False, allow_blank=True, default="")
    existing_customer = serializers.CharField(required=False, allow_blank=True, default="", help_text="customer_id for an extra connection")
    confirm_duplicates = serializers.BooleanField(default=False)
    override_reason = serializers.CharField(required=False, allow_blank=True, default="")


class LeadViewSet(OfficeScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [PermissionMap]
    permission_map = {"list": "leads.view", "retrieve": "leads.view", "create": "leads.manage", "partial_update": "leads.manage",
                      "note": "leads.manage", "lost": "leads.manage", "convert": "leads.convert"}
    http_method_names = ["get", "post", "patch", "head", "options"]
    serializer_class = LeadSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "phone", "address"]

    def get_queryset(self):
        qs = self.scoped(Lead.objects.select_related("office", "interested_package", "converted_customer", "converted_connection").prefetch_related("follow_ups__author"))
        for param, field in (("status", "status"), ("office", "office_id"), ("source", "source"), ("assigned_to", "assigned_to_id")):
            if self.request.query_params.get(param):
                qs = qs.filter(**{field: self.request.query_params[param]})
        return qs

    def _out(self, lead):
        return Response(LeadSerializer(Lead.objects.select_related("office", "interested_package", "converted_customer", "converted_connection").get(pk=lead.pk)).data)

    def _assignee(self, pk, office):
        if pk is None:
            return None
        emp = Employee.objects.filter(pk=pk, is_active=True).first()
        if emp is None or not acl.has_permission(emp, "leads.view", office.pk):
            raise Http404
        return emp

    @extend_schema(request=LeadCreateSerializer, responses=LeadSerializer)
    def create(self, request):
        s = LeadCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        self.check_office(d.pop("office"))
        office = Office.objects.get(pk=request.data["office"])
        pkg = Package.objects.filter(pk=d.pop("interested_package")).first() if d.get("interested_package") else None
        d.pop("interested_package", None)
        assignee = self._assignee(d.pop("assigned_to", None), office)
        lead = services.create_lead(actor=request.user, office=office, interested_package=pkg, assigned_to=assignee, request=request, **d)
        return Response(LeadSerializer(lead).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=LeadUpdateSerializer, responses=LeadSerializer)
    def partial_update(self, request, *args, **kwargs):
        lead = self.get_object()
        s = LeadUpdateSerializer(data=request.data, partial=True); s.is_valid(raise_exception=True)
        changes = dict(s.validated_data)
        if "assigned_to" in changes:
            changes["assigned_to"] = self._assignee(changes["assigned_to"], lead.office)
        return self._out(services.update_lead(actor=request.user, lead=lead, changes=changes, request=request))

    @extend_schema(request=NoteCreateSerializer, responses=LeadSerializer)
    @action(detail=True, methods=["post"])
    def note(self, request, pk=None):
        s = NoteCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        lead = self.get_object()
        services.add_note(actor=request.user, lead=lead, note=s.validated_data["note"])
        return self._out(lead)

    @extend_schema(request=LostSerializer, responses=LeadSerializer)
    @action(detail=True, methods=["post"])
    def lost(self, request, pk=None):
        s = LostSerializer(data=request.data); s.is_valid(raise_exception=True)
        return self._out(services.mark_lost(actor=request.user, lead=self.get_object(), reason=s.validated_data["reason"], request=request))

    @extend_schema(request=ConvertSerializer, responses=LeadSerializer)
    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        s = ConvertSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        lead = self.get_object()
        for code in ("customers.create", "connections.create"):  # converting needs the rights those actions normally need
            if lead.office_id not in acl.office_scope(request.user, code):
                raise Http404
        village = Village.objects.filter(pk=d["village"], is_active=True).first()
        package = Package.objects.filter(pk=d["package"]).first()
        if village is None or package is None:
            raise Http404
        existing = None
        if d["existing_customer"]:
            existing = Customer.objects.filter(customer_id=d["existing_customer"].upper(), office=lead.office).first() or self._missing()
        lead = services.convert(actor=request.user, lead=lead, village=village, package=package, installation_address=d["installation_address"],
                                permanent_address=d["permanent_address"] or d["installation_address"], full_name=d["full_name"], cnic=d["cnic"],
                                confirm_duplicates=d["confirm_duplicates"], override_reason=d["override_reason"], existing_customer=existing, request=request)
        return self._out(lead)

    def _missing(self):
        raise Http404
