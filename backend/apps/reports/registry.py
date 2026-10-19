"""Report definitions. Each builder returns (columns, rows) for ONE user: office scope and CNIC masking are applied here,
so a file can never contain data the requester may not see."""
from dataclasses import dataclass
from decimal import Decimal
from datetime import date, datetime, time, timedelta
from typing import Callable

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts import services as acl
from apps.billing.models import Invoice, Payment
from apps.customers.models import Connection, Customer, mask_cnic
from apps.expenses.models import Expense
from apps.finance.models import CashDeposit, Refund
from apps.hr.models import Attendance, LeaveRequest
from apps.imports.models import OpeningBalance
from apps.inventory.models import InventoryItem, Purchase, StockMovement
from apps.operations.models import Complaint


@dataclass
class Report:
    name: str
    title: str
    permission: str
    builder: Callable
    sensitive: bool = False  # needs customers.export in addition to reports.export


def _d(params, key):
    raw = params.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError({key: "Use YYYY-MM-DD."})


def _range(qs, field, params, is_datetime=False):
    start, end = _d(params, "date_from"), _d(params, "date_to")
    tz = timezone.get_current_timezone()
    if is_datetime:
        if start:
            qs = qs.filter(**{f"{field}__gte": timezone.make_aware(datetime.combine(start, time.min), tz)})
        if end:
            qs = qs.filter(**{f"{field}__lt": timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)})
    else:
        if start:
            qs = qs.filter(**{f"{field}__gte": start})
        if end:
            qs = qs.filter(**{f"{field}__lte": end})
    return qs


def _common(qs, params, office_field="office_id", status_field="status"):
    if params.get("office"):
        qs = qs.filter(**{office_field: params["office"]})
    if params.get("status") and status_field:
        qs = qs.filter(**{status_field: params["status"]})
    return qs


def _dt(v):
    return timezone.localtime(v).strftime("%Y-%m-%d %H:%M") if v else ""


def customers(user, params):
    scope = acl.office_scope(user, "customers.view")
    cnic = acl.office_scope(user, "customers.view_cnic")
    qs = _common(scope.apply(Customer.objects.select_related("village", "office")), params).order_by("customer_id")
    cols = ["Customer ID", "Name", "Phone", "CNIC", "Village", "Office", "Status", "Registered"]
    return cols, [[c.customer_id, c.full_name, c.phone, c.cnic if c.office_id in cnic else mask_cnic(c.cnic), c.village.code, c.office.code,
                   c.status, c.registered_on] for c in qs]


def connections(user, params):
    qs = _common(acl.office_scope(user, "connections.view").apply(Connection.objects.select_related("customer", "package", "office")), params)
    if params.get("package"):
        qs = qs.filter(package_id=params["package"])
    cols = ["Connection", "Customer ID", "Customer", "Package", "Office", "Status", "Expires"]
    return cols, [[c.connection_id, c.customer.customer_id, c.customer.full_name, c.package.code, c.office.code, c.status, _dt(c.expires_at)]
                  for c in qs.order_by("connection_id")]


def expiry(user, params):
    until = _d(params, "until") or (timezone.localdate() + timedelta(days=7))
    end = timezone.make_aware(datetime.combine(until + timedelta(days=1), time.min), timezone.get_current_timezone())
    qs = _common(acl.office_scope(user, "connections.view").apply(Connection.objects.select_related("customer", "package", "office")), params, status_field=None)
    qs = qs.filter(status__in=["active", "expired"], expires_at__lt=end).order_by("expires_at")
    cols = ["Connection", "Customer ID", "Customer", "Phone", "Package", "Office", "Status", "Expires"]
    return cols, [[c.connection_id, c.customer.customer_id, c.customer.full_name, c.customer.phone, c.package.code, c.office.code, c.status, _dt(c.expires_at)] for c in qs]


