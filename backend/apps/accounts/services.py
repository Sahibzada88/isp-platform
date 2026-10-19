import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core import sequences
from apps.core.exceptions import BusinessRuleError

from .models import OfficeAssignment, PermissionGrant, Role
from .permissions import GLOBAL_PERMISSIONS, PERMISSIONS, ROLE_DEFAULTS

Employee = get_user_model()


# ------------------------------------------------------------------ scope checks
class OfficeScope:
    """The set of offices in which a user holds a given permission."""

    def __init__(self, everything, ids=()):
        self.everything = everything
        self.ids = set(ids)

    def __contains__(self, office_id):
        return self.everything or office_id in self.ids

    def __bool__(self):
        return self.everything or bool(self.ids)

    def apply(self, queryset, field="office_id"):
        return queryset if self.everything else queryset.filter(**{f"{field}__in": self.ids})


def office_scope(user, code) -> OfficeScope:
    if not (user and user.is_authenticated and user.is_active):
        return OfficeScope(False)
    if user.is_owner:
        return OfficeScope(True)
    if code in GLOBAL_PERMISSIONS:
        has = PermissionGrant.objects.filter(employee=user, code=code, office__isnull=True).exists()
        return OfficeScope(has)
    ids = PermissionGrant.objects.filter(
        employee=user,
        code=code,
        office__isnull=False,
        office__assignments__employee=user,  # grant only counts while still assigned to that office
        office__assignments__is_active=True,
    ).values_list("office_id", flat=True)
    return OfficeScope(False, set(ids))


def has_permission(user, code, office_id=None) -> bool:
    scope = office_scope(user, code)
    if office_id is None:
        return bool(scope)
    return office_id in scope


# ------------------------------------------------------------------ authentication
class LoginFailed(Exception):
    pass


