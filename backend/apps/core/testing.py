"""Test factories (used only by automated tests; never imported in production code)."""
from decimal import Decimal
from itertools import count

from apps.accounts import services as acl
from apps.accounts.models import Employee, Role
from apps.organization.models import Office, Village
from apps.packages.models import Package

_n = count(1)
PASSWORD = "Str0ng-Test-Passw0rd!"


def make_office(code, name=None):
    return Office.objects.create(code=code, name=name or f"Office {code}")


def make_village(office, code, name=None):
    return Village.objects.create(code=code, name=name or f"Village {code}", office=office)


def make_user(role=Role.OFFICE_STAFF, offices=(), username=None, extra_perms=(), owner_actor=None):
    username = username or f"user{next(_n)}"
    if role == Role.OWNER:
        return Employee.objects.create_user(username=username, password=PASSWORD, role=Role.OWNER, email=f"{username}@x.pk")
    user = Employee.objects.create_user(username=username, password=PASSWORD, role=role, email=f"{username}@x.pk")
    for office in offices:
        acl.assign_office(actor=None, employee=user, office=office)
        if extra_perms:
            from apps.accounts.models import PermissionGrant
            for code in extra_perms:
                PermissionGrant.objects.get_or_create(employee=user, office=office, code=code)
    return user


def make_package(code="P10", price="1500.00", speed=10, active=True):
    return Package.objects.create(code=code, name=f"{speed} Mbps", speed_mbps=speed, price_pkr=Decimal(price), is_active=active)