def invoices(user, params):
    qs = _range(_common(acl.office_scope(user, "billing.view").apply(Invoice.objects.select_related("customer", "connection", "office")), params), "issued_at", params, True)
    cols = ["Invoice", "Customer ID", "Connection", "Type", "Package", "Total (PKR)", "Status", "Issued"]
    return cols, [[i.number, i.customer.customer_id, i.connection.connection_id, i.kind, i.package_code, i.total_amount, i.status, _dt(i.issued_at)] for i in qs.order_by("issued_at")]


def outstanding(user, params):
    qs = acl.office_scope(user, "billing.view").apply(Invoice.objects.select_related("customer", "connection", "office")).filter(status__in=["open", "payment_pending"])
    qs = _common(qs, params, status_field=None)
    cols = ["Invoice", "Customer ID", "Customer", "Phone", "Connection", "Total (PKR)", "Status", "Issued"]
    return cols, [[i.number, i.customer.customer_id, i.customer.full_name, i.customer.phone, i.connection.connection_id, i.total_amount, i.status, _dt(i.issued_at)] for i in qs.order_by("issued_at")]


def collections(user, params):
    qs = acl.office_scope(user, "billing.view").apply(Payment.objects.select_related("customer", "invoice", "office", "receipt", "entered_by"))
    qs = _common(qs, params, status_field=None).filter(status="confirmed")
    if params.get("method"):
        qs = qs.filter(method=params["method"])
    qs = _range(qs, "confirmed_at", params, True)
    cols = ["Confirmed", "Office", "Receipt", "Invoice", "Type", "Customer ID", "Method", "Reference", "Amount (PKR)", "Entered by"]
    return cols, [[_dt(p.confirmed_at), p.office.code, p.receipt.number if hasattr(p, "receipt") else "", p.invoice.number, p.invoice.kind, p.customer.customer_id, p.method,
                   p.reference, p.amount, p.entered_by.username] for p in qs.order_by("confirmed_at")]


def pending_verification(user, params):
    qs = _common(acl.office_scope(user, "billing.view").apply(Payment.objects.select_related("customer", "invoice", "office", "entered_by")), params, status_field=None)
    qs = qs.filter(status="pending_verification")
    cols = ["Entered", "Office", "Invoice", "Customer ID", "Method", "Reference", "Amount (PKR)", "Entered by"]
    return cols, [[_dt(p.entered_at), p.office.code, p.invoice.number, p.customer.customer_id, p.method, p.reference, p.amount, p.entered_by.username] for p in qs.order_by("entered_at")]


def refunds(user, params):
    qs = _range(_common(acl.office_scope(user, "billing.view").apply(Refund.objects.select_related("payment__invoice", "office")), params), "requested_at", params, True)
    cols = ["Requested", "Office", "Invoice", "Amount (PKR)", "Status", "Reason", "Method", "Refunded on"]
    return cols, [[_dt(r.requested_at), r.office.code, r.payment.invoice.number, r.amount, r.status, r.reason, r.method, r.refunded_on] for r in qs.order_by("requested_at")]


def cash_reconciliation(user, params):
    qs = _range(_common(acl.office_scope(user, "billing.view").apply(CashDeposit.objects.select_related("collector", "office", "reconciled_by")), params), "created_at", params, True)
    cols = ["Deposit", "Office", "Collector", "Expected (PKR)", "Declared (PKR)", "Received (PKR)", "Variance (PKR)", "Status", "Reconciled by", "Variance note"]
    return cols, [[d.number, d.office.code, d.collector.username, d.expected_amount, d.declared_amount, d.received_amount, d.variance, d.status,
                   d.reconciled_by.username if d.reconciled_by else "", d.variance_note] for d in qs.order_by("created_at")]


def expenses(user, params):
    qs = _range(_common(acl.office_scope(user, "expenses.view").apply(Expense.objects.select_related("office", "entered_by", "decided_by")), params), "expense_date", params)
    if params.get("category"):
        qs = qs.filter(category=params["category"])
    cols = ["Expense", "Date", "Office", "Category", "Description", "Amount (PKR)", "Method", "Status", "Entered by", "Decided by", "Decision reason"]
    return cols, [[e.number, e.expense_date, e.office.code, e.category, e.description, e.amount, e.payment_method, e.status, e.entered_by.username,
                   e.decided_by.username if e.decided_by else "", e.decision_reason] for e in qs.order_by("expense_date")]


