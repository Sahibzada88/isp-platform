from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import PermissionGrant, Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.core.testing import make_office, make_package, make_user
from apps.packages.models import Package

BODY = {"code": "P50", "name": "50 Mbps", "speed_mbps": 50, "price_pkr": "4000.00"}


class PackageTests(TestCase):
    def setUp(self):
        self.office = make_office("AAA")
        self.owner = make_user(Role.OWNER)
        self.mgr = make_user(Role.MANAGER, [self.office])
        self.co = APIClient(); login(self.co, self.owner)
        self.cm = APIClient(); login(self.cm, self.mgr)

    def test_manager_needs_explicit_permissions(self):
        self.assertEqual(self.cm.post("/api/v1/packages/", BODY, format="json").status_code, 403)
        PermissionGrant.objects.create(employee=self.mgr, office=self.office, code="packages.manage")
        self.assertEqual(self.cm.post("/api/v1/packages/", BODY, format="json").status_code, 403)  # still no price right
        PermissionGrant.objects.create(employee=self.mgr, office=self.office, code="packages.change_price")
        self.assertEqual(self.cm.post("/api/v1/packages/", BODY, format="json").status_code, 201)

    def test_price_change_is_audited_and_validity_fixed(self):
        pkg = self.co.post("/api/v1/packages/", {**BODY, "validity_days": 90}, format="json").json()
        self.assertEqual(pkg["validity_days"], 30)
        self.co.patch(f"/api/v1/packages/{pkg['id']}/", {"price_pkr": "4500.00"}, format="json")
        log = AuditLog.objects.get(action="package.updated")
        self.assertEqual((log.before["price_pkr"], log.after["price_pkr"]), ("4000.00", "4500.00"))

    def test_name_edit_allowed_without_price_right_but_price_blocked(self):
        pkg = make_package("PX")
        PermissionGrant.objects.create(employee=self.mgr, office=self.office, code="packages.manage")
        url = f"/api/v1/packages/{pkg.pk}/"
        self.assertEqual(self.cm.patch(url, {"name": "Renamed"}, format="json").status_code, 200)
        self.assertEqual(self.cm.patch(url, {"price_pkr": "1.00"}, format="json").status_code, 403)

    def test_deactivate_not_delete(self):
        pkg = make_package("PX")
        self.assertIn(self.co.delete(f"/api/v1/packages/{pkg.pk}/").status_code, (403, 405))
        with self.assertRaises(ProtectedError):
            pkg.delete()
        r = self.co.post(f"/api/v1/packages/{pkg.pk}/deactivate/")
        self.assertFalse(r.json()["is_active"])
        self.assertEqual(self.co.get("/api/v1/packages/?active=true").json()["count"], 0)

    def test_database_constraints(self):
        for kwargs in ({"price_pkr": Decimal("0")}, {"speed_mbps": 0}, {"validity_days": 45}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                fields = {"code": "BAD", "name": "bad", "speed_mbps": 5, "price_pkr": Decimal("100"), **kwargs}
                Package.objects.create(**fields)

    def test_invalid_input_rejected(self):
        self.assertEqual(self.co.post("/api/v1/packages/", {**BODY, "price_pkr": "-5"}, format="json").status_code, 400)
        self.assertEqual(self.co.post("/api/v1/packages/", {**BODY, "speed_mbps": 0}, format="json").status_code, 400)
        self.co.post("/api/v1/packages/", BODY, format="json")
        self.assertEqual(self.co.post("/api/v1/packages/", BODY, format="json").status_code, 400)  # duplicate code
