"""Office and ownership transfers (approval workflows). Lock order: Connection -> related rows."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import services as acl
from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.notifications import services as notes

from .models import Connection, ConnectionAddressHistory, Customer, OfficeTransfer, OwnershipTransfer

CONN_FIELDS = ["office", "village", "installation_address", "customer", "status"]


def _unsettled_invoices(conn):
    from apps.billing.models import Invoice
    return Invoice.objects.filter(connection=conn, status__in=["open", "payment_pending"])


def _lock(conn):
    return Connection.objects.select_for_update(of=("self",)).select_related("office", "village", "customer").get(pk=conn.pk)


# ================================================================== office transfer
@transaction.atomic
def request_office_transfer(*, actor, connection, to_village, new_address, reason, move_customer=False, request=None):
    conn = _lock(connection)
    if conn.status == Connection.Status.CANCELLED:
        raise BusinessRuleError("A cancelled connection cannot be transferred.")
    if not to_village.is_active:
        raise BusinessRuleError("The destination village is inactive.")
    if to_village.pk == conn.village_id and to_village.office_id == conn.office_id:
        raise BusinessRuleError("The connection is already in this village and office.")
    if not reason or not (new_address or "").strip():
        raise BusinessRuleError("A reason and the new installation address are required.")
    try:
        with transaction.atomic():
            t = OfficeTransfer.objects.create(
                connection=conn, customer=conn.customer, from_office=conn.office, to_office=to_village.office, from_village=conn.village,
                to_village=to_village, old_address=conn.installation_address, new_installation_address=new_address.strip(),
                move_customer=move_customer, reason=reason, requested_by=actor)
    except IntegrityError:
        raise ConflictError("A transfer request for this connection is already pending.")
    audit.log(actor=actor, action="transfer.requested", entity=t, office=conn.office, reason=reason, request=request,
              after={"connection": conn.connection_id, "from": conn.office.code, "to": to_village.office.code, "to_village": to_village.code})
    notes.notify_permission("transfers.approve", t.to_office, kind="transfer.requested", title=f"Transfer request for {conn.connection_id}",
                            body=f"{conn.office.code} -> {to_village.office.code} ({to_village.code})", link="/transfers",
                            dedupe_key=f"transfer-requested-{t.pk}", exclude=actor)
    return t


@transaction.atomic
def approve_office_transfer(*, actor, transfer, note="", request=None):
    t = OfficeTransfer.objects.select_for_update().get(pk=transfer.pk)
    if t.status != OfficeTransfer.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    if t.requested_by_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("A transfer must be approved by someone other than the employee who requested it.")
    if not acl.has_permission(actor, "transfers.approve", t.to_office_id):
        raise BusinessRuleError("You need transfer-approval rights in the destination office.")
    conn = _lock(t.connection)
    if conn.status == Connection.Status.CANCELLED:
        raise BusinessRuleError("The connection was cancelled in the meantime.")
    if conn.office_id != t.from_office_id or conn.village_id != t.from_village_id:
        raise BusinessRuleError("The connection was moved after this request was made; submit a new request.")
    if _unsettled_invoices(conn).exists():
        raise BusinessRuleError("Settle or void the connection's unpaid invoice first; invoices belong to the office that issued them.")
    before = audit.snapshot(conn, CONN_FIELDS)
    ConnectionAddressHistory.objects.create(connection=conn, old_address=conn.installation_address, new_address=t.new_installation_address,
                                            changed_by=actor, reason=f"Transfer {t.from_office.code} -> {t.to_office.code}: {t.reason}")
    conn.office, conn.village, conn.installation_address = t.to_office, t.to_village, t.new_installation_address
    conn.save()
    if t.move_customer:
        cust = Customer.objects.select_for_update().get(pk=t.customer_id)
        cust.office, cust.village, cust.updated_by = t.to_office, t.to_village, actor
        cust.save()
    _hand_over_work(conn, t, actor)
    from apps.operations.models import Task
    task = Task.objects.create(office=t.to_office, title=f"Relocate {conn.connection_id} to new address", kind=Task.Kind.INSTALLATION,
                               description=f"{t.old_address} -> {t.new_installation_address}", connection=conn, created_by=actor)
    t.status, t.decided_by, t.decided_at, t.decision_note, t.relocation_task = OfficeTransfer.Status.APPROVED, actor, timezone.now(), note, task
    t.save()
    audit.log(actor=actor, action="transfer.approved", entity=t, office=t.to_office, reason=note, request=request,
              before=before, after=audit.snapshot(conn, CONN_FIELDS))
    audit.log(actor=actor, action="connection.office_changed", entity=conn, office=t.from_office, request=request,
              before={"office": t.from_office.code}, after={"office": t.to_office.code})
    notes.notify([t.requested_by], kind="transfer.decided", title=f"Transfer of {conn.connection_id} approved", link="/transfers",
                 office=t.from_office, dedupe_key=f"transfer-decided-{t.pk}", exclude=actor)
    notes.notify_permission("tasks.manage", t.to_office, kind="task.pending", title=f"Relocation task for {conn.connection_id}",
                            link="/tasks", dedupe_key=f"transfer-task-{t.pk}", exclude=actor)
    return t


def _hand_over_work(conn, t, actor):
    """Open complaints/tasks and assigned devices follow the connection into the destination office."""
    from apps.inventory.models import Equipment
    from apps.operations.models import Complaint, ComplaintEvent, Task
    for c in Complaint.objects.select_for_update().filter(connection=conn, status__in=["new", "assigned", "in_progress", "waiting", "reopened"]):
        had = c.assigned_to.username if c.assigned_to else ""
        c.office, c.assigned_to = t.to_office, None
        if c.status in ("assigned", "in_progress", "waiting"):
            c.status = "new"
        c.save()
        ComplaintEvent.objects.create(complaint=c, kind="unassigned", actor=actor, actor_label=actor.username, from_value=had,
                                      note=f"connection transferred to office {t.to_office.code}")
        notes.notify_permission("complaints.assign", t.to_office, kind="complaint.unassigned", title=f"Complaint {c.number} moved to your office",
                                link=f"/complaints/{c.number}", dedupe_key=f"complaint-transfer-{c.pk}-{t.pk}")
    Task.objects.filter(connection=conn, status__in=["todo", "in_progress"]).update(office=t.to_office, assigned_to=None)
    Equipment.objects.filter(assigned_connection=conn).update(office=t.to_office)


@transaction.atomic
def reject_office_transfer(*, actor, transfer, note, request=None):
    if not note:
        raise BusinessRuleError("A reason is required.")
    t = OfficeTransfer.objects.select_for_update().get(pk=transfer.pk)
    if t.status != OfficeTransfer.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    t.status, t.decided_by, t.decided_at, t.decision_note = OfficeTransfer.Status.REJECTED, actor, timezone.now(), note
    t.save()
    audit.log(actor=actor, action="transfer.rejected", entity=t, office=t.from_office, reason=note, request=request)
    notes.notify([t.requested_by], kind="transfer.decided", title=f"Transfer of {t.connection.connection_id} rejected", body=note,
                 link="/transfers", office=t.from_office, dedupe_key=f"transfer-decided-{t.pk}", exclude=actor)
    return t


# ================================================================== ownership transfer
def _owing(conn):
    """Unpaid money tied to the connection: unsettled invoices + unsettled opening balances."""
    from apps.imports.models import OpeningBalance
    inv = _unsettled_invoices(conn).exists()
    ob = OpeningBalance.objects.filter(connection=conn).exclude(status="settled").exists()
    return inv or ob


@transaction.atomic
def request_ownership_transfer(*, actor, connection, new_customer, effective_date, balance_handling, reason, handling_note="", request=None):
    conn = _lock(connection)
    if conn.status == Connection.Status.CANCELLED:
        raise BusinessRuleError("A cancelled connection cannot change owner.")
    if new_customer.pk == conn.customer_id:
        raise BusinessRuleError("The new owner is already the owner of this connection.")
    if new_customer.office_id != conn.office_id:
        raise BusinessRuleError("The new owner must be registered in the same office as the connection.")
    if new_customer.status != Customer.Status.ACTIVE:
        raise BusinessRuleError("The new owner's customer record is archived.")
    if balance_handling not in OwnershipTransfer.Handling.values:
        raise BusinessRuleError("Choose how any outstanding balance is handled.")
    if balance_handling == OwnershipTransfer.Handling.OLD_OWNER_RESPONSIBLE and not handling_note:
        raise BusinessRuleError("Explain the arrangement for the previous owner's unpaid balance.")
    if not reason:
        raise BusinessRuleError("A reason is required.")
    try:
        with transaction.atomic():
            t = OwnershipTransfer.objects.create(connection=conn, from_customer=conn.customer, to_customer=new_customer, effective_date=effective_date,
                                                 balance_handling=balance_handling, handling_note=handling_note, reason=reason, requested_by=actor)
    except IntegrityError:
        raise ConflictError("An ownership transfer for this connection is already pending.")
    audit.log(actor=actor, action="ownership_transfer.requested", entity=t, office=conn.office, reason=reason, request=request,
              after={"connection": conn.connection_id, "from": conn.customer.customer_id, "to": new_customer.customer_id, "handling": balance_handling})
    notes.notify_permission("transfers.approve", conn.office, kind="ownership.requested", title=f"Ownership transfer for {conn.connection_id}",
                            link="/transfers", dedupe_key=f"ownership-requested-{t.pk}", exclude=actor)
    return t


@transaction.atomic
def approve_ownership_transfer(*, actor, transfer, note="", request=None):
    t = OwnershipTransfer.objects.select_for_update().get(pk=transfer.pk)
    if t.status != OwnershipTransfer.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    if t.requested_by_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("A transfer must be approved by someone other than the employee who requested it.")
    conn = _lock(t.connection)
    if not acl.has_permission(actor, "transfers.approve", conn.office_id):
        raise BusinessRuleError("You need transfer-approval rights in this office.")
    if conn.status == Connection.Status.CANCELLED or conn.customer_id != t.from_customer_id:
        raise BusinessRuleError("The connection changed after this request was made; submit a new request.")
    if t.effective_date > timezone.localdate():
        raise BusinessRuleError("Future-dated ownership transfers are not supported; approve on or after the effective date.")
    if t.balance_handling == OwnershipTransfer.Handling.NONE_OUTSTANDING and _owing(conn):
        raise BusinessRuleError("There is an unpaid balance on this connection. Settle it, or request the transfer again stating that the "
                                "previous owner remains responsible. Balances are never moved to the new owner silently.")
    before = audit.snapshot(conn, ["customer"])
    conn.customer = t.to_customer
    conn.save()
    t.status, t.decided_by, t.decided_at, t.decision_note = OwnershipTransfer.Status.APPROVED, actor, timezone.now(), note
    t.save()
    audit.log(actor=actor, action="ownership_transfer.approved", entity=t, office=conn.office, reason=note, request=request, before=before,
              after={"customer": t.to_customer.customer_id, "effective": str(t.effective_date), "handling": t.balance_handling})
    audit.log(actor=actor, action="connection.owner_changed", entity=conn, office=conn.office, request=request,
              before={"customer": t.from_customer.customer_id}, after={"customer": t.to_customer.customer_id})
    notes.notify([t.requested_by], kind="ownership.decided", title=f"Ownership transfer of {conn.connection_id} approved", link="/transfers",
                 office=conn.office, dedupe_key=f"ownership-decided-{t.pk}", exclude=actor)
    return t


@transaction.atomic
def reject_ownership_transfer(*, actor, transfer, note, request=None):
    if not note:
        raise BusinessRuleError("A reason is required.")
    t = OwnershipTransfer.objects.select_for_update().get(pk=transfer.pk)
    if t.status != OwnershipTransfer.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    t.status, t.decided_by, t.decided_at, t.decision_note = OwnershipTransfer.Status.REJECTED, actor, timezone.now(), note
    t.save()
    audit.log(actor=actor, action="ownership_transfer.rejected", entity=t, office=t.connection.office, reason=note, request=request)
    notes.notify([t.requested_by], kind="ownership.decided", title=f"Ownership transfer of {t.connection.connection_id} rejected", body=note,
                 link="/transfers", office=t.connection.office, dedupe_key=f"ownership-decided-{t.pk}", exclude=actor)
    return t
