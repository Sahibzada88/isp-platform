"""Complaint and task workflows. Every change is an append-only event + an audit record."""
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts import services as acl
from apps.accounts.signals import employee_disabled
from apps.audit import services as audit
from apps.core import sequences
from apps.core.exceptions import BusinessRuleError
from apps.notifications import services as notes

from .models import Complaint, ComplaintEvent, ComplaintWorkLog, Task

S = Complaint.Status
ALLOWED = {
    S.NEW: {S.ASSIGNED},
    S.ASSIGNED: {S.IN_PROGRESS},
    S.IN_PROGRESS: {S.WAITING, S.RESOLVED},
    S.WAITING: {S.IN_PROGRESS},
    S.RESOLVED: {S.CLOSED, S.REOPENED},
    S.CLOSED: {S.REOPENED},
    S.REOPENED: {S.ASSIGNED, S.IN_PROGRESS},
}
OPEN_STATUSES = [S.NEW, S.ASSIGNED, S.IN_PROGRESS, S.WAITING, S.REOPENED]


def _event(c, kind, actor, frm="", to="", note=""):
    return ComplaintEvent.objects.create(complaint=c, kind=kind, actor=actor if getattr(actor, "pk", None) else None,
                                         actor_label=getattr(actor, "username", "") if actor else "system",
                                         from_value=frm, to_value=to, note=note)


def _lock(c):
    return Complaint.objects.select_for_update(of=("self",)).select_related("office", "customer", "assigned_to").get(pk=c.pk)


def _move(c, new_status):
    if new_status not in ALLOWED[c.status]:
        raise BusinessRuleError(f"A {c.status.replace('_', ' ')} complaint cannot move to {new_status.replace('_', ' ')}.")
    old, c.status = c.status, new_status
    return old


def _can_work(actor, c):
    return c.assigned_to_id == actor.pk or acl.has_permission(actor, "complaints.assign", c.office_id)


def _require_worker(actor, c):
    if not _can_work(actor, c):
        raise BusinessRuleError("Only the assigned technician or a manager can update this complaint.")


@transaction.atomic
def create_complaint(*, actor, customer, category, description, connection=None, priority="medium", source="office", request=None):
    if connection is not None and connection.customer_id != customer.pk:
        raise BusinessRuleError("The connection does not belong to this customer.")
    office = connection.office if connection else customer.office
    number = f"{office.doc_prefix}-CMP-{sequences.next_value('complaint', str(office.pk)):06d}"
    c = Complaint.objects.create(number=number, office=office, customer=customer, connection=connection, category=category,
                                 description=description, priority=priority, source=source,
                                 created_by=actor if getattr(actor, "pk", None) else None)
    _event(c, "created", actor, to=S.NEW, note=f"via {source}")
    audit.log(actor=actor, action="complaint.created", entity=c, office=office, request=request,
              after={"number": number, "category": category, "priority": priority, "source": source})
    notes.notify_permission("complaints.assign", office, kind="complaint.new", title=f"New complaint {number}",
                            body=f"{customer.customer_id}: {c.get_category_display()}", link=f"/complaints/{number}",
                            dedupe_key=f"complaint-new-{c.pk}", exclude=actor if getattr(actor, "pk", None) else None)
    return c


@transaction.atomic
def assign(*, actor, complaint, technician, request=None):
    c = _lock(complaint)
    if c.status in (S.RESOLVED, S.CLOSED):
        raise BusinessRuleError("Reopen the complaint before assigning it again.")
    if not technician.is_active or not (acl.has_permission(technician, "complaints.work", c.office_id)
                                        or acl.has_permission(technician, "complaints.assign", c.office_id)):
        raise BusinessRuleError("This employee cannot take complaints in this office.")
    before = c.assigned_to.username if c.assigned_to else ""
    c.assigned_to = technician
    if c.status in (S.NEW, S.REOPENED):
        old = _move(c, S.ASSIGNED)
    c.save()
    ev = _event(c, "assigned", actor, frm=before, to=technician.username)
    audit.log(actor=actor, action="complaint.assigned", entity=c, office=c.office, request=request,
              before={"assigned_to": before}, after={"assigned_to": technician.username, "status": c.status})
    notes.notify([technician], kind="complaint.assigned", title=f"Complaint {c.number} assigned to you",
                 body=c.get_category_display(), link=f"/complaints/{c.number}", office=c.office,
                 dedupe_key=f"complaint-assigned-{c.pk}-{ev.pk}", exclude=actor)
    return c


@transaction.atomic
def set_priority(*, actor, complaint, priority, request=None):
    c = _lock(complaint)
    if priority not in Complaint.Priority.values:
        raise BusinessRuleError("Invalid priority.")
    if c.priority == priority:
        return c
    old, c.priority = c.priority, priority
    c.save(update_fields=["priority", "updated_at"])
    _event(c, "priority", actor, frm=old, to=priority)
    audit.log(actor=actor, action="complaint.priority_changed", entity=c, office=c.office, request=request,
              before={"priority": old}, after={"priority": priority})
    return c


