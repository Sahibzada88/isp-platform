"""Financial corrections, refunds, overpayment exceptions and cash deposits.

Nothing here edits or deletes a financial record: payments are marked reversed, receipts
invalidated, and every decision keeps who/when/why. Lock order: Connection -> Invoice -> Payment.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.audit import services as audit
from apps.billing import services as bs
from apps.billing.models import BillingTerm, Invoice, Payment, Receipt
from apps.core import sequences
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.customers.models import Connection

from .models import CashDeposit, DepositItem, OverpaymentException, PaymentCorrection, Refund

C = Connection.Status
LIVE_REFUND = [Refund.Status.REQUESTED, Refund.Status.APPROVED, Refund.Status.PAID]


def _require_owner(actor, what):
    if not actor.is_owner:
        raise PermissionDenied(f"Only the owner can {what}.")


# ================================================================== corrections / reversal
@transaction.atomic
def request_correction(*, actor, payment, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    p = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
    if p.status == Payment.Status.PENDING_VERIFICATION:
        raise BusinessRuleError("This payment is still awaiting verification; reject it instead of correcting it.")
    if p.status != Payment.Status.CONFIRMED:
        raise BusinessRuleError(f"A {p.status} payment cannot be corrected.")
    try:
        with transaction.atomic():
            corr = PaymentCorrection.objects.create(payment=p, office=p.office, reason=reason, requested_by=actor)
    except IntegrityError:
        raise ConflictError("A correction request for this payment is already pending.")
    audit.log(actor=actor, action="correction.requested", entity=corr, office=p.office, reason=reason, request=request,
              after={"payment": p.pk, "invoice": p.invoice.number, "amount": str(p.amount)})
    return corr


def _undo_term(conn, inv, now):
    if inv.kind == Invoice.Kind.OPENING_BALANCE:
        from apps.imports.models import OpeningBalance
        ob = OpeningBalance.objects.select_for_update().get(invoice=inv)
        ob.status, ob.settled_at = OpeningBalance.Status.VERIFIED, None
        ob.save(update_fields=["status", "settled_at"])
        return
    if inv.kind == Invoice.Kind.INSTALLATION:
        if conn.initial_payment_confirmed_at is not None:
            raise BusinessRuleError("The first term has been paid since; reverse that payment first, then the installation fee.")
        return
    term = BillingTerm.objects.select_for_update().filter(invoice=inv, reversed_at__isnull=True).first()
    if term is None:
        raise BusinessRuleError("No billing term is recorded for this payment; contact an administrator.")
    if inv.kind == Invoice.Kind.INITIAL:
        if conn.status not in (C.PENDING_INSTALLATION, C.PENDING_APPROVAL):
            raise BusinessRuleError("The connection has already been activated, so this payment can no longer be "
                                    "reversed. Cancel the connection and use a refund instead.")
        conn.initial_payment_confirmed_at, conn.expires_at = None, None
    elif term.confirmed_at is not None:  # early renewal of an active connection
        if term.starts_at <= now:
            raise BusinessRuleError("The renewed term has already started; it cannot be reversed. Use a refund instead.")
        if conn.expires_at != term.ends_at:
            raise BusinessRuleError("A later term has been paid on this connection; reverse that one first.")
        conn.expires_at = term.starts_at
    # else: renewal of an EXPIRED connection that staff never confirmed - nothing on the connection to undo
    term.reversed_at = now
    term.save(update_fields=["reversed_at"])
    conn.save()


@transaction.atomic
def approve_correction(*, actor, correction, note="", request=None):
    corr = PaymentCorrection.objects.select_for_update().get(pk=correction.pk)
    if corr.status != PaymentCorrection.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    if corr.requested_by_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("A correction must be approved by someone other than the employee who requested it.")
    payment = corr.payment
    conn = bs._lock_conn(payment.connection_id)
    inv = bs._lock_invoice(payment.invoice_id)
    p = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
    if p.status != Payment.Status.CONFIRMED:
        raise BusinessRuleError("The payment is no longer in a confirmed state.")
    if DepositItem.objects.filter(payment=p, released_at__isnull=True).exists():
        raise BusinessRuleError("This cash is part of a cash deposit. Cancel the unreconciled deposit first "
                                "(reconciled deposits cannot be altered).")
    if Refund.objects.filter(payment=p, status__in=LIVE_REFUND).exists():
        raise BusinessRuleError("A refund exists for this payment; it cannot be reversed.")
    now = timezone.now()
    before = audit.snapshot(conn, ["status", "expires_at", "package"])
    _undo_term(conn, inv, now)
    p.status, p.reversed_at, p.reversal_reason = Payment.Status.REVERSED, now, corr.reason
    p.save()
    Receipt.objects.filter(payment=p).update(status=Receipt.Status.INVALIDATED, invalidated_at=now,
                                             invalidation_reason=f"Payment reversed: {corr.reason}")
    if Invoice.objects.filter(connection=conn, status__in=bs.UNSETTLED).exclude(pk=inv.pk).exists():
        inv.status, inv.void_reason, inv.voided_at, inv.voided_by = Invoice.Status.VOID, "Payment reversed", now, actor
        inv.save(update_fields=["status", "void_reason", "voided_at", "voided_by"])
    else:
        inv.status = Invoice.Status.OPEN
        inv.save(update_fields=["status"])
    corr.status, corr.decided_by, corr.decided_at, corr.decision_note = PaymentCorrection.Status.APPROVED, actor, now, note
    corr.save()
    audit.log(actor=actor, action="correction.approved", entity=corr, office=corr.office, reason=note, request=request,
              before=before, after={"payment": p.pk, "payment_status": p.status, "invoice_status": inv.status,
                                    **audit.snapshot(conn, ["status", "expires_at", "package"])})
    audit.log(actor=actor, action="payment.reversed", entity=p, office=p.office, reason=corr.reason, request=request)
    return corr


@transaction.atomic
def reject_correction(*, actor, correction, note, request=None):
    if not note:
        raise BusinessRuleError("A reason is required.")
    corr = PaymentCorrection.objects.select_for_update().get(pk=correction.pk)
    if corr.status != PaymentCorrection.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    corr.status, corr.decided_by, corr.decided_at, corr.decision_note = (
        PaymentCorrection.Status.REJECTED, actor, timezone.now(), note)
    corr.save()
    audit.log(actor=actor, action="correction.rejected", entity=corr, office=corr.office, reason=note, request=request)
    return corr


# ================================================================== refunds
def _refunded_total(payment, exclude_pk=None):
    qs = Refund.objects.filter(payment=payment, status__in=LIVE_REFUND)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")


@transaction.atomic
def request_refund(*, actor, payment, amount, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    p = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
    if p.status != Payment.Status.CONFIRMED:
        raise BusinessRuleError("Only confirmed payments can be refunded.")
    amount = bs.money(amount)
    remaining = p.amount - _refunded_total(p)
    if amount <= 0 or amount > remaining:
        raise BusinessRuleError(f"The refund must be between 0.01 and the refundable balance (PKR {remaining}).")
    refund = Refund.objects.create(payment=p, office=p.office, amount=amount, reason=reason, requested_by=actor)
    audit.log(actor=actor, action="refund.requested", entity=refund, office=p.office, reason=reason, request=request,
              after={"payment": p.pk, "amount": str(amount)})
    return refund


def _locked_refund(refund):
    r = Refund.objects.select_for_update().get(pk=refund.pk)
    Payment.objects.select_for_update(of=("self",)).get(pk=r.payment_id)  # serialise per payment
    return r


@transaction.atomic
def approve_refund(*, actor, refund, note="", request=None):
    _require_owner(actor, "approve refunds")
    r = _locked_refund(refund)
    if r.status != Refund.Status.REQUESTED:
        raise BusinessRuleError("Only requested refunds can be approved.")
    if r.payment.status != Payment.Status.CONFIRMED:
        raise BusinessRuleError("The payment is no longer confirmed.")
    if r.amount > r.payment.amount - _refunded_total(r.payment, exclude_pk=r.pk):
        raise BusinessRuleError("This refund exceeds the refundable balance.")
    r.status, r.decided_by, r.decided_at, r.decision_note = Refund.Status.APPROVED, actor, timezone.now(), note
    r.save()
    audit.log(actor=actor, action="refund.approved", entity=r, office=r.office, reason=note, request=request)
    return r


@transaction.atomic
def reject_refund(*, actor, refund, note, request=None):
    _require_owner(actor, "reject refunds")
    if not note:
        raise BusinessRuleError("A reason is required.")
    r = _locked_refund(refund)
    if r.status != Refund.Status.REQUESTED:
        raise BusinessRuleError("Only requested refunds can be rejected.")
    r.status, r.decided_by, r.decided_at, r.decision_note = Refund.Status.REJECTED, actor, timezone.now(), note
    r.save()
    audit.log(actor=actor, action="refund.rejected", entity=r, office=r.office, reason=note, request=request)
    return r


@transaction.atomic
def mark_refund_paid(*, actor, refund, method=None, exception_reason="", refunded_on=None, request=None):
    r = _locked_refund(refund)
    if r.status != Refund.Status.APPROVED:
        raise BusinessRuleError("Only owner-approved refunds can be paid out.")
    method = method or r.payment.method
    if method != r.payment.method and not exception_reason:
        raise BusinessRuleError("Refunds go back through the original payment method; a reason is required to use another.")
    r.status, r.method, r.exception_reason = Refund.Status.PAID, method, exception_reason
    r.refunded_on, r.paid_by = refunded_on or timezone.localdate(), actor
    r.save()
    audit.log(actor=actor, action="refund.paid", entity=r, office=r.office, reason=exception_reason, request=request,
              after={"method": method, "amount": str(r.amount)})
    return r


# ================================================================== overpayment exceptions
@transaction.atomic
def record_overpayment(*, actor, office, method, amount_received, transaction_at, reference="", invoice=None,
                       notes="", request=None):
    amount_received = bs.money(amount_received)
    reference = (reference or "").strip().upper()
    if invoice is not None and amount_received <= invoice.total_amount:
        raise BusinessRuleError("This amount does not exceed the invoice total; record it as a normal payment.")
    if method != Payment.Method.CASH and not reference:
        raise BusinessRuleError("A transaction reference is required.")
    try:
        with transaction.atomic():
            exc = OverpaymentException.objects.create(
                office=office, invoice=invoice, customer=invoice.customer if invoice else None, method=method,
                reference=reference, amount_received=amount_received,
                amount_expected=invoice.total_amount if invoice else None, transaction_at=transaction_at,
                notes=notes, recorded_by=actor)
    except IntegrityError:
        raise ConflictError("An exception with this transaction reference is already recorded.")
    audit.log(actor=actor, action="overpayment.recorded", entity=exc, office=office, request=request,
              after={"amount_received": str(amount_received), "expected": str(exc.amount_expected), "reference": reference})
    return exc


@transaction.atomic
def decide_overpayment(*, actor, exception, decision, note, request=None):
    _require_owner(actor, "decide overpayment exceptions")
    if decision not in ("refund", "settlement"):
        raise BusinessRuleError("Decision must be 'refund' or 'settlement'.")
    if not note:
        raise BusinessRuleError("A note explaining the decision is required.")
    exc = OverpaymentException.objects.select_for_update().get(pk=exception.pk)
    if exc.status != OverpaymentException.Status.OPEN:
        raise BusinessRuleError("This exception has already been decided.")
    exc.status = (OverpaymentException.Status.REFUND_APPROVED if decision == "refund"
                  else OverpaymentException.Status.SETTLEMENT_APPROVED)
    exc.decided_by, exc.decided_at, exc.decision_note = actor, timezone.now(), note
    exc.save()
    audit.log(actor=actor, action=f"overpayment.{decision}_approved", entity=exc, office=exc.office, reason=note, request=request)
    return exc


@transaction.atomic
def mark_overpayment_refunded(*, actor, exception, method, request=None):
    exc = OverpaymentException.objects.select_for_update().get(pk=exception.pk)
    if exc.status != OverpaymentException.Status.REFUND_APPROVED:
        raise BusinessRuleError("Only owner-approved refunds can be marked as refunded.")
    exc.status, exc.refunded_by, exc.refunded_at, exc.refund_method = (
        OverpaymentException.Status.REFUNDED, actor, timezone.now(), method)
    exc.save()
    audit.log(actor=actor, action="overpayment.refunded", entity=exc, office=exc.office, request=request,
              after={"method": method, "excess": str(exc.amount_received - (exc.amount_expected or 0))})
    return exc


# ================================================================== cash deposits
@transaction.atomic
def create_deposit(*, actor, office, payment_ids=None, declared_amount=None, request=None):
    held = DepositItem.objects.filter(released_at__isnull=True).values("payment_id")
    qs = (Payment.objects.select_for_update(of=("self",))
          .filter(office=office, method=Payment.Method.CASH, status=Payment.Status.CONFIRMED, entered_by=actor)
          .exclude(pk__in=held))
    if payment_ids is not None:
        ids = set(payment_ids)
        qs = qs.filter(pk__in=ids)
    payments = list(qs.order_by("pk"))
    if payment_ids is not None and {p.pk for p in payments} != ids:
        raise BusinessRuleError("Some payments are not yours, not confirmed cash, or already part of a deposit.")
    if not payments:
        raise BusinessRuleError("There is no undeposited cash to deposit.")
    expected = sum((p.amount for p in payments), Decimal("0.00"))
    declared = bs.money(declared_amount) if declared_amount is not None else expected
    number = f"{office.doc_prefix}-DEP-{sequences.next_value('deposit', str(office.pk)):06d}"
    deposit = CashDeposit.objects.create(number=number, office=office, collector=actor,
                                         expected_amount=expected, declared_amount=declared)
    try:
        with transaction.atomic():
            DepositItem.objects.bulk_create([DepositItem(deposit=deposit, payment=p, amount=p.amount) for p in payments])
    except IntegrityError:
        raise BusinessRuleError("Some of this cash was just included in another deposit.")
    audit.log(actor=actor, action="deposit.created", entity=deposit, office=office, request=request,
              after={"expected": str(expected), "declared": str(declared), "payments": len(payments)})
    return deposit


@transaction.atomic
def reconcile_deposit(*, actor, deposit, received_amount, variance_note="", request=None):
    d = CashDeposit.objects.select_for_update().get(pk=deposit.pk)
    if d.status != CashDeposit.Status.SUBMITTED:
        raise BusinessRuleError("Only submitted deposits can be reconciled.")
    if d.collector_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("A deposit must be counted by someone other than the collector who made it.")
    received = bs.money(received_amount)
    if received < 0:
        raise BusinessRuleError("Received amount cannot be negative.")
    variance = received - d.expected_amount
    if variance != 0 and not variance_note:
        raise BusinessRuleError("A cash variance must be explained in the variance note for investigation.")
    d.received_amount, d.variance, d.variance_note = received, variance, variance_note
    d.status, d.reconciled_by, d.reconciled_at = CashDeposit.Status.RECONCILED, actor, timezone.now()
    d.save()
    audit.log(actor=actor, action="deposit.reconciled" if variance == 0 else "deposit.reconciled_with_variance",
              entity=d, office=d.office, reason=variance_note, request=request,
              after={"expected": str(d.expected_amount), "received": str(received), "variance": str(variance)})
    return d


@transaction.atomic
def cancel_deposit(*, actor, deposit, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    d = CashDeposit.objects.select_for_update().get(pk=deposit.pk)
    if d.status != CashDeposit.Status.SUBMITTED:
        raise BusinessRuleError("Only unreconciled deposits can be cancelled.")
    now = timezone.now()
    d.items.filter(released_at__isnull=True).update(released_at=now)
    d.status, d.cancelled_by, d.cancelled_at, d.cancel_reason = CashDeposit.Status.CANCELLED, actor, now, reason
    d.save()
    audit.log(actor=actor, action="deposit.cancelled", entity=d, office=d.office, reason=reason, request=request)
    return d