def _find_user(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    user = Employee.objects.filter(username__iexact=identifier).first()
    if user is None:
        matches = list(Employee.objects.filter(email__iexact=identifier)[:2])
        user = matches[0] if len(matches) == 1 else None  # ambiguous e-mail => no login
    return user


def authenticate_employee(identifier, password, request=None):
    """Returns the user or raises LoginFailed (always the same generic failure)."""
    user = _find_user(identifier)
    now = timezone.now()
    if user is None:
        Employee().set_password(password or "x")  # equalise timing
        raise LoginFailed
    if user.locked_until and user.locked_until > now:
        raise LoginFailed
    if not user.is_active or not user.check_password(password or ""):
        if user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.LOGIN_MAX_FAILURES:
                user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                user.failed_login_count = 0
                audit.log(actor=None, action="auth.account_locked", entity=user, request=request)
            user.save(update_fields=["failed_login_count", "locked_until"])
        raise LoginFailed
    user.failed_login_count, user.locked_until = 0, None
    user.save(update_fields=["failed_login_count", "locked_until"])
    return user


def revoke_user_sessions(user) -> int:
    count = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()
            count += 1
    return count


# ------------------------------------------------------------------ employee admin
@transaction.atomic
def create_employee(*, actor, username, password, role, offices, first_name="", last_name="", email="", phone="", request=None):
    if role == Role.OWNER and not (actor and actor.is_owner):
        raise BusinessRuleError("Only an owner can create another owner.")
    employee = Employee(username=username, role=role, first_name=first_name, last_name=last_name, email=email, phone=phone)
    employee.employee_code = f"EMP-{sequences.next_value('employee_code'):04d}"
    employee.set_password(password)
    employee.save()
    for office in offices:
        assign_office(actor=actor, employee=employee, office=office, request=request, apply_defaults=True)
    audit.log(actor=actor, action="employee.created", entity=employee, after={"role": role, "username": username}, request=request)
    return employee


@transaction.atomic
def assign_office(*, actor, employee, office, request=None, apply_defaults=True):
    OfficeAssignment.objects.update_or_create(employee=employee, office=office, defaults={"is_active": True})
    if apply_defaults and employee.role != Role.OWNER:
        for code in ROLE_DEFAULTS.get(employee.role, []):
            PermissionGrant.objects.get_or_create(employee=employee, office=office, code=code, defaults={"granted_by": actor})
    audit.log(actor=actor, action="employee.office_assigned", entity=employee, office=office, request=request)


@transaction.atomic
def set_office_permissions(*, actor, employee, office, codes, request=None):
    """Replace the employee's permission set in one office (office=None => global set)."""
    codes = set(codes)
    unknown = codes - set(PERMISSIONS)
    if unknown:
        raise BusinessRuleError(f"Unknown permissions: {', '.join(sorted(unknown))}")
    if office is None and not codes <= GLOBAL_PERMISSIONS:
        raise BusinessRuleError("Only global permissions can be granted without an office.")
    if office is not None and codes & GLOBAL_PERMISSIONS:
        raise BusinessRuleError("Global permissions cannot be granted for a single office.")
    if office is not None and not OfficeAssignment.objects.filter(employee=employee, office=office, is_active=True).exists():
        raise BusinessRuleError("Employee is not assigned to this office.")
    current = PermissionGrant.objects.filter(employee=employee, office=office)
    before = sorted(current.values_list("code", flat=True))
    current.exclude(code__in=codes).delete()
    for code in codes:
        PermissionGrant.objects.get_or_create(employee=employee, office=office, code=code, defaults={"granted_by": actor})
    audit.log(
        actor=actor, action="permissions.changed", entity=employee, office=office,
        before={"permissions": before}, after={"permissions": sorted(codes)}, request=request,
    )


def employee_offices(employee):
    return set(OfficeAssignment.objects.filter(employee=employee, is_active=True).values_list("office_id", flat=True))


def can_manage_employee(actor, employee) -> bool:
    if actor.is_owner:
        return True
    if employee.role == Role.OWNER or employee.pk == actor.pk:
        return False
    scope = office_scope(actor, "employees.manage")
    offices = employee_offices(employee)
    return bool(offices) and all(o in scope for o in offices)


@transaction.atomic
def disable_employee(*, actor, employee, reason, reassign_to=None, request=None):
    if employee.pk == actor.pk:
        raise BusinessRuleError("You cannot disable your own account.")
    if not reason:
        raise BusinessRuleError("A reason is required.")
    if reassign_to is not None and (not reassign_to.is_active or reassign_to.pk == employee.pk):
        raise BusinessRuleError("Choose another active employee to take over the pending work.")
    if employee.role == Role.OWNER and Employee.objects.filter(role=Role.OWNER, is_active=True).exclude(pk=employee.pk).count() == 0:
        raise BusinessRuleError("Cannot disable the last active owner.")
    employee.is_active = False
    employee.disabled_at = timezone.now()
    employee.disabled_by = actor
    employee.save(update_fields=["is_active", "disabled_at", "disabled_by"])
    OfficeAssignment.objects.filter(employee=employee).update(is_active=False)
    revoked = revoke_user_sessions(employee)
    audit.log(actor=actor, action="employee.disabled", entity=employee, reason=reason, after={"sessions_revoked": revoked}, request=request)
    # Later modules (complaints, tasks) hook in here to reassign pending work.
    from .signals import employee_disabled

    employee_disabled.send(sender=Employee, employee=employee, actor=actor, reassign_to=reassign_to)
    return employee


@transaction.atomic
def reset_password(*, actor, employee, request=None):
    """Staff-assisted reset: returns a one-time temporary password, forces a change."""
    temp = secrets.token_urlsafe(12)
    employee.set_password(temp)
    employee.must_change_password = True
    employee.failed_login_count, employee.locked_until = 0, None
    employee.save(update_fields=["password", "must_change_password", "failed_login_count", "locked_until"])
    revoke_user_sessions(employee)
    audit.log(actor=actor, action="employee.password_reset", entity=employee, request=request)
    return temp


def employees_with_permission(code, office=None):
    """Active employees who hold `code` in `office` (owners always qualify). Used to route notifications."""
    from django.db.models import Q

    q = Q(role=Role.OWNER)
    if office is not None and code not in GLOBAL_PERMISSIONS:
        q |= Q(permission_grants__code=code, permission_grants__office=office,
               assignments__office=office, assignments__is_active=True)
    elif code in GLOBAL_PERMISSIONS:
        q |= Q(permission_grants__code=code, permission_grants__office__isnull=True)
    return Employee.objects.filter(q, is_active=True).distinct()