def _transition(actor, complaint, new_status, *, require_worker=True, note="", request=None, extra=None):
    c = _lock(complaint)
    if require_worker:
        _require_worker(actor, c)
    old = _move(c, new_status)
    for k, v in (extra or {}).items():
        setattr(c, k, v(c) if callable(v) else v)
    c.save()
    _event(c, "status", actor, frm=old, to=new_status, note=note)
    audit.log(actor=actor, action=f"complaint.{new_status}", entity=c, office=c.office, request=request, reason=note,
              before={"status": old}, after={"status": new_status})
    return c


@transaction.atomic
def start_work(*, actor, complaint, request=None):
    return _transition(actor, complaint, S.IN_PROGRESS, request=request)


@transaction.atomic
def set_waiting(*, actor, complaint, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required when a complaint is put on hold.")
    return _transition(actor, complaint, S.WAITING, note=reason, request=request, extra={"waiting_reason": reason})


@transaction.atomic
def resume(*, actor, complaint, request=None):
    return _transition(actor, complaint, S.IN_PROGRESS, request=request, extra={"waiting_reason": ""})


@transaction.atomic
def resolve(*, actor, complaint, resolution_notes, request=None):
    if not resolution_notes:
        raise BusinessRuleError("Resolution notes are required.")
    c = _transition(actor, complaint, S.RESOLVED, note=resolution_notes, request=request,
                    extra={"resolution_notes": resolution_notes, "resolved_at": timezone.now(), "closed_at": None})
    notes.notify_permission("complaints.close", c.office, kind="complaint.resolved", title=f"Complaint {c.number} resolved",
                            body="Ready to close.", link=f"/complaints/{c.number}", dedupe_key=f"complaint-resolved-{c.pk}-{c.reopen_count}",
                            exclude=actor)
    return c


@transaction.atomic
def close(*, actor, complaint, note="", request=None):
    return _transition(actor, complaint, S.CLOSED, require_worker=False, note=note, request=request,
                       extra={"closed_at": timezone.now()})


@transaction.atomic
def reopen(*, actor, complaint, reason, request=None):
    """Reopens the ORIGINAL record: assignment, notes and attempts all stay."""
    if not reason:
        raise BusinessRuleError("A reason is required to reopen a complaint.")
    c = _transition(actor, complaint, S.REOPENED, require_worker=False, note=reason, request=request,
                    extra={"reopen_count": lambda c: c.reopen_count + 1, "closed_at": None})
    _event(c, "reopened", actor, note=reason)
    targets = [c.assigned_to] if c.assigned_to else []
    notes.notify(targets, kind="complaint.reopened", title=f"Complaint {c.number} reopened", body=reason,
                 link=f"/complaints/{c.number}", office=c.office, dedupe_key=f"complaint-reopened-{c.pk}-{c.reopen_count}", exclude=actor)
    notes.notify_permission("complaints.assign", c.office, kind="complaint.reopened", title=f"Complaint {c.number} reopened",
                            body=reason, link=f"/complaints/{c.number}", dedupe_key=f"complaint-reopened-{c.pk}-{c.reopen_count}", exclude=actor)
    return c


@transaction.atomic
def add_work_log(*, actor, complaint, notes_text, materials_used="", equipment_used="", request=None):
    c = _lock(complaint)
    _require_worker(actor, c)
    if c.status not in (S.ASSIGNED, S.IN_PROGRESS, S.WAITING, S.REOPENED):
        raise BusinessRuleError("Work can only be logged on an open complaint.")
    if not notes_text:
        raise BusinessRuleError("Work notes are required.")
    log = ComplaintWorkLog.objects.create(complaint=c, author=actor, notes=notes_text, materials_used=materials_used,
                                          equipment_used=equipment_used)
    _event(c, "work_log", actor, note=notes_text[:200])
    audit.log(actor=actor, action="complaint.work_logged", entity=c, office=c.office, request=request, after={"log": log.pk})
    return log


# ================================================================== tasks
TS = Task.Status


@transaction.atomic
def create_task(*, actor, office, title, assigned_to=None, request=None, **fields):
    if assigned_to is not None:
        _check_assignee(assigned_to, office)
    task = Task.objects.create(office=office, title=title, assigned_to=assigned_to, created_by=actor, **fields)
    audit.log(actor=actor, action="task.created", entity=task, office=office, request=request,
              after={"title": title, "assigned_to": assigned_to.username if assigned_to else None})
    if assigned_to:
        _notify_task(task, actor)
    return task


def _check_assignee(employee, office):
    if not employee.is_active or not (acl.has_permission(employee, "tasks.work", office.pk) or acl.has_permission(employee, "tasks.manage", office.pk)):
        raise BusinessRuleError("This employee cannot take tasks in this office.")


def _notify_task(task, actor):
    notes.notify([task.assigned_to], kind="task.assigned", title=f"Task assigned: {task.title}", link=f"/tasks/{task.pk}",
                 office=task.office, dedupe_key=f"task-assigned-{task.pk}-{task.assigned_to_id}", exclude=actor)


@transaction.atomic
def assign_task(*, actor, task, employee, request=None):
    t = Task.objects.select_for_update(of=("self",)).get(pk=task.pk)
    if t.status in (TS.DONE, TS.CANCELLED):
        raise BusinessRuleError("This task is already finished.")
    _check_assignee(employee, t.office)
    before = t.assigned_to.username if t.assigned_to else None
    t.assigned_to = employee
    t.save(update_fields=["assigned_to", "updated_at"])
    audit.log(actor=actor, action="task.assigned", entity=t, office=t.office, request=request,
              before={"assigned_to": before}, after={"assigned_to": employee.username})
    _notify_task(t, actor)
    return t


def _task_move(actor, task, new, *, allowed, note="", extra=None, request=None):
    t = Task.objects.select_for_update(of=("self",)).get(pk=task.pk)
    if t.status not in allowed:
        raise BusinessRuleError(f"A {t.status.replace('_', ' ')} task cannot be changed this way.")
    if t.assigned_to_id != actor.pk and not acl.has_permission(actor, "tasks.manage", t.office_id):
        raise BusinessRuleError("Only the assignee or a manager can update this task.")
    old, t.status = t.status, new
    for k, v in (extra or {}).items():
        setattr(t, k, v)
    t.save()
    audit.log(actor=actor, action=f"task.{new}", entity=t, office=t.office, reason=note, request=request,
              before={"status": old}, after={"status": new})
    return t


@transaction.atomic
def start_task(*, actor, task, request=None):
    return _task_move(actor, task, TS.IN_PROGRESS, allowed=[TS.TODO], request=request)


@transaction.atomic
def complete_task(*, actor, task, notes_text, request=None):
    if not notes_text:
        raise BusinessRuleError("Completion notes are required.")
    return _task_move(actor, task, TS.DONE, allowed=[TS.TODO, TS.IN_PROGRESS], note=notes_text, request=request,
                      extra={"completion_notes": notes_text, "completed_at": timezone.now()})


@transaction.atomic
def cancel_task(*, actor, task, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required.")
    return _task_move(actor, task, TS.CANCELLED, allowed=[TS.TODO, TS.IN_PROGRESS], note=reason, request=request,
                      extra={"completion_notes": reason})


# ================================================================== employee leaves: hand the work over
@receiver(employee_disabled)
def reassign_work(sender, employee, actor, reassign_to=None, **kwargs):
    complaints = Complaint.objects.select_for_update().filter(assigned_to=employee, status__in=OPEN_STATUSES)
    tasks = Task.objects.select_for_update().filter(assigned_to=employee, status__in=[TS.TODO, TS.IN_PROGRESS])
    for c in complaints:
        c.assigned_to = reassign_to
        if reassign_to is None and c.status in (S.ASSIGNED, S.IN_PROGRESS, S.WAITING):
            c.status = S.NEW
        c.save()
        _event(c, "assigned" if reassign_to else "unassigned", actor, frm=employee.username,
               to=reassign_to.username if reassign_to else "", note="previous assignee left the company")
        if reassign_to:
            notes.notify([reassign_to], kind="complaint.assigned", title=f"Complaint {c.number} reassigned to you",
                         link=f"/complaints/{c.number}", office=c.office, dedupe_key=f"complaint-handover-{c.pk}-{employee.pk}")
        else:
            notes.notify_permission("complaints.assign", c.office, kind="complaint.unassigned",
                                    title=f"Complaint {c.number} needs a new technician", link=f"/complaints/{c.number}",
                                    dedupe_key=f"complaint-handover-{c.pk}-{employee.pk}")
    for t in tasks:
        t.assigned_to = reassign_to
        t.save()
        audit.log(actor=actor, action="task.reassigned_on_offboarding", entity=t, office=t.office,
                  before={"assigned_to": employee.username}, after={"assigned_to": reassign_to.username if reassign_to else None})
        if reassign_to:
            notes.notify([reassign_to], kind="task.assigned", title=f"Task reassigned to you: {t.title}", link=f"/tasks/{t.pk}",
                         office=t.office, dedupe_key=f"task-handover-{t.pk}-{employee.pk}")
        else:
            notes.notify_permission("tasks.manage", t.office, kind="task.unassigned", title=f"Task needs an assignee: {t.title}",
                                    link=f"/tasks/{t.pk}", dedupe_key=f"task-handover-{t.pk}-{employee.pk}")
