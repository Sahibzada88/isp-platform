import base64

from django.http import Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts import services as acl

from . import services
from .models import Attachment


class AttachmentSerializer(serializers.ModelSerializer):
    """Metadata only - never includes image content."""

    class Meta:
        model = Attachment
        fields = ["id", "entity_type", "entity_id", "office", "kind", "filename", "mime_type", "width", "height", "size_bytes",
                  "original_format", "checksum_sha256", "created_by", "created_at"]
        read_only_fields = fields


class UploadSerializer(serializers.Serializer):
    entity_type = serializers.ChoiceField(choices=sorted(services.ENTITIES))
    entity_id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=Attachment.Kind.choices, default="other")
    filename = serializers.CharField(max_length=200, required=False, allow_blank=True, default="image")
    data = serializers.CharField(help_text="Base64 image (a data: URL is accepted)")


class AttachmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = AttachmentSerializer

    def get_queryset(self):
        return Attachment.objects.all()  # content table is never joined here

    def _authorise(self, att):
        services.resolve_entity(self.request.user, att.entity_type, att.entity_id, write=False)
        return att

    def list(self, request, *args, **kwargs):
        et, eid = request.query_params.get("entity_type"), request.query_params.get("entity_id")
        if not et or not eid or not eid.isdigit():
            raise serializers.ValidationError({"entity_type": "entity_type and entity_id are required."})
        services.resolve_entity(request.user, et, int(eid), write=False)
        qs = Attachment.objects.filter(entity_type=et, entity_id=int(eid))
        return Response(AttachmentSerializer(qs, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        att = Attachment.objects.filter(pk=kwargs["pk"]).first() or self._missing()
        return Response(AttachmentSerializer(self._authorise(att)).data)

    def _missing(self):
        raise Http404

    @extend_schema(request=UploadSerializer, responses=AttachmentSerializer)
    def create(self, request):
        s = UploadSerializer(data=request.data); s.is_valid(raise_exception=True)
        d = s.validated_data
        att = services.upload(actor=request.user, entity_type=d["entity_type"], entity_id=d["entity_id"], kind=d["kind"],
                              filename=d["filename"], data=d["data"], request=request)
        return Response(AttachmentSerializer(att).data, status=status.HTTP_201_CREATED)

    def _binary(self, request, pk, field, mime):
        att = self._authorise(Attachment.objects.filter(pk=pk).first() or self._missing())
        content = getattr(att.data, field)  # the only place the Base64 content is read
        resp = HttpResponse(base64.b64decode(content), content_type=mime)
        resp["Cache-Control"] = "private, no-store"
        resp["Content-Disposition"] = "inline"
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return resp

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def image(self, request, pk=None):
        return self._binary(request, pk, "data_b64", "image/jpeg")

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def thumbnail(self, request, pk=None):
        return self._binary(request, pk, "thumb_b64", "image/jpeg")
