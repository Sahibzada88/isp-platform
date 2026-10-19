"""Customer & connection business rules. All state changes go through here."""
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core import sequences
from apps.core.exceptions import BusinessRuleError, DuplicateWarning
from apps.core.terms import compute_term

from .models import Connection, ConnectionAddressHistory, Customer, CustomerAddressHistory

S = Connection.Status
CUSTOMER_AUDIT_FIELDS = ["full_name", "phone", "cnic", "permanent_address", "notes", "village", "office", "status"]
CONNECTION_AUDIT_FIELDS = ["status", "package", "next_package", "installation_address", "expires_at", "office", "village"]

ALLOWED = {
    S.PENDING_INSTALLATION: {S.PENDING_APPROVAL, S.CANCELLED},
    S.PENDING_APPROVAL: {S.ACTIVE, S.CANCELLED},
    S.ACTIVE: {S.EXPIRED, S.SUSPENDED, S.CANCELLED},
    S.EXPIRED: {S.ACTIVE, S.SUSPENDED, S.CANCELLED},
    S.SUSPENDED: {S.ACTIVE, S.EXPIRED, S.CANCELLED},
    S.CANCELLED: set(),  # only the explicit reactivation workflow may leave this state
}


def _move(conn, new_status):
    if new_status not in ALLOWED[conn.status]:
        raise BusinessRuleError(f"Cannot change a {conn.get_status_display().lower()} connection to {new_status}.")
    conn.status = new_status


# ------------------------------------------------------------------ customers
def normalize_cnic(value):
    """Single place that canonicalises a CNIC (12345-1234567-1); every entry path goes through it."""
    import re

    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if len(digits) != 13:
        raise BusinessRuleError("CNIC must contain 13 digits.")
    return f"{digits[:5]}-{digits[5:12]}-{digits[12]}"


def find_duplicates(*, full_name, phone, cnic, village_id):
    found = {}
    if phone:
        for c in Customer.objects.filter(phone=phone)[:5]:
            found.setdefault(c.customer_id, []).append("same phone")
    if cnic:
        for c in Customer.objects.filter(cnic=cnic)[:5]:
            found.setdefault(c.customer_id, []).append("same CNIC")
    for c in Customer.objects.filter(full_name__iexact=full_name, village_id=village_id)[:5]:
        found.setdefault(c.customer_id, []).append("same name in village")
    return [{"customer_id": cid, "matches": m} for cid, m in found.items()]


@transaction.atomic
def register_customer(*, actor, data, confirm_duplicates=False, reason="", request=None):
    data = {**data, "cnic": normalize_cnic(data.get("cnic", ""))}
    duplicates = find_duplicates(full_name=data["full_name"], phone=data["phone"], cnic=data.get("cnic", ""),
                                 village_id=data["village"].pk)
    if duplicates and not confirm_duplicates:
        raise DuplicateWarning(duplicates)
    village = data["village"]
    number = sequences.next_value("customer_id", village.code)
    customer = Customer.objects.create(
        customer_id=sequences.format_customer_id(village.code, number), created_by=actor, **data
    )
    audit.log(actor=actor, action="customer.created", entity=customer, office=customer.office, request=request,
              after=audit.snapshot(customer, CUSTOMER_AUDIT_FIELDS))
    if duplicates:
        audit.log(actor=actor, action="customer.duplicate_override", entity=customer, office=customer.office,
                  reason=reason, after={"duplicates": duplicates}, request=request)
    return customer


@transaction.atomic
def update_customer(*, actor, customer, changes, request=None):
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    if "cnic" in changes:
        changes = {**changes, "cnic": normalize_cnic(changes["cnic"])}
    before = audit.snapshot(customer, CUSTOMER_AUDIT_FIELDS)
    old_address = customer.permanent_address
    for field, value in changes.items():
        setattr(customer, field, value)
    customer.updated_by = actor
    customer.save()
    if customer.permanent_address != old_address:
        CustomerAddressHistory.objects.create(customer=customer, old_address=old_address,
                                              new_address=customer.permanent_address, changed_by=actor)
    audit.log(actor=actor, action="customer.updated", entity=customer, office=customer.office, request=request,
              before=before, after=audit.snapshot(customer, CUSTOMER_AUDIT_FIELDS))
    return customer


