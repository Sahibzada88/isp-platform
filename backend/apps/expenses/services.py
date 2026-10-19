from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core import sequences
from apps.core.exceptions import BusinessRuleError
from apps.notifications import services as notes

from .models import Expense

METHODS = ["cash", "easypaisa", "jazzcash", "bank_transfer"]


@transaction.atomic
def create_expense(*, actor, office, category, description, amount, expense_date, payment_method, request=None):
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise BusinessRuleError("The amount must be greater than zero.")
    if payment_method not in METHODS:
        raise BusinessRuleError("Invalid payment method.")
    if expense_date > timezone.localdate():
        raise BusinessRuleError("The expense date cannot be in the future.")
    number = f"{office.doc_prefix}-EXP-{sequences.next_value('expense', str(office.pk)):06d}"
    exp = Expense.objects.create(number=number, office=office, category=category, description=description, amount=amount,
                                 expense_date=expense_date, payment_method=payment_method, entered_by=actor)
    audit.log(actor=actor, action="expense.created", entity=exp, office=office, request=request,
              after={"number": number, "amount": str(amount), "category": category})
    notes.notify_permission("expenses.approve", office, kind="expense.pending", title=f"Expense {number} needs approval",
                            body=f"PKR {amount} - {category}", link="/expenses", dedupe_key=f"expense-pending-{exp.pk}", exclude=actor)
    return exp


@transaction.atomic
def decide_expense(*, actor, expense, approve, reason="", request=None):
    exp = Expense.objects.select_for_update().get(pk=expense.pk)
    if exp.status != Expense.Status.PENDING:
        raise BusinessRuleError("This expense has already been decided.")
    if exp.entered_by_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("An expense must be approved by someone other than the employee who entered it.")
    if not approve and not reason:
        raise BusinessRuleError("A reason is required when rejecting an expense.")
    exp.status = Expense.Status.APPROVED if approve else Expense.Status.REJECTED
    exp.decided_by, exp.decided_at, exp.decision_reason = actor, timezone.now(), reason
    exp.save()
    audit.log(actor=actor, action=f"expense.{exp.status}", entity=exp, office=exp.office, reason=reason, request=request,
              after={"amount": str(exp.amount)})
    notes.notify([exp.entered_by], kind="expense.decided", title=f"Expense {exp.number} {exp.status}", body=reason,
                 link="/expenses", office=exp.office, dedupe_key=f"expense-decided-{exp.pk}", exclude=actor)
    return exp
