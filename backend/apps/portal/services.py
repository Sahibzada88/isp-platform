from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.billing.models import Invoice
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.customers.models import Connection

from .models import CustomerAccount


class PortalLoginFailed(Exception):
    pass


def _validate_new_password(password, username=None):
    from django.core.exceptions import ValidationError

    try:
        validate_password(password)
    except ValidationError as e:
        raise BusinessRuleError(" ".join(e.messages))


@transaction.atomic
def create_account(*, actor, customer, username, password, request=None):
    username = (username or "").strip().lower()
    if len(username) < 4:
        raise BusinessRuleError("The username must be at least 4 characters.")
    _validate_new_password(password)
    if customer.status != "active":
        raise BusinessRuleError("The customer is archived.")
    try:
        with transaction.atomic():
            acc = CustomerAccount.objects.create(customer=customer, username=username, password=make_password(password), created_by=actor)
    except IntegrityError:
        raise ConflictError("This customer already has a portal account, or the username is taken.")
    audit.log(actor=actor, action="portal.account_created", entity=acc, office=customer.office, request=request,
              after={"customer": customer.customer_id, "username": username})
    return acc


def revoke_sessions(account):
    from django.contrib.sessions.models import Session

    from .auth import SESSION_KEY

    n = 0
    for sess in Session.objects.filter(expire_date__gt=timezone.now()):
        if sess.get_decoded().get(SESSION_KEY) == account.pk:
            sess.delete()
            n += 1
    return n


@transaction.atomic
def reset_password(*, actor, account, request=None):
    import secrets

    temp = secrets.token_urlsafe(10)
    account.password, account.must_change_password = make_password(temp), True
    account.failed_login_count, account.locked_until = 0, None
    account.save(update_fields=["password", "must_change_password", "failed_login_count", "locked_until"])
    revoke_sessions(account)
    audit.log(actor=actor, action="portal.password_reset", entity=account, office=account.customer.office, request=request)
    return temp


@transaction.atomic
def set_active(*, actor, account, active, request=None):
    account.is_active = active
    account.save(update_fields=["is_active"])
    audit.log(actor=actor, action="portal.account_enabled" if active else "portal.account_disabled", entity=account,
              office=account.customer.office, request=request)
    return account


def authenticate(username, password):
    acc = CustomerAccount.objects.select_related("customer").filter(username=(username or "").strip().lower()).first()
    now = timezone.now()
    if acc is None:
        make_password(password or "x")  # equalise timing
        raise PortalLoginFailed
    if acc.locked_until and acc.locked_until > now:
        raise PortalLoginFailed
    if not acc.is_active or not check_password(password or "", acc.password):
        if acc.is_active:
            acc.failed_login_count += 1
            if acc.failed_login_count >= settings.LOGIN_MAX_FAILURES:
                acc.locked_until, acc.failed_login_count = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES), 0
            acc.save(update_fields=["failed_login_count", "locked_until"])
        raise PortalLoginFailed
    acc.failed_login_count, acc.locked_until, acc.last_login = 0, None, now
    acc.save(update_fields=["failed_login_count", "locked_until", "last_login"])
    return acc


def change_password(account, old, new):
    if not check_password(old or "", account.password):
        raise BusinessRuleError("The current password is incorrect.")
    _validate_new_password(new)
    account.password, account.must_change_password = make_password(new), False
    account.save(update_fields=["password", "must_change_password"])


def charges_for(connection: Connection):
    """What the customer would pay now, and what to tell them. Read-only; never creates an invoice."""
    open_inv = Invoice.objects.filter(connection=connection, status__in=["open", "payment_pending"]).first()
    status = connection.status
    if status == "cancelled":
        return {"amount_due": None, "source": None, "invoice": None, "instructions": "This connection is cancelled. Contact your office to reconnect."}
    if status == "suspended":
        return {"amount_due": None, "source": None, "invoice": None, "instructions": "This connection is suspended. Please contact your office."}
    if open_inv:
        msg = ("Your payment is awaiting verification by the office." if open_inv.status == "payment_pending"
               else "Pay the full amount at your office or to your collector to complete this invoice.")
        return {"amount_due": str(open_inv.total_amount), "source": "open_invoice", "invoice": open_inv.number, "instructions": msg}
    if status in ("active", "expired"):
        pkg = connection.next_package or connection.package
        msg = ("Your connection has expired. Please contact your office to renew." if status == "expired"
               else "You can renew early at your office; the new period starts when the current one ends.")
        return {"amount_due": str(pkg.price_pkr), "source": "renewal_price", "invoice": None, "instructions": msg}
    return {"amount_due": None, "source": None, "invoice": None, "instructions": "Your connection is being set up. The office will contact you."}
