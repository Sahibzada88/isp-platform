from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, mixins, serializers, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services as acl
from apps.accounts.drf import PermissionMap
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.billing.models import Invoice, Payment
from apps.core.exceptions import BusinessRuleError
from apps.customers.models import Connection, Customer
from apps.expenses.models import Expense
from apps.finance.models import CashDeposit, DepositItem
from apps.hr.models import Attendance, LeaveRequest
from apps.inventory.models import InventoryItem
from apps.operations.models import Complaint, Task

from apps.core.money import money_str

from . import export
from .registry import REPORTS

MAX_ROWS = 20_000
MAX_PDF_ROWS = 2_000
MAX_BYTES = 4_000_000  # Vercel's response limit is ~4.5 MB
CONTENT_TYPES = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "pdf": "application/pdf"}


def _preview(value):
    """JSON preview shows the real value (the UI renders it as text). Formula escaping is only for spreadsheet files."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return money_str(value)
    return str(value)


class ReportViewSet(viewsets.ViewSet):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def list(self, request):
        can_export = acl.has_permission(request.user, "reports.export")
        out = []
        for r in REPORTS.values():
            if acl.office_scope(request.user, r.permission):
                out.append({"name": r.name, "title": r.title,
                            "exportable": can_export and (not r.sensitive or acl.has_permission(request.user, "customers.export"))})
        return Response(out)

    @extend_schema(parameters=[OpenApiParameter("fmt", str, description="json (default preview) | csv | xlsx | pdf"),
                               OpenApiParameter("office", int), OpenApiParameter("status", str), OpenApiParameter("date_from", str),
                               OpenApiParameter("date_to", str), OpenApiParameter("page", int)], responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        report = REPORTS.get(pk)
        if report is None:
            raise Http404
        if not acl.office_scope(request.user, report.permission):
            raise PermissionDenied("You do not have access to this report.")
        params = {k: v for k, v in request.query_params.items()}
        if params.get("fy") and report.name != "financial-summary":  # a financial year is just a date range (owner-configured start month)
            from apps.core import fiscal
            try:
                params["date_from"], params["date_to"] = (str(d) for d in fiscal.fy_range(int(params["fy"])))
            except ValueError:
                raise ValidationError({"fy": "Use the starting year, e.g. 2026."})
        fmt = params.get("fmt", "json")
        if fmt not in ("json", "csv", "xlsx", "pdf"):
            raise ValidationError({"fmt": "Use json, csv, xlsx or pdf."})
        columns, rows = report.builder(request.user, params)
        rows = list(rows)

        if fmt == "json":  # server-side paginated preview; the browser never receives the whole dataset
            try:
                page, size = max(1, int(params.get("page", 1))), min(100, max(1, int(params.get("page_size", 50))))
            except ValueError:
                raise ValidationError({"page": "Must be a number."})
            window = rows[(page - 1) * size: page * size]
            return Response({"name": report.name, "title": report.title, "columns": columns, "count": len(rows), "page": page,
                             "page_size": size, "results": [[_preview(c) for c in r] for r in window]})

        if not acl.has_permission(request.user, "reports.export") or (report.sensitive and not acl.has_permission(request.user, "customers.export")):
            raise PermissionDenied("You are not allowed to export this report.")
        if len(rows) > MAX_ROWS or (fmt == "pdf" and len(rows) > MAX_PDF_ROWS):
            raise BusinessRuleError(f"Too many rows ({len(rows)}) for one {fmt.upper()} file. Narrow the filters (office, dates, status).")
        title = report.title
        if fmt == "csv":
            data = export.to_csv(columns, rows)
        elif fmt == "xlsx":
            data = export.to_xlsx(title, columns, rows)
        else:
            data = export.to_pdf(title, columns, rows, subtitle=f"Generated {timezone.localtime().strftime('%Y-%m-%d %H:%M')} by {request.user.username}")
        if len(data) > MAX_BYTES:
            raise BusinessRuleError("The file is too large to generate in one request. Narrow the filters and try again.")
        audit.log(actor=request.user, action="report.exported", entity=("report", report.name), request=request,
                  after={"format": fmt, "rows": len(rows), "filters": {k: v for k, v in params.items() if k != "fmt"}})
        resp = HttpResponse(data, content_type=CONTENT_TYPES[fmt])
        resp["Content-Disposition"] = f'attachment; filename="{report.name}-{timezone.localdate()}.{fmt}"'
        resp["Cache-Control"] = "private, no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class DashboardView(APIView):
    """Real numbers only. Each section appears only if the user holds its permission; every figure respects office scope.
    Money is split into verified / pending / reversed - never one blended number."""

    @extend_schema(parameters=[OpenApiParameter("office", int)], responses=OpenApiTypes.OBJECT)
    def get(self, request):
        u = request.user
        office = request.query_params.get("office")
        zero = Decimal("0.00")

        def sc(code, qs, field="office_id"):
            s = acl.office_scope(u, code)
            if not s:
                return None
            qs = s.apply(qs, field)
            return qs.filter(**{field: office}) if office else qs

        tz = timezone.get_current_timezone()
        today = timezone.localdate()
        day_start = timezone.make_aware(datetime.combine(today, time.min), tz)
        day_end = day_start + timedelta(days=1)
        month_start = timezone.make_aware(datetime.combine(today.replace(day=1), time.min), tz)
        out = {"generated_at": timezone.now(), "scope_note": "Revenue is not profit: bandwidth costs are not tracked yet."}

        customers = sc("customers.view", Customer.objects.all())
        if customers is not None:
            out["customers"] = {"total": customers.count(), "active": customers.filter(status="active").count()}
        conns = sc("connections.view", Connection.objects.all())
        if conns is not None:
            by = {r["status"]: r["n"] for r in conns.values("status").annotate(n=Count("id"))}
            out["connections"] = {"total": conns.count(), "by_status": by, "pending_installations": by.get("pending_installation", 0),
                                  "pending_approvals": by.get("pending_approval", 0),
                                  "expiring_in_7_days": conns.filter(status="active", expires_at__lt=timezone.now() + timedelta(days=7)).count()}

        pays = sc("billing.view", Payment.objects.all())
        if pays is not None:
            def tot(qs):
                return money_str(qs.aggregate(t=Sum("amount"))["t"])
            conf = pays.filter(status="confirmed")
            held = DepositItem.objects.filter(released_at__isnull=True).values("payment_id")
            deps = sc("billing.view", CashDeposit.objects.all())
            out["collections"] = {
                "today": {"cash": tot(conf.filter(method="cash", confirmed_at__gte=day_start, confirmed_at__lt=day_end)),
                          "digital_verified": tot(conf.exclude(method="cash").filter(confirmed_at__gte=day_start, confirmed_at__lt=day_end))},
                "month": {"cash": tot(conf.filter(method="cash", confirmed_at__gte=month_start)),
                          "digital_verified": tot(conf.exclude(method="cash").filter(confirmed_at__gte=month_start))},
                "pending_verification": {"count": pays.filter(status="pending_verification").count(), "amount": tot(pays.filter(status="pending_verification"))},
                "reversed_this_month": tot(pays.filter(status="reversed", reversed_at__gte=month_start)),
                "of_which_opening_balance_recovered_this_month": tot(conf.filter(invoice__kind="opening_balance", confirmed_at__gte=month_start)),
                "undeposited_cash": tot(conf.filter(method="cash").exclude(pk__in=held)),
                "deposits_awaiting_reconciliation": deps.filter(status="submitted").count(),
            }
            inv = sc("billing.view", Invoice.objects.filter(status__in=["open", "payment_pending"]))
            out["outstanding_invoices"] = {"count": inv.count(), "amount": money_str(inv.aggregate(t=Sum("total_amount"))["t"])}
            scope = acl.office_scope(u, "billing.view")
            if scope.everything or len(scope.ids) > 1:
                rows = conf.filter(confirmed_at__gte=month_start).values("office__code").annotate(t=Sum("amount")).order_by("office__code")
                out["office_income_month"] = {r["office__code"]: money_str(r["t"]) for r in rows}

        exps = sc("expenses.view", Expense.objects.all())
        if exps is not None:
            out["expenses"] = {"approved_this_month": money_str(exps.filter(status="approved", expense_date__gte=today.replace(day=1)).aggregate(t=Sum("amount"))["t"]),
                               "pending_approval_count": exps.filter(status="pending").count(),
                               "pending_approval_amount": money_str(exps.filter(status="pending").aggregate(t=Sum("amount"))["t"])}

        cq = Complaint.objects.filter(status__in=["new", "assigned", "in_progress", "waiting", "reopened"])
        full = sc("complaints.view", cq)
        if full is None and acl.office_scope(u, "complaints.view_assigned"):
            full = acl.office_scope(u, "complaints.view_assigned").apply(cq.filter(assigned_to=u))
        if full is not None:
            out["complaints"] = {"open": full.count(), "by_priority": {r["priority"]: r["n"] for r in full.values("priority").annotate(n=Count("id"))},
                                 "unassigned": full.filter(assigned_to__isnull=True).count()}

        if acl.has_permission(u, "tasks.work") or acl.has_permission(u, "tasks.view"):
            mine = Task.objects.filter(assigned_to=u, status__in=["todo", "in_progress"])
            out["tasks"] = {"mine_open": mine.count(), "mine_overdue": mine.filter(due_date__lt=today).count()}

        items = sc("inventory.view", InventoryItem.objects.filter(is_active=True))
        if items is not None:
            from django.db.models import F
            out["inventory"] = {"low_stock_items": items.filter(minimum_stock__gt=0, quantity__lte=F("minimum_stock")).count()}

        att = sc("attendance.view", Attendance.objects.filter(date=today))
        if att is not None:
            out["attendance_today"] = {"checked_in": att.count(), "checked_out": att.filter(check_out__isnull=False).count()}
        mine_att = Attendance.objects.filter(employee=u, date=today).first()
        out["my_attendance"] = {"checked_in": bool(mine_att and mine_att.check_in), "checked_out": bool(mine_att and mine_att.check_out)}
        leaves = sc("leave.approve", LeaveRequest.objects.filter(status="pending"))
        if leaves is not None:
            out["leave_pending"] = leaves.count()
        return Response(out)


class AuditLogSerializer(serializers.ModelSerializer):
    office_code = serializers.CharField(source="office.code", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "actor_label", "action", "entity_type", "entity_id", "office", "office_code", "reason", "before", "after", "ip_address"]
        read_only_fields = fields


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only. Records cannot be edited or deleted (enforced in code and by a database trigger)."""

    permission_classes = [PermissionMap]
    permission_map = {"list": "audit.view", "retrieve": "audit.view"}
    serializer_class = AuditLogSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["reason", "entity_id", "actor_label"]

    def get_queryset(self):
        qs = AuditLog.objects.select_related("office")
        p = self.request.query_params
        for param, field in (("entity_type", "entity_type"), ("entity_id", "entity_id"), ("office", "office_id"), ("actor", "actor_label"), ("action", "action")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        if p.get("action_prefix"):
            qs = qs.filter(action__startswith=p["action_prefix"])
        if p.get("date_from"):
            qs = qs.filter(created_at__date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(created_at__date__lte=p["date_to"])
        return qs
