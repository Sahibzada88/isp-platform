from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import PASSWORD, login
from apps.audit.models import AuditLog
from apps.billing.tests import new_customer
from apps.core.testing import make_office, make_user, make_village
from apps.organization.models import Office, Village


class OfficeVillageSetupTests(TestCase):
    """First-time setup: the owner creates offices and villages, then employees can be assigned to them."""

    def setUp(self):
        self.owner = make_user(Role.OWNER)
        self.o = APIClient(); login(self.o, self.owner)

    def test_owner_sets_up_office_village_and_then_an_employee_through_the_api(self):
        r = self.o.post("/api/v1/offices/", {"code": "KOT", "name": "Kotha Office", "is_head_office": True}, format="json")
        self.assertEqual((r.status_code, r.json()["code"], r.json()["is_head_office"]), (201, "KOT", True))
        office_id = r.json()["id"]
        v = self.o.post("/api/v1/villages/", {"code": "KHT", "name": "Kotha", "office": office_id}, format="json")
        self.assertEqual((v.status_code, v.json()["code"]), (201, "KHT"))
        e = self.o.post("/api/v1/employees/", {"username": "ali.staff", "password": PASSWORD, "role": "office_staff", "offices": [office_id]}, format="json")
        self.assertEqual(e.status_code, 201, e.content)
        staff = APIClient()
        self.assertEqual(staff.post("/api/v1/auth/login/", {"username": "ali.staff", "password": PASSWORD}, format="json").status_code, 200)
        c = staff.post("/api/v1/customers/", {"full_name": "Ali", "phone": "0300", "permanent_address": "x", "village": v.json()["id"]}, format="json")
        self.assertEqual((c.status_code, c.json()["customer_id"]), (201, "KHT001"))        # the whole chain works
        self.assertTrue(AuditLog.objects.filter(action="office.created").exists())
        self.assertTrue(AuditLog.objects.filter(action="village.created").exists())

    def test_validation_and_duplicates(self):
        self.assertEqual(self.o.post("/api/v1/offices/", {"code": "kot", "name": "x"}, format="json").status_code, 400)       # must be upper-case
        self.assertEqual(self.o.post("/api/v1/offices/", {"code": "K", "name": "x"}, format="json").status_code, 400)         # too short
        self.assertEqual(self.o.post("/api/v1/offices/", {"code": "KOT", "name": ""}, format="json").status_code, 400)
        self.o.post("/api/v1/offices/", {"code": "KOT", "name": "A"}, format="json")
        self.assertEqual(self.o.post("/api/v1/offices/", {"code": "KOT", "name": "B"}, format="json").status_code, 400)       # duplicate code
        oid = Office.objects.get(code="KOT").pk
        self.assertEqual(self.o.post("/api/v1/villages/", {"code": "TOOLONGX", "name": "x", "office": oid}, format="json").status_code, 400)

    def test_only_authorised_people_change_the_structure_and_scope_applies(self):
        a, b = make_office("AAA"), make_office("BBB")
        make_village(a, "VAA"); make_village(b, "VBB")
        mgr = make_user(Role.MANAGER, [a]); m = APIClient(); login(m, mgr)
        self.assertEqual(m.post("/api/v1/offices/", {"code": "NEW", "name": "x"}, format="json").status_code, 403)
        self.assertEqual(m.post("/api/v1/villages/", {"code": "NEW", "name": "x", "office": a.pk}, format="json").status_code, 403)
        self.assertEqual(m.patch(f"/api/v1/offices/{a.pk}/", {"name": "Renamed"}, format="json").status_code, 403)
        self.assertEqual([x["code"] for x in m.get("/api/v1/offices/").json()["results"]], ["AAA"])           # managers see only their office
        self.assertEqual([x["code"] for x in m.get("/api/v1/villages/").json()["results"]], ["VAA"])
        self.assertEqual(sorted(x["code"] for x in self.o.get("/api/v1/offices/").json()["results"]), ["AAA", "BBB"])
        self.assertEqual(APIClient().get("/api/v1/offices/").status_code, 403)

    def test_edit_and_deactivate_and_village_code_freezes_once_customers_exist(self):
        office = make_office("AAA"); village = make_village(office, "VAA", "Old name")
        r = self.o.patch(f"/api/v1/villages/{village.pk}/", {"name": "New name", "code": "VZZ"}, format="json")
        self.assertEqual((r.status_code, r.json()["code"], r.json()["name"]), (200, "VZZ", "New name"))     # allowed while empty
        staff = make_user(Role.OFFICE_STAFF, [office])
        new_customer(staff, Village.objects.get(pk=village.pk), office)
        r = self.o.patch(f"/api/v1/villages/{village.pk}/", {"code": "QQQ"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("cannot change", str(r.json()))
        self.assertEqual(self.o.patch(f"/api/v1/villages/{village.pk}/", {"is_active": False}, format="json").status_code, 200)
        self.assertEqual(self.o.patch(f"/api/v1/offices/{office.pk}/", {"name": "Main", "is_active": False}, format="json").json()["is_active"], False)
        self.assertIn(self.o.delete(f"/api/v1/offices/{office.pk}/").status_code, (403, 405))                    # offices can be deactivated, never deleted
        self.assertTrue(Office.objects.filter(pk=office.pk).exists())