def complaints(user, params):
    qs = _range(_common(acl.office_scope(user, "complaints.view").apply(Complaint.objects.select_related("customer", "office", "assigned_to")), params), "created_at", params, True)
    for p, f in (("category", "category"), ("priority", "priority"), ("assigned_to", "assigned_to_id")):
        if params.get(p):
            qs = qs.filter(**{f: params[p]})
    cols = ["Complaint", "Office", "Customer ID", "Category", "Priority", "Status", "Assigned to", "Created", "Resolved"]
    return cols, [[c.number, c.office.code, c.customer.customer_id, c.category, c.priority, c.status, c.assigned_to.username if c.assigned_to else "",
                   _dt(c.created_at), _dt(c.resolved_at)] for c in qs.order_by("created_at")]


def technician_workload(user, params):
    scope = acl.office_scope(user, "complaints.view")
    open_statuses = ["assigned", "in_progress", "waiting", "reopened"]
    rows = (scope.apply(Complaint.objects.filter(assigned_to__isnull=False)).values("assigned_to__username")
            .annotate(total=Count("id"), open=Count("id", filter=Q(status__in=open_statuses))).order_by("assigned_to__username"))
    return ["Technician", "Open complaints", "All complaints"], [[r["assigned_to__username"], r["open"], r["total"]] for r in rows]


def inventory(user, params):
    qs = _common(acl.office_scope(user, "inventory.view").apply(InventoryItem.objects.select_related("office")), params, status_field=None)
    cols = ["Office", "Item", "Category", "Unit", "In stock", "Minimum", "Faulty", "Low stock"]
    return cols, [[i.office.code, i.name, i.category, i.unit, i.quantity, i.minimum_stock, i.faulty_quantity, i.minimum_stock > 0 and i.quantity <= i.minimum_stock]
                  for i in qs.order_by("office_id", "name")]


def stock_movements(user, params):
    qs = _range(_common(acl.office_scope(user, "inventory.view").apply(StockMovement.objects.select_related("item", "office", "actor")), params, status_field=None), "created_at", params, True)
    if params.get("item"):
        qs = qs.filter(item_id=params["item"])
    cols = ["When", "Office", "Item", "Type", "Change", "Balance after", "By", "Note"]
    return cols, [[_dt(m.created_at), m.office.code, m.item.name, m.kind, m.quantity_delta, m.balance_after, m.actor.username if m.actor else "system", m.note] for m in qs.order_by("created_at")]


def attendance(user, params):
    qs = _range(_common(acl.office_scope(user, "attendance.view").apply(Attendance.objects.select_related("employee", "office")), params, status_field=None), "date", params)
    cols = ["Date", "Employee", "Office", "Check in", "Check out"]
    return cols, [[a.date, a.employee.username, a.office.code, _dt(a.check_in), _dt(a.check_out)] for a in qs.order_by("date", "employee_id")]


def leave(user, params):
    qs = _range(_common(acl.office_scope(user, "leave.approve").apply(LeaveRequest.objects.select_related("employee", "office")), params), "start_date", params)
    cols = ["Employee", "Office", "From", "To", "Status", "Reason", "Decision note"]
    return cols, [[l.employee.username, l.office.code, l.start_date, l.end_date, l.status, l.reason, l.decision_note] for l in qs.order_by("start_date")]


def packages_report(user, params):
    from apps.packages.models import Package
    conns = acl.office_scope(user, "connections.view").apply(Connection.objects.all())
    counts = {}
    for r in conns.values("package_id", "status").annotate(n=Count("id")):
        counts.setdefault(r["package_id"], {})[r["status"]] = r["n"]
    cols = ["Code", "Name", "Speed (Mbps)", "Price (PKR)", "Validity (days)", "Active", "Connections active", "Connections expired", "Connections total"]
    return cols, [[p.code, p.name, p.speed_mbps, p.price_pkr, p.validity_days, p.is_active, counts.get(p.pk, {}).get("active", 0),
                   counts.get(p.pk, {}).get("expired", 0), sum(counts.get(p.pk, {}).values())] for p in Package.objects.order_by("speed_mbps", "code")]


