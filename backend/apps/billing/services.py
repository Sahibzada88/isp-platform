"""Billing rules: invoices, payments, receipts, terms.

Lock order everywhere: Connection row -> Invoice row -> Payment row (prevents deadlocks).
Money is Decimal only. Nothing here touches a router; software status only.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core import sequences
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.core.settings_store import get_setting
from apps.core.terms import compute_term
from apps.customers import services as cust
from apps.customers.models import Connection

from .models import BillingTerm, Invoice, InvoiceLine, Payment, Receipt

C = Connection.Status
UNSETTLED = [Invoice.Status.OPEN, Invoice.Status.PAYMENT_PENDING]
LIVE_PAYMENT = [Payment.Status.PENDING_VERIFICATION, Payment.Status.CONFIRMED]
CENT = Decimal("0.01")
INVOICE_FIELDS = ["number", "status", "kind", "package_code", "package_price", "installation_fee", "total_amount"]


def money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT)
    except Exception:  # noqa: BLE001
        raise BusinessRuleError("Invalid amount.")


def installation_fee_amount() -> Decimal:
    return money(get_setting("installation_fee_pkr", "0"))


def installation_fee_mode() -> str:
    """'included' (fee on the first bill) or 'separate' (its own invoice, paid before the first term is invoiced)."""
    mode = get_setting("installation_fee_mode", "included")
    return mode if mode in ("included", "separate") else "included"


def _lock_conn(pk):
    return Connection.objects.select_for_update(of=("self",)).select_related("office", "customer", "package").get(pk=pk)


def _lock_invoice(pk):
    return Invoice.objects.select_for_update(of=("self",)).select_related("office", "package").get(pk=pk)


# ------------------------------------------------------------------ invoices
def _infer_kind(conn):
    if conn.status in (C.PENDING_INSTALLATION, C.PENDING_APPROVAL):
        if conn.initial_payment_confirmed_at:
            raise BusinessRuleError("The initial payment for this connection is already confirmed.")
        if (installation_fee_mode() == "separate" and installation_fee_amount() > 0 and not conn.installation_fee_waived
                and not _fee_already_charged(conn)):
            return Invoice.Kind.INSTALLATION
        return Invoice.Kind.INITIAL
    if conn.status in (C.ACTIVE, C.EXPIRED):
        return Invoice.Kind.RENEWAL
    raise BusinessRuleError(f"Cannot invoice a {conn.get_status_display().lower()} connection.")


def _fee_already_charged(conn) -> bool:
    return (InvoiceLine.objects.filter(invoice__connection=conn, kind=InvoiceLine.Kind.INSTALLATION_FEE)
            .exclude(invoice__status=Invoice.Status.VOID).exists())


def _build_invoice(actor, conn, supersedes=None, request=None):
    kind = _infer_kind(conn)
    package = conn.package if kind in (Invoice.Kind.INITIAL, Invoice.Kind.INSTALLATION) else (conn.next_package or conn.package)
    fee = Decimal("0.00")
    if kind == Invoice.Kind.INSTALLATION:
        fee = installation_fee_amount()
    elif (kind == Invoice.Kind.INITIAL and installation_fee_mode() == "included" and not conn.installation_fee_waived
          and not _fee_already_charged(conn)):
        fee = installation_fee_amount()
    price = Decimal("0.00") if kind == Invoice.Kind.INSTALLATION else package.price_pkr
    seq = sequences.next_value("invoice", str(conn.office_id))
    invoice = Invoice.objects.create(
        number=f"{conn.office.doc_prefix}-INV-{seq:06d}", office=conn.office, customer=conn.customer, connection=conn,
        kind=kind, package=package, package_code=package.code, package_name=package.name, speed_mbps=package.speed_mbps,
        package_price=price, installation_fee=fee, total_amount=price + fee,
        term_days=package.validity_days, created_by=actor, supersedes=supersedes,
    )
    if kind != Invoice.Kind.INSTALLATION:
        InvoiceLine.objects.create(invoice=invoice, kind=InvoiceLine.Kind.PACKAGE, amount=package.price_pkr,
                                   description=f"{package.name} ({package.speed_mbps} Mbps) - {package.validity_days} days")
    if fee:
        InvoiceLine.objects.create(invoice=invoice, kind=InvoiceLine.Kind.INSTALLATION_FEE, amount=fee,
                                   description="Installation fee")
    audit.log(actor=actor, action="invoice.created", entity=invoice, office=invoice.office, request=request,
              after=audit.snapshot(invoice, INVOICE_FIELDS))
    return invoice


@transaction.atomic
def create_invoice(*, actor, connection, request=None):
    """Returns (invoice, created). An existing unsettled invoice is reopened, never duplicated."""
    conn = _lock_conn(connection.pk)
    existing = Invoice.objects.filter(connection=conn, status__in=UNSETTLED).exclude(kind=Invoice.Kind.OPENING_BALANCE).first()
    if existing:
        return existing, False
    return _build_invoice(actor, conn, request=request), True


@transaction.atomic
def void_invoice(*, actor, invoice, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    _lock_conn(invoice.connection_id)
    inv = _lock_invoice(invoice.pk)
    if inv.status != Invoice.Status.OPEN:
        raise BusinessRuleError("Only open (unpaid) invoices can be voided. Paid invoices need an approved correction.")
    before = audit.snapshot(inv, INVOICE_FIELDS)
    inv.status, inv.void_reason, inv.voided_at, inv.voided_by = Invoice.Status.VOID, reason, timezone.now(), actor
    inv.save(update_fields=["status", "void_reason", "voided_at", "voided_by"])
    if inv.kind == Invoice.Kind.OPENING_BALANCE:  # the balance becomes collectable again
        from apps.imports.models import OpeningBalance
        OpeningBalance.objects.filter(invoice=inv).update(invoice=None)
    audit.log(actor=actor, action="invoice.voided", entity=inv, office=inv.office, reason=reason, request=request,
              before=before, after=audit.snapshot(inv, INVOICE_FIELDS))
    return inv


@transaction.atomic
def reissue_invoice(*, actor, invoice, reason, request=None):
    """Void an unpaid invoice and issue a fresh one using the *current* package price/fee rules."""
    conn = _lock_conn(invoice.connection_id)
    old = void_invoice(actor=actor, invoice=invoice, reason=reason, request=request)
    return _build_invoice(actor, conn, supersedes=old, request=request)


# ------------------------------------------------------------------ payments
def _ensure_eligible(conn, invoice):
    if invoice.kind == Invoice.Kind.OPENING_BALANCE:  # old debts can be collected whatever the connection's status
        return
    if invoice.kind in (Invoice.Kind.INITIAL, Invoice.Kind.INSTALLATION):
        ok = conn.status in (C.PENDING_INSTALLATION, C.PENDING_APPROVAL) and not conn.initial_payment_confirmed_at
    else:
        ok = conn.status in (C.ACTIVE, C.EXPIRED)
    if not ok:
        raise BusinessRuleError(
            f"The connection is now {conn.get_status_display().lower()}; this invoice can no longer be settled. "
            "Void it and review the connection first.")


def _receipt_number(office):
    return f"{office.doc_prefix}-RCP-{sequences.next_value('receipt', str(office.pk)):06d}"


@transaction.atomic
def record_payment(*, actor, invoice, method, amount, paid_at=None, reference="", request=None):
    conn = _lock_conn(invoice.connection_id)
    inv = _lock_invoice(invoice.pk)
    if inv.status == Invoice.Status.PAYMENT_PENDING:
        raise BusinessRuleError("A payment for this invoice is already awaiting verification.")
    if inv.status != Invoice.Status.OPEN:
        raise BusinessRuleError(f"This invoice is {inv.status} and cannot be paid.")
    amount = money(amount)
    if amount < inv.total_amount:
        raise BusinessRuleError("Partial payments are not accepted; the full invoice amount is required.")
    if amount > inv.total_amount:
        raise BusinessRuleError("The amount exceeds the invoice total. Record the exact amount and refer the "
                                "excess to the owner as an overpayment exception.")
    _ensure_eligible(conn, inv)
    now = timezone.now()
    paid_at = paid_at or now
    if paid_at > now + timedelta(minutes=5):
        raise BusinessRuleError("Transaction time cannot be in the future.")
    reference = (reference or "").strip().upper()
    is_cash = method == Payment.Method.CASH
    if is_cash:
        reference = ""
    elif not reference:
        raise BusinessRuleError("A transaction reference is required for digital payments.")
    elif Payment.objects.filter(method=method, reference=reference, status__in=LIVE_PAYMENT).exists():
        raise ConflictError("This transaction reference has already been recorded.")
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                invoice=inv, office=inv.office, customer=inv.customer, connection=conn, method=method, amount=amount,
                status=Payment.Status.PENDING_VERIFICATION, reference=reference, paid_at=paid_at, entered_by=actor)
    except IntegrityError:
        raise ConflictError("This transaction reference has already been recorded.")
    Receipt.objects.create(number=_receipt_number(inv.office), payment=payment, office=inv.office, issued_by=actor)
    audit.log(actor=actor, action="payment.recorded", entity=payment, office=inv.office, request=request,
              after={"invoice": inv.number, "method": method, "amount": str(amount), "reference": reference,
                     "status": payment.status})
    if is_cash:
        _confirm(payment, inv, conn, actor=actor, verified=False, request=request)
    else:
        inv.status = Invoice.Status.PAYMENT_PENDING
        inv.save(update_fields=["status"])
    return payment


def _confirm(payment, inv, conn, *, actor, verified, request):
    now = timezone.now()
    payment.status, payment.confirmed_at = Payment.Status.CONFIRMED, now
    if verified:
        payment.verified_by, payment.verified_at = actor, now
    payment.save()
    inv.status = Invoice.Status.PAID
    inv.save(update_fields=["status"])
    _apply_term(conn, inv, now)
    audit.log(actor=actor, action="payment.verified" if verified else "payment.confirmed", entity=payment,
              office=payment.office, request=request, after={"invoice": inv.number, "status": payment.status})


def _apply_term(conn, inv, confirmed_at):
    if inv.kind == Invoice.Kind.INSTALLATION:
        return  # the fee is paid; the first term is invoiced (and its payment applied) separately
    if inv.kind == Invoice.Kind.OPENING_BALANCE:
        from apps.imports.models import OpeningBalance
        ob = OpeningBalance.objects.select_for_update().get(invoice=inv)
        ob.status, ob.settled_at = OpeningBalance.Status.SETTLED, confirmed_at
        ob.save(update_fields=["status", "settled_at"])
        audit.log(actor=None, action="opening_balance.settled", entity=ob, office=ob.office, after={"invoice": inv.number, "amount": str(ob.amount)})
        return
    before = audit.snapshot(conn, cust.CONNECTION_AUDIT_FIELDS)
    if inv.kind == Invoice.Kind.INITIAL:
        start, end = compute_term(confirmed_at)
        conn.initial_payment_confirmed_at, conn.expires_at = confirmed_at, end
        BillingTerm.objects.create(connection=conn, invoice=inv, package=inv.package, package_price=inv.package_price,
                                   starts_at=start, ends_at=end, confirmed_at=confirmed_at, applied=True)
    elif conn.status == C.ACTIVE:  # early (or on-time) renewal: starts after the current term ends
        start, end = compute_term(confirmed_at, conn.expires_at)
        applied = start <= confirmed_at
        BillingTerm.objects.create(connection=conn, invoice=inv, package=inv.package, package_price=inv.package_price,
                                   starts_at=start, ends_at=end, confirmed_at=confirmed_at, applied=applied)
        conn.expires_at = end
        if applied:
            _switch_package(conn, inv.package)
    else:  # EXPIRED: term is paid but only starts once staff confirm the renewal
        start, end = compute_term(confirmed_at)
        BillingTerm.objects.create(connection=conn, invoice=inv, package=inv.package, package_price=inv.package_price,
                                   starts_at=start, ends_at=end, confirmed_at=None, applied=False)
    conn.save()
    audit.log(actor=None, action="connection.term_paid", entity=conn, office=conn.office,
              before=before, after=audit.snapshot(conn, cust.CONNECTION_AUDIT_FIELDS))


def _switch_package(conn, package):
    conn.package = package
    if conn.next_package_id == package.pk:
        conn.next_package = None


@transaction.atomic
def verify_payment(*, actor, payment, request=None):
    conn = _lock_conn(payment.connection_id)
    inv = _lock_invoice(payment.invoice_id)
    p = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
    if p.status != Payment.Status.PENDING_VERIFICATION:
        raise BusinessRuleError("Only payments awaiting verification can be verified.")
    if p.entered_by_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("A payment must be verified by someone other than the employee who entered it.")
    _ensure_eligible(conn, inv)
    _confirm(p, inv, conn, actor=actor, verified=True, request=request)
    return p


@transaction.atomic
def reject_payment(*, actor, payment, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    _lock_conn(payment.connection_id)
    inv = _lock_invoice(payment.invoice_id)
    p = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
    if p.status != Payment.Status.PENDING_VERIFICATION:
        raise BusinessRuleError("Only payments awaiting verification can be rejected.")
    now = timezone.now()
    p.status, p.rejection_reason = Payment.Status.REJECTED, reason
    p.save()
    Receipt.objects.filter(payment=p).update(status=Receipt.Status.INVALIDATED, invalidated_at=now,
                                             invalidation_reason=reason)
    if inv.status == Invoice.Status.PAYMENT_PENDING:
        inv.status = Invoice.Status.OPEN
        inv.save(update_fields=["status"])
    audit.log(actor=actor, action="payment.rejected", entity=p, office=p.office, reason=reason, request=request,
              after={"invoice": inv.number, "status": p.status})
    return p


# ------------------------------------------------------------------ renewal / terms
@transaction.atomic
def confirm_renewal(*, actor, connection, request=None):
    """Staff confirmation that an EXPIRED connection's paid renewal takes effect (software status only)."""
    conn = _lock_conn(connection.pk)
    if conn.status != C.EXPIRED:
        raise BusinessRuleError("Only expired connections need renewal confirmation.")
    now = timezone.now()
    term = (BillingTerm.objects.select_for_update().filter(connection=conn, confirmed_at__isnull=True, reversed_at__isnull=True,
                                                           invoice__status=Invoice.Status.PAID).order_by("-starts_at").first())
    if term is None:
        raise BusinessRuleError("There is no paid renewal awaiting confirmation.")
    if term.ends_at <= now:
        raise BusinessRuleError("The paid term has already ended; review this connection with a manager.")
    before = audit.snapshot(conn, cust.CONNECTION_AUDIT_FIELDS)
    term.confirmed_at, term.applied = now, True
    term.save(update_fields=["confirmed_at", "applied"])
    _switch_package(conn, term.package)
    conn.expires_at = term.ends_at
    cust._move(conn, C.ACTIVE)
    conn.save()
    audit.log(actor=actor, action="connection.renewal_confirmed", entity=conn, office=conn.office, request=request,
              before=before, after=audit.snapshot(conn, cust.CONNECTION_AUDIT_FIELDS))
    return conn


