import hmac

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .jobs import run_job


def _cron_authorized(request) -> bool:
    secret = settings.CRON_SECRET
    if not secret:  # fail closed when not configured
        return False
    supplied = request.headers.get("Authorization", "")
    return hmac.compare_digest(supplied, f"Bearer {secret}")


@extend_schema(responses=OpenApiTypes.OBJECT)
class ExpireConnectionsCronView(APIView):
    """Vercel Cron (always GET). Idempotent; processes one bounded batch per call."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not _cron_authorized(request):
            return Response({"error": {"code": "forbidden", "message": "Forbidden."}}, status=status.HTTP_403_FORBIDDEN)
        from apps.billing.services import apply_due_terms
        from apps.customers.services import process_expiries

        size = settings.CRON_BATCH_SIZE
        terms = run_job("apply_billing_terms", lambda: apply_due_terms(batch_size=size))
        run = run_job("expire_connections", lambda: process_expiries(batch_size=size))
        ok = run.status == "succeeded" and terms.status == "succeeded"
        return Response({"status": "succeeded" if ok else "failed", "processed": run.processed_count,
                         "terms_applied": terms.processed_count},
                        status=status.HTTP_200_OK if ok else status.HTTP_500_INTERNAL_SERVER_ERROR)


from rest_framework import serializers as _s  # noqa: E402

from apps.accounts import services as _acl  # noqa: E402

from .backups import BackupNotConfigured, request_backup  # noqa: E402
from .models import BackupRequest  # noqa: E402


class _BackupSerializer(_s.ModelSerializer):
    requested_by = _s.CharField(source="requested_by.username")

    class Meta:
        model = BackupRequest
        fields = ["id", "requested_by", "requested_at", "status", "detail"]


class BackupRequestView(APIView):
    """Owner-level: ask for a manual database backup (runs in GitHub Actions) and see the recent requests."""

    @extend_schema(responses=_BackupSerializer(many=True))
    def get(self, request):
        if not _acl.has_permission(request.user, "system.manage"):
            return Response({"error": {"code": "permission_denied", "message": "Forbidden."}}, status=403)
        return Response(_BackupSerializer(BackupRequest.objects.select_related("requested_by")[:30], many=True).data)

    @extend_schema(request=None, responses=_BackupSerializer)
    def post(self, request):
        if not _acl.has_permission(request.user, "system.manage"):
            return Response({"error": {"code": "permission_denied", "message": "Forbidden."}}, status=403)
        try:
            rec = request_backup(actor=request.user, request=request)
        except BackupNotConfigured as e:
            return Response({"error": {"code": "not_configured", "message": str(e)}}, status=503)
        return Response(_BackupSerializer(rec).data, status=status.HTTP_202_ACCEPTED if rec.status == "requested" else status.HTTP_502_BAD_GATEWAY)