def purchases(user, params):
    qs = _range(_common(acl.office_scope(user, "inventory.view").apply(Purchase.objects.select_related("item", "office", "entered_by")), params, status_field=None), "purchase_date", params)
    cols = ["Date", "Office", "Item", "Quantity", "Unit price (PKR)", "Total (PKR)", "Entered by", "Supplier / notes"]
    return cols, [[p.purchase_date, p.office.code, p.item.name, p.quantity, p.unit_price, p.total_amount, p.entered_by.username, p.supplier_notes] for p in qs.order_by("purchase_date")]


def employees_report(user, params):
    from apps.accounts.models import Employee
    scope = acl.office_scope(user, "employees.view")
    qs = Employee.objects.prefetch_related("assignments__office").order_by("username")
    if not scope.everything:
        qs = qs.filter(assignments__office_id__in=scope.ids, assignments__is_active=True).distinct()
    cols = ["Employee code", "Username", "Name", "Role", "Offices", "Active", "Joined"]
    return cols, [[e.employee_code, e.username, e.get_full_name(), e.role, ", ".join(a.office.code for a in e.assignments.all() if a.is_active), e.is_active, e.joining_date] for e in qs]


def _month_bounds(params):
    start, end = _d(params, "date_from"), _d(params, "date_to")
    today = timezone.localdate()
    start = start or today.replace(day=1)
    end = end or today
    tz = timezone.get_current_timezone()
    return start, end, timezone.make_aware(datetime.combine(start, time.min), tz), timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)


def branch_performance(user, params):
    from django.db.models import Sum
    from apps.organization.models import Office
    scope = acl.office_scope(user, "billing.view")
    start, end, t0, t1 = _month_bounds(params)
    offices = scope.apply(Office.objects.all(), "id")
    if params.get("office"):
        offices = offices.filter(pk=params["office"])
    rows = []
    for o in offices.order_by("code"):
        money = lambda qs, f="amount": qs.aggregate(t=Sum(f))["t"] or Decimal("0.00")
        pays = Payment.objects.filter(office=o, status="confirmed", confirmed_at__gte=t0, confirmed_at__lt=t1)
        rows.append([o.code, Customer.objects.filter(office=o).count(), Connection.objects.filter(office=o, status="active").count(),
                     Connection.objects.filter(office=o, status="expired").count(), money(pays.filter(method="cash")), money(pays.exclude(method="cash")),
                     money(Expense.objects.filter(office=o, status="approved", expense_date__gte=start, expense_date__lte=end)),
                     Complaint.objects.filter(office=o, status__in=["new", "assigned", "in_progress", "waiting", "reopened"]).count(),
                     Payment.objects.filter(office=o, status="pending_verification").count()])
    cols = ["Office", "Customers", "Active connections", "Expired connections", f"Cash {start}..{end} (PKR)", f"Digital verified {start}..{end} (PKR)",
            f"Approved expenses {start}..{end} (PKR)", "Open complaints", "Payments awaiting verification"]
    return cols, rows


