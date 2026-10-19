"""Inventory: the movement ledger is the only way stock changes. Lock order: item row."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError
from apps.notifications import services as notes

from .models import (Equipment, EquipmentAssignment, InventoryItem, MaterialUsage, Purchase, ReturnRecord,
                     StockAdjustmentRequest, StockMovement)

K = StockMovement.Kind
CENT = Decimal("0.01")


def qty(value, name="Quantity"):
    try:
        v = Decimal(str(value)).quantize(CENT)
    except Exception:  # noqa: BLE001
        raise BusinessRuleError(f"{name} is not a valid number.")
    return v


def _lock_item(item):
    return InventoryItem.objects.select_for_update(of=("self",)).select_related("office").get(pk=item.pk)


def _low_stock(item):
    """Alert once when stock falls to/below the minimum; re-arm once it is back above."""
    if item.minimum_stock <= 0:
        return
    if item.quantity <= item.minimum_stock and not item.low_stock_alerted:
        item.low_stock_alerted = True
        item.low_stock_episodes += 1
        notes.notify_permission("inventory.edit", item.office, kind="inventory.low_stock", title=f"Low stock: {item.name}",
                                body=f"{item.quantity} {item.unit} left (minimum {item.minimum_stock})", link="/inventory",
                                dedupe_key=f"lowstock-{item.pk}-{item.low_stock_episodes}")
    elif item.quantity > item.minimum_stock and item.low_stock_alerted:
        item.low_stock_alerted = False


def _move(item, kind, delta, *, actor, faulty_delta=Decimal("0"), key="", note="", **refs):
    """Apply one movement to a LOCKED item. Idempotent when `key` was already used."""
    if key:
        existing = StockMovement.objects.filter(item=item, idempotency_key=key).first()
        if existing:
            return existing, False
    new_qty, new_faulty = item.quantity + delta, item.faulty_quantity + faulty_delta
    if new_qty < 0:
        raise BusinessRuleError(f"Insufficient stock: only {item.quantity} {item.unit} of {item.name} available.")
    if new_faulty < 0:
        raise BusinessRuleError("Faulty stock cannot go below zero.")
    item.quantity, item.faulty_quantity = new_qty, new_faulty
    _low_stock(item)
    item.save()
    mv = StockMovement.objects.create(item=item, office=item.office, kind=kind, quantity_delta=delta, faulty_delta=faulty_delta,
                                      balance_after=new_qty, actor=actor, note=note, idempotency_key=key, **refs)
    return mv, True


def _sync_serialized(item, actor):
    """Usable stock of a serialized item = number of AVAILABLE devices."""
    available = Equipment.objects.filter(item=item, status=Equipment.Status.AVAILABLE).count()
    delta = Decimal(available) - item.quantity
    if delta != 0:
        _move(item, K.EQUIPMENT_SYNC, delta, actor=actor, note="device status change")


@transaction.atomic
def create_item(*, actor, office, name, category, unit="pcs", is_serialized=False, minimum_stock=0, request=None):
    if InventoryItem.objects.filter(office=office, name__iexact=name).exists():
        raise BusinessRuleError("An item with this name already exists in this office.")
    item = InventoryItem.objects.create(office=office, name=name, category=category, unit=unit,
                                        is_serialized=is_serialized, minimum_stock=qty(minimum_stock))
    audit.log(actor=actor, action="inventory.item_created", entity=item, office=office, request=request,
              after={"name": name, "category": category, "serialized": is_serialized})
    return item


@transaction.atomic
def record_purchase(*, actor, item, quantity, unit_price, purchase_date, supplier_notes="", serials=None, request=None):
    item = _lock_item(item)
    quantity, unit_price = qty(quantity), qty(unit_price, "Unit price")
    if quantity <= 0 or unit_price < 0:
        raise BusinessRuleError("Quantity must be positive and the price cannot be negative.")
    if item.is_serialized:
        serials = [s.strip() for s in (serials or []) if s and s.strip()]
        if quantity != len(serials) or len(set(s.upper() for s in serials)) != len(serials):
            raise BusinessRuleError("Provide exactly one unique serial number per device purchased.")
        if Equipment.objects.filter(serial_number__in=serials).exists():
            raise BusinessRuleError("One or more serial numbers already exist.")
    purchase = Purchase.objects.create(office=item.office, item=item, quantity=quantity, unit_price=unit_price,
                                       total_amount=(quantity * unit_price).quantize(CENT), purchase_date=purchase_date,
                                       supplier_notes=supplier_notes, entered_by=actor)
    if item.is_serialized:
        Equipment.objects.bulk_create([Equipment(item=item, office=item.office, serial_number=s) for s in serials])
        _move(item, K.PURCHASE, quantity, actor=actor, note=f"purchase #{purchase.pk}")
    else:
        _move(item, K.PURCHASE, quantity, actor=actor, note=f"purchase #{purchase.pk}")
    audit.log(actor=actor, action="inventory.purchase", entity=purchase, office=item.office, request=request,
              after={"item": item.name, "quantity": str(quantity), "total": str(purchase.total_amount)})
    return purchase


@transaction.atomic
def use_materials(*, actor, item, quantity, idempotency_key, complaint=None, task=None, connection=None, request=None):
    """Deduct consumables for a job. Retrying with the same key never deducts twice."""
    if not idempotency_key:
        raise BusinessRuleError("An idempotency key is required so retries cannot deduct stock twice.")
    if not (complaint or task or connection):
        raise BusinessRuleError("Link the usage to a complaint, task or connection.")
    item = _lock_item(item)
    if item.is_serialized:
        raise BusinessRuleError("Serialized devices are assigned to a connection, not consumed.")
    quantity = qty(quantity)
    if quantity <= 0:
        raise BusinessRuleError("Quantity must be positive.")
    existing = MaterialUsage.objects.filter(item=item, idempotency_key=idempotency_key).first()
    if existing:
        return existing, False
    mv, _ = _move(item, K.USAGE, -quantity, actor=actor, key=f"use-{idempotency_key}", complaint=complaint, task=task, connection=connection)
    try:
        with transaction.atomic():
            usage = MaterialUsage.objects.create(office=item.office, item=item, quantity=quantity, used_by=actor, complaint=complaint,
                                                 task=task, connection=connection, idempotency_key=idempotency_key)
    except IntegrityError:
        raise BusinessRuleError("This usage was already recorded.")
    audit.log(actor=actor, action="inventory.material_used", entity=usage, office=item.office, request=request,
              after={"item": item.name, "quantity": str(quantity)})
    return usage, True


@transaction.atomic
def reverse_usage(*, actor, usage, reason, request=None):
    """Correct a usage by adding a compensating movement - the original is never edited or deleted."""
    if not reason:
        raise BusinessRuleError("A reason is required.")
    u = MaterialUsage.objects.select_for_update().get(pk=usage.pk)
    if u.reversed_at:
        raise BusinessRuleError("This usage was already reversed.")
    item = _lock_item(u.item)
    _move(item, K.USAGE_REVERSAL, u.quantity, actor=actor, key=f"rev-{u.pk}", note=reason, complaint=u.complaint, task=u.task, connection=u.connection)
    u.reversed_at, u.reversal_reason = timezone.now(), reason
    u.save(update_fields=["reversed_at", "reversal_reason"])
    audit.log(actor=actor, action="inventory.usage_reversed", entity=u, office=u.office, reason=reason, request=request)
    return u


@transaction.atomic
def request_adjustment(*, actor, item, delta, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    delta = qty(delta)
    if delta == 0:
        raise BusinessRuleError("The adjustment cannot be zero.")
    item = InventoryItem.objects.select_related("office").get(pk=item.pk)
    if item.is_serialized:
        raise BusinessRuleError("Serialized stock changes through device records, not quantity adjustments.")
    req = StockAdjustmentRequest.objects.create(office=item.office, item=item, requested_delta=delta, reason=reason, requested_by=actor)
    audit.log(actor=actor, action="inventory.adjustment_requested", entity=req, office=item.office, reason=reason, request=request,
              after={"item": item.name, "delta": str(delta)})
    notes.notify_permission("inventory.approve_adjustment", item.office, kind="inventory.adjustment", title=f"Stock adjustment for {item.name}",
                            body=f"{delta:+} {item.unit}: {reason}", link="/inventory", dedupe_key=f"adjustment-{req.pk}", exclude=actor)
    return req


@transaction.atomic
def decide_adjustment(*, actor, adjustment, approve, approved_delta=None, note="", request=None):
    req = StockAdjustmentRequest.objects.select_for_update().get(pk=adjustment.pk)
    if req.status != StockAdjustmentRequest.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    if req.requested_by_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("An adjustment must be approved by someone other than the requester.")
    if not approve and not note:
        raise BusinessRuleError("A reason is required when rejecting.")
    req.decided_by, req.decided_at, req.decision_note = actor, timezone.now(), note
    if approve:
        delta = qty(approved_delta) if approved_delta is not None else req.requested_delta
        item = _lock_item(req.item)
        _move(item, K.ADJUSTMENT, delta, actor=actor, key=f"adj-{req.pk}", note=req.reason)
        req.approved_delta, req.status = delta, StockAdjustmentRequest.Status.APPROVED
    else:
        req.status = StockAdjustmentRequest.Status.REJECTED
    req.save()
    audit.log(actor=actor, action=f"inventory.adjustment_{req.status}", entity=req, office=req.office, reason=note, request=request,
              after={"requested": str(req.requested_delta), "approved": str(req.approved_delta)})
    return req


@transaction.atomic
def submit_return(*, actor, item, quantity, note="", request=None):
    item = InventoryItem.objects.select_related("office").get(pk=item.pk)
    quantity = qty(quantity)
    if quantity <= 0 or item.is_serialized:
        raise BusinessRuleError("Enter a positive quantity of a consumable item.")
    rec = ReturnRecord.objects.create(office=item.office, item=item, quantity=quantity, returned_by=actor, note=note)
    audit.log(actor=actor, action="inventory.return_submitted", entity=rec, office=item.office, request=request, after={"quantity": str(quantity)})
    return rec


@transaction.atomic
def inspect_return(*, actor, record, condition, note="", request=None):
    if condition not in ("good", "faulty"):
        raise BusinessRuleError("Condition must be 'good' or 'faulty'.")
    rec = ReturnRecord.objects.select_for_update().get(pk=record.pk)
    if rec.status != ReturnRecord.Status.PENDING:
        raise BusinessRuleError("This return was already inspected.")
    item = _lock_item(rec.item)
    if condition == "good":
        _move(item, K.RETURN_GOOD, rec.quantity, actor=actor, key=f"ret-{rec.pk}", note=note)
    else:  # faulty stock stays out of usable stock
        _move(item, K.RETURN_FAULTY, Decimal("0"), faulty_delta=rec.quantity, actor=actor, key=f"ret-{rec.pk}", note=note)
    rec.status, rec.condition, rec.inspected_by, rec.inspected_at = ReturnRecord.Status.INSPECTED, condition, actor, timezone.now()
    rec.save()
    audit.log(actor=actor, action="inventory.return_inspected", entity=rec, office=rec.office, reason=note, request=request, after={"condition": condition})
    return rec


# ================================================================== serialized equipment
def _lock_equipment(eq):
    return Equipment.objects.select_for_update(of=("self",)).select_related("item", "office").get(pk=eq.pk)


@transaction.atomic
def assign_equipment(*, actor, equipment, connection, request=None):
    eq = _lock_equipment(equipment)
    from apps.customers.models import Connection
    connection = Connection.objects.select_for_update(of=("self",)).get(pk=connection.pk)   # never trust a stale object
    if eq.status != Equipment.Status.AVAILABLE:
        raise BusinessRuleError(f"This device is {eq.get_status_display().lower()} and cannot be assigned.")
    if connection.office_id != eq.office_id:
        raise BusinessRuleError("The device belongs to a different office than the connection.")
    if connection.status == "cancelled":
        raise BusinessRuleError("Cannot assign equipment to a cancelled connection.")
    now = timezone.now()
    try:
        with transaction.atomic():
            EquipmentAssignment.objects.create(equipment=eq, connection=connection, assigned_by=actor)
    except IntegrityError:
        raise BusinessRuleError("This device is already assigned to an active connection.")
    eq.status, eq.assigned_connection, eq.assigned_at, eq.returned_at = Equipment.Status.ASSIGNED, connection, now, None
    eq.save()
    _sync_serialized(_lock_item(eq.item), actor)
    audit.log(actor=actor, action="equipment.assigned", entity=eq, office=eq.office, request=request,
              after={"serial": eq.serial_number, "connection": connection.connection_id})
    return eq


@transaction.atomic
def unassign_equipment(*, actor, equipment, request=None):
    """Device comes back from the customer: it goes to PENDING_RETURN until inspected."""
    eq = _lock_equipment(equipment)
    if eq.status != Equipment.Status.ASSIGNED:
        raise BusinessRuleError("This device is not assigned to a connection.")
    cid = eq.assigned_connection.connection_id
    EquipmentAssignment.objects.filter(equipment=eq, returned_at__isnull=True).update(returned_at=timezone.now())
    eq.status, eq.assigned_connection, eq.returned_at = Equipment.Status.PENDING_RETURN, None, timezone.now()
    eq.save()
    audit.log(actor=actor, action="equipment.returned", entity=eq, office=eq.office, request=request, before={"connection": cid})
    return eq


@transaction.atomic
def inspect_equipment(*, actor, equipment, result, condition="", request=None):
    if result not in ("good", "faulty"):
        raise BusinessRuleError("Result must be 'good' or 'faulty'.")
    eq = _lock_equipment(equipment)
    if eq.status != Equipment.Status.PENDING_RETURN:
        raise BusinessRuleError("Only returned devices awaiting inspection can be inspected.")
    eq.status = Equipment.Status.AVAILABLE if result == "good" else Equipment.Status.FAULTY
    eq.condition = condition
    eq.save()
    _sync_serialized(_lock_item(eq.item), actor)
    audit.log(actor=actor, action="equipment.inspected", entity=eq, office=eq.office, request=request, after={"result": result})
    return eq


@transaction.atomic
def set_equipment_status(*, actor, equipment, status, note="", request=None):
    if status not in (Equipment.Status.FAULTY, Equipment.Status.LOST, Equipment.Status.UNDER_REPAIR, Equipment.Status.AVAILABLE):
        raise BusinessRuleError("Invalid status.")
    eq = _lock_equipment(equipment)
    if eq.status in (Equipment.Status.ASSIGNED, Equipment.Status.PENDING_RETURN):
        raise BusinessRuleError("Return and inspect the device first.")
    before = eq.status
    eq.status, eq.condition = status, note or eq.condition
    eq.save()
    _sync_serialized(_lock_item(eq.item), actor)
    audit.log(actor=actor, action="equipment.status_changed", entity=eq, office=eq.office, reason=note, request=request,
              before={"status": before}, after={"status": status})
    return eq
