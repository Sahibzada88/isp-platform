from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import services as acl
from apps.accounts.models import OfficeAssignment
from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError
from apps.notifications import services as notes

from .models import Attendance, AttendanceChange, LeaveRequest


def _office_for(employee, office):
    offices = list(OfficeAssignment.objects.filter(employee=employee, is_active=True).select_related("office"))
    if office is None:
        if len(offices) != 1:
            raise BusinessRuleError("Choose which office you are checking in at.")
        return offices[0].office
    if not any(a.office_id == office.pk for a in offices):
        raise BusinessRuleError("You are not assigned to this office.")
    return office


@transaction.atomic
def check_in(*, actor, office=None, request=None):
    office = _office_for(actor, office)
    today = timezone.localdate()
    try:
        with transaction.atomic():
            rec = Attendance.objects.create(employee=actor, office=office, date=today, check_in=timezone.now())
    except IntegrityError:
        raise BusinessRuleError("You have already checked in today.")
    audit.log(actor=actor, action="attendance.check_in", entity=rec, office=office, request=request)
    return rec


@transaction.atomic
def check_out(*, actor, request=None):
    rec = Attendance.objects.select_for_update().filter(employee=actor, date=timezone.localdate()).first()
    if rec is None or rec.check_in is None:
        raise BusinessRuleError("You have not checked in today.")
    if rec.check_out is not None:
        raise BusinessRuleError("You have already checked out today.")
    rec.check_out = timezone.now()
    rec.save(update_fields=["check_out"])
    audit.log(actor=actor, action="attendance.check_out", entity=rec, office=rec.office, request=request)
    return rec


@transaction.atomic
def correct_attendance(*, actor, attendance, check_in, check_out, reason, request=None):
    if not reason:
        raise BusinessRuleError("A reason is required to correct attendance.")
    rec = Attendance.objects.select_for_update().get(pk=attendance.pk)
    if check_in and check_out and check_out < check_in:
        raise BusinessRuleError("Check-out cannot be before check-in.")
    change = AttendanceChange.objects.create(attendance=rec, changed_by=actor, reason=reason, old_check_in=rec.check_in,
                                             old_check_out=rec.check_out, new_check_in=check_in, new_check_out=check_out)
    rec.check_in, rec.check_out = check_in, check_out
    rec.save(update_fields=["check_in", "check_out"])
    audit.log(actor=actor, action="attendance.corrected", entity=rec, office=rec.office, reason=reason, request=request,
              before={"check_in": str(change.old_check_in), "check_out": str(change.old_check_out)},
              after={"check_in": str(check_in), "check_out": str(check_out)})
    return rec


@transaction.atomic
def request_leave(*, actor, start_date, end_date, reason, office=None, request=None):
    office = _office_for(actor, office)
    if end_date < start_date:
        raise BusinessRuleError("The end date cannot be before the start date.")
    if not reason:
        raise BusinessRuleError("A reason is required.")
    if LeaveRequest.objects.filter(employee=actor, status__in=["pending", "approved"], start_date__lte=end_date,
                                   end_date__gte=start_date).exists():
        raise BusinessRuleError("You already have a leave request overlapping these dates.")
    leave = LeaveRequest.objects.create(employee=actor, office=office, start_date=start_date, end_date=end_date, reason=reason)
    audit.log(actor=actor, action="leave.requested", entity=leave, office=office, request=request,
              after={"start": str(start_date), "end": str(end_date)})
    notes.notify_permission("leave.approve", office, kind="leave.requested", title=f"Leave request from {actor.username}",
                            body=f"{start_date} to {end_date}", link="/leave", dedupe_key=f"leave-requested-{leave.pk}", exclude=actor)
    return leave


@transaction.atomic
def decide_leave(*, actor, leave, approve, note="", request=None):
    lv = LeaveRequest.objects.select_for_update().get(pk=leave.pk)
    if lv.status != LeaveRequest.Status.PENDING:
        raise BusinessRuleError("This request has already been decided.")
    if lv.employee_id == actor.pk and not actor.is_owner:
        raise BusinessRuleError("You cannot decide your own leave request.")
    if not approve and not note:
        raise BusinessRuleError("A reason is required when rejecting leave.")
    lv.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    lv.decided_by, lv.decided_at, lv.decision_note = actor, timezone.now(), note
    lv.save()
    audit.log(actor=actor, action=f"leave.{lv.status}", entity=lv, office=lv.office, reason=note, request=request)
    notes.notify([lv.employee], kind="leave.decided", title=f"Your leave was {lv.status}", body=note, link="/leave",
                 office=lv.office, dedupe_key=f"leave-decided-{lv.pk}")
    return lv


@transaction.atomic
def cancel_leave(*, actor, leave, request=None):
    lv = LeaveRequest.objects.select_for_update().get(pk=leave.pk)
    if lv.employee_id != actor.pk:
        raise BusinessRuleError("Only the employee can cancel their own request.")
    if lv.status not in (LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED) or lv.start_date <= timezone.localdate() and lv.status == "approved":
        raise BusinessRuleError("This request can no longer be cancelled.")
    lv.status = LeaveRequest.Status.CANCELLED
    lv.save(update_fields=["status"])
    audit.log(actor=actor, action="leave.cancelled", entity=lv, office=lv.office, request=request)
    return lv