def financial_summary(user, params):
    """Month-by-month for one financial year. Collections minus expenses is NOT profit: bandwidth costs are not tracked."""
    from django.db.models import Sum
    from apps.core import fiscal
    from apps.organization.models import Office
    scope = acl.office_scope(user, "billing.view")
    today = timezone.localdate()
    label = int(params["fy"]) if params.get("fy") else fiscal.fy_label_for(today)
    fy_start, fy_end = fiscal.fy_range(label)
    offices = scope.apply(Office.objects.all(), "id")
    if params.get("office"):
        offices = offices.filter(pk=params["office"])
    tz = timezone.get_current_timezone()
    rows, cursor = [], fy_start
    while cursor <= fy_end:
        nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        t0, t1 = (timezone.make_aware(datetime.combine(d, time.min), tz) for d in (cursor, nxt))
        for o in offices.order_by("code"):
            tot = lambda qs, f="amount": qs.aggregate(t=Sum(f))["t"] or Decimal("0.00")
            conf = Payment.objects.filter(office=o, status="confirmed", confirmed_at__gte=t0, confirmed_at__lt=t1)
            ob = conf.filter(invoice__kind="opening_balance")
            rows.append([cursor.strftime("%Y-%m"), o.code, tot(conf.filter(method="cash").exclude(invoice__kind="opening_balance")),
                         tot(conf.exclude(method="cash").exclude(invoice__kind="opening_balance")), tot(ob),
                         tot(Refund.objects.filter(office=o, status="paid", refunded_on__gte=cursor, refunded_on__lt=nxt)),
                         tot(Expense.objects.filter(office=o, status="approved", expense_date__gte=cursor, expense_date__lt=nxt)),
                         tot(Payment.objects.filter(office=o, status="reversed", reversed_at__gte=t0, reversed_at__lt=t1))])
        cursor = nxt
    cols = [f"Month (FY{label}: {fy_start}..{fy_end})", "Office", "New cash collected", "New digital verified", "Opening balances recovered (old money)",
            "Refunds paid", "Approved expenses", "Payments reversed"]
    return cols, rows


def opening_balances(user, params):
    qs = _common(acl.office_scope(user, "billing.view").apply(OpeningBalance.objects.select_related("customer", "connection", "office")), params, status_field="status")
    cols = ["Customer ID", "Customer", "Connection", "Office", "Amount (PKR)", "Status", "Old reference", "Migrated"]
    return cols, [[b.customer.customer_id, b.customer.full_name, b.connection.connection_id if b.connection else "", b.office.code, b.amount, b.status,
                   b.source_reference, b.migration_date] for b in qs.order_by("customer__customer_id")]


def receipts_report(user, params):
    from apps.billing.models import Receipt
    qs = _range(_common(acl.office_scope(user, "billing.view").apply(Receipt.objects.select_related("payment__invoice", "payment__customer", "office")), params), "issued_at", params, True)
    cols = ["Receipt", "Office", "Issued", "Invoice", "Customer ID", "Method", "Amount (PKR)", "Receipt status", "Payment status"]
    return cols, [[r.number, r.office.code, _dt(r.issued_at), r.payment.invoice.number, r.payment.customer.customer_id, r.payment.method, r.payment.amount, r.status,
                   r.payment.status] for r in qs.order_by("issued_at")]


REPORTS = {r.name: r for r in [
    Report("customers", "Customer directory", "customers.view", customers, sensitive=True),
    Report("connections", "Connection directory", "connections.view", connections),
    Report("expiry", "Expiry report", "connections.view", expiry),
    Report("invoices", "Invoices", "billing.view", invoices),
    Report("outstanding", "Outstanding invoices", "billing.view", outstanding),
    Report("collections", "Collection report", "billing.view", collections),
    Report("pending-verification", "Pending payment verification", "billing.view", pending_verification),
    Report("refunds", "Refund report", "billing.view", refunds),
    Report("cash-reconciliation", "Cash reconciliation", "billing.view", cash_reconciliation),
    Report("expenses", "Expense report", "expenses.view", expenses),
    Report("complaints", "Complaints", "complaints.view", complaints),
    Report("technician-workload", "Technician workload", "complaints.view", technician_workload),
    Report("inventory", "Inventory", "inventory.view", inventory),
    Report("stock-movements", "Stock movements", "inventory.view", stock_movements),
    Report("attendance", "Attendance", "attendance.view", attendance),
    Report("leave", "Leave", "leave.approve", leave),
    Report("packages", "Package report", "packages.view", packages_report),
    Report("purchases", "Purchases", "inventory.view", purchases),
    Report("employees", "Employee records", "employees.view", employees_report),
    Report("branch-performance", "Branch performance", "billing.view", branch_performance),
    Report("financial-summary", "Financial summary (not profit)", "billing.view", financial_summary),
    Report("opening-balances", "Opening balances", "billing.view", opening_balances),
    Report("receipts", "Receipts", "billing.view", receipts_report),
]}