# ------------------------------------------------------------------ connections
@transaction.atomic
def create_connection(*, actor, customer, package, installation_address, village=None, office=None, request=None):
    if not package.is_active:
        raise BusinessRuleError("This package is inactive.")
    if customer.status != Customer.Status.ACTIVE:
        raise BusinessRuleError("Customer is archived.")
    village = village or customer.village
    office = office or village.office
    number = sequences.next_value("connection_id")
    conn = Connection.objects.create(
        connection_id=sequences.format_connection_id(number), customer=customer, village=village, office=office,
        package=package, installation_address=installation_address, created_by=actor,
    )
    audit.log(actor=actor, action="connection.created", entity=conn, office=conn.office, request=request,
              after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


def _lock(conn):
    return Connection.objects.select_for_update().get(pk=conn.pk)


@transaction.atomic
def complete_installation(*, actor, connection, request=None):
    conn = _lock(connection)
    if conn.status != S.PENDING_INSTALLATION:
        raise BusinessRuleError("Installation can only be completed for connections pending installation.")
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    conn.installation_completed_at = timezone.now()
    _move(conn, S.PENDING_APPROVAL)
    conn.save()
    audit.log(actor=actor, action="connection.installation_completed", entity=conn, office=conn.office, request=request,
              before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


@transaction.atomic
def approve_activation(*, actor, connection, request=None):
    """New/reactivated connection becomes Active only with payment + installation + approval."""
    conn = _lock(connection)
    if conn.status != S.PENDING_APPROVAL:
        raise BusinessRuleError("Only connections pending approval can be activated.")
    missing = []
    if not conn.initial_payment_confirmed_at:
        missing.append("payment is not confirmed")
    if not conn.installation_completed_at:
        missing.append("installation is not completed")
    if missing:
        raise BusinessRuleError("Cannot activate: " + " and ".join(missing) + ".")
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    now = timezone.now()
    conn.activation_approved_at, conn.activation_approved_by, conn.activated_at = now, actor, now
    if conn.expires_at is None:  # expiry = confirmed payment timestamp + 30 days
        _, conn.expires_at = compute_term(conn.initial_payment_confirmed_at)
    _move(conn, S.ACTIVE)
    conn.save()
    audit.log(actor=actor, action="connection.activated", entity=conn, office=conn.office, request=request,
              before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


@transaction.atomic
def suspend_connection(*, actor, connection, reason, request=None):
    conn = _lock(connection)
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    _move(conn, S.SUSPENDED)
    conn.save()
    audit.log(actor=actor, action="connection.suspended", entity=conn, office=conn.office, reason=reason, request=request,
              before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


@transaction.atomic
def cancel_connection(*, actor, connection, reason, equipment_unreturned=False, request=None):
    conn = _lock(connection)
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    _move(conn, S.CANCELLED)
    conn.cancelled_at, conn.cancellation_reason = timezone.now(), reason
    conn.equipment_return_status = (Connection.EquipmentReturn.PENDING_RETURN if equipment_unreturned
                                    else Connection.EquipmentReturn.NOT_APPLICABLE)
    conn.save()
    audit.log(actor=actor, action="connection.cancelled", entity=conn, office=conn.office, reason=reason, request=request,
              before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


@transaction.atomic
def reactivate_connection(*, actor, connection, reason, request=None):
    """Reopens a cancelled connection; payment, installation and approval are required again."""
    conn = _lock(connection)
    if conn.status != S.CANCELLED:
        raise BusinessRuleError("Only cancelled connections can be reactivated.")
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    conn.status = S.PENDING_INSTALLATION
    conn.initial_payment_confirmed_at = conn.installation_completed_at = None
    conn.activation_approved_at = conn.activation_approved_by = conn.activated_at = None
    conn.expires_at = None
    conn.save()
    audit.log(actor=actor, action="connection.reactivation_started", entity=conn, office=conn.office, reason=reason,
              request=request, before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


@transaction.atomic
def change_installation_address(*, actor, connection, new_address, reason="", request=None):
    conn = _lock(connection)
    if conn.status == S.CANCELLED:
        raise BusinessRuleError("Cannot relocate a cancelled connection.")
    if new_address.strip() == conn.installation_address.strip():
        raise BusinessRuleError("The new address is the same as the current one.")
    ConnectionAddressHistory.objects.create(connection=conn, old_address=conn.installation_address,
                                            new_address=new_address, changed_by=actor, reason=reason)
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    conn.installation_address = new_address
    conn.save()
    audit.log(actor=actor, action="connection.address_changed", entity=conn, office=conn.office, reason=reason,
              request=request, before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


@transaction.atomic
def schedule_package_change(*, actor, connection, package, request=None):
    """Applies at the next renewal; the current term and price are untouched."""
    conn = _lock(connection)
    if not package.is_active:
        raise BusinessRuleError("This package is inactive.")
    if conn.status == S.CANCELLED:
        raise BusinessRuleError("Cannot change the package of a cancelled connection.")
    before = audit.snapshot(conn, CONNECTION_AUDIT_FIELDS)
    conn.next_package = None if package.pk == conn.package_id else package
    conn.save()
    audit.log(actor=actor, action="connection.package_change_scheduled", entity=conn, office=conn.office, request=request,
              before=before, after=audit.snapshot(conn, CONNECTION_AUDIT_FIELDS))
    return conn


# ------------------------------------------------------------------ expiry
def process_expiries(*, batch_size=500, now=None):
    """Marks ACTIVE connections past their expiry as EXPIRED. Idempotent and bounded.

    Only ACTIVE rows are touched, so Cancelled / Pending / Suspended / already-Expired
    connections are never altered. This changes the *software* status only; the
    physical router is disconnected manually by staff.
    """
    now = now or timezone.now()
    with transaction.atomic():
        due = list(
            Connection.objects.select_for_update(skip_locked=True)
            .filter(status=S.ACTIVE, expires_at__lte=now)
            .order_by("expires_at")[:batch_size]
        )
        for conn in due:
            conn.status = S.EXPIRED
            conn.save(update_fields=["status", "updated_at"])
            audit.log(actor=None, action="connection.expired", entity=conn, office=conn.office,
                      before={"status": S.ACTIVE}, after={"status": S.EXPIRED, "expires_at": str(conn.expires_at)})
    return len(due)


def expire_if_due(connection, now=None):
    """Access-time check used by read paths; same rule as the batch job."""
    now = now or timezone.now()
    if connection.status == S.ACTIVE and connection.expires_at and connection.expires_at <= now:
        return process_expiries_for(connection, now)
    return connection


@transaction.atomic
def process_expiries_for(connection, now):
    conn = Connection.objects.select_for_update().get(pk=connection.pk)
    if conn.status == S.ACTIVE and conn.expires_at and conn.expires_at <= now:
        conn.status = S.EXPIRED
        conn.save(update_fields=["status", "updated_at"])
        audit.log(actor=None, action="connection.expired", entity=conn, office=conn.office,
                  before={"status": S.ACTIVE}, after={"status": S.EXPIRED})
    return conn