def apply_due_terms(*, batch_size=500, now=None):
    """Switches a connection onto its next package when a pre-paid term starts. Idempotent, bounded."""
    now = now or timezone.now()
    n = 0
    with transaction.atomic():
        terms = list(BillingTerm.objects.select_for_update(skip_locked=True, of=("self",))
                     .filter(applied=False, confirmed_at__isnull=False, reversed_at__isnull=True, starts_at__lte=now)
                     .select_related("package")[:batch_size])
        for term in terms:
            conn = Connection.objects.select_for_update(of=("self",)).get(pk=term.connection_id)
            before = audit.snapshot(conn, cust.CONNECTION_AUDIT_FIELDS)
            _switch_package(conn, term.package)
            conn.save()
            term.applied = True
            term.save(update_fields=["applied"])
            audit.log(actor=None, action="connection.term_started", entity=conn, office=conn.office,
                      before=before, after=audit.snapshot(conn, cust.CONNECTION_AUDIT_FIELDS))
            n += 1
    return n


@transaction.atomic
def waive_installation_fee(*, actor, connection, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    conn = _lock_conn(connection.pk)
    if conn.installation_fee_waived:
        raise BusinessRuleError("The installation fee is already waived.")
    if _fee_already_charged(conn):
        raise BusinessRuleError("The fee is already on an invoice. Void/reissue that invoice after waiving is not "
                                "possible; void it first, then waive.")
    conn.installation_fee_waived, conn.installation_fee_waiver_reason = True, reason
    conn.save(update_fields=["installation_fee_waived", "installation_fee_waiver_reason", "updated_at"])
    audit.log(actor=actor, action="connection.installation_fee_waived", entity=conn, office=conn.office,
              reason=reason, request=request)
    return conn


# ------------------------------------------------------------------ opening balances
@transaction.atomic
def create_opening_balance_invoice(*, actor, balance, connection=None, request=None):
    """Invoice that collects a VERIFIED opening balance. It is a collection of old money, not new income."""
    from apps.imports.models import OpeningBalance

    ob = OpeningBalance.objects.select_for_update(of=("self",)).select_related("customer", "connection").get(pk=balance.pk)
    if ob.status == OpeningBalance.Status.SETTLED:
        raise BusinessRuleError("This balance has already been collected.")
    if ob.status != OpeningBalance.Status.VERIFIED:
        raise BusinessRuleError("Verify the opening balance before invoicing it.")
    if ob.invoice_id and ob.invoice.status in UNSETTLED:
        return ob.invoice, False
    target = connection or ob.connection
    if target is None:
        raise BusinessRuleError("Choose one of the customer's connections to attach this invoice to.")
    if target.customer_id != ob.customer_id and not Invoice.objects.filter(connection=target, customer=ob.customer).exists():
        raise BusinessRuleError("That connection does not belong to this customer.")
    conn = _lock_conn(target.pk)
    seq = sequences.next_value("invoice", str(conn.office_id))
    inv = Invoice.objects.create(
        number=f"{conn.office.doc_prefix}-INV-{seq:06d}", office=conn.office, customer=ob.customer, connection=conn,
        kind=Invoice.Kind.OPENING_BALANCE, package=conn.package, package_code=conn.package.code, package_name=conn.package.name,
        speed_mbps=conn.package.speed_mbps, package_price=Decimal("0.00"), installation_fee=Decimal("0.00"), other_charges=ob.amount,
        total_amount=ob.amount, term_days=conn.package.validity_days, created_by=actor)
    InvoiceLine.objects.create(invoice=inv, kind=InvoiceLine.Kind.OPENING_BALANCE, amount=ob.amount,
                               description=f"Outstanding balance brought forward (old ref {ob.source_reference})")
    ob.invoice = inv
    ob.save(update_fields=["invoice"])
    audit.log(actor=actor, action="invoice.created", entity=inv, office=inv.office, request=request,
              after={**audit.snapshot(inv, INVOICE_FIELDS), "opening_balance": ob.pk})
    return inv, True
