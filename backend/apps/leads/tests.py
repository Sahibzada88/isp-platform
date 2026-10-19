from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.core.exceptions import BusinessRuleError, DuplicateWarning
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers.models import Connection, Customer
from apps.leads import services as ls
from apps.leads.models import Lead


class LeadTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.pkg = make_package()

    def api(self, u):
        c = APIClient(); login(c, u); return c

    def lead(self, **kw):
        d = dict(actor=self.staff, office=self.a, name="Bilal", phone="03111111111", source="phone", interested_package=self.pkg)
        d.update(kw)
        return ls.create_lead(**d)

    def convert_body(self, lead, **kw):
        return {"village": self.va.pk, "package": self.pkg.pk, "installation_address": "Shop 3", "permanent_address": "House 9", **kw}

    def test_conversion_creates_customer_and_connection_with_fresh_ids(self):
        lead = self.lead()
        self.assertEqual(Customer.objects.count(), 0)    # no customer ID is consumed by a lead
        r = self.api(self.staff).post(f"/api/v1/leads/{lead.pk}/convert/", self.convert_body(lead, cnic="3520212345671"), format="json")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d["status"], d["customer_id"], d["connection_id"]), ("converted", "KHT001", "CON-00001"))
        c = Customer.objects.get()
        self.assertEqual((c.full_name, c.phone, c.cnic), ("Bilal", "03111111111", "35202-1234567-1"))
        self.assertEqual(Connection.objects.get().status, "pending_installation")
        self.assertTrue(AuditLog.objects.filter(action="lead.converted").exists())
        self.assertEqual(self.api(self.staff).post(f"/api/v1/leads/{lead.pk}/convert/", self.convert_body(lead), format="json").status_code, 400)  # only once

    def test_duplicate_customer_warns_and_failed_conversion_rolls_back(self):
        existing = self.api(self.staff).post("/api/v1/leads/", {"office": self.a.pk, "name": "Old", "phone": "03111111111"}, format="json")
        first = self.lead(name="Other", phone="03222222222")
        self.api(self.staff).post(f"/api/v1/leads/{first.pk}/convert/", self.convert_body(first), format="json")
        dup = self.lead(name="Third", phone="03222222222")
        r = self.api(self.staff).post(f"/api/v1/leads/{dup.pk}/convert/", self.convert_body(dup), format="json")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "possible_duplicate"))
        dup.refresh_from_db()
        self.assertEqual((dup.status, Customer.objects.count(), Connection.objects.count()), ("new", 1, 1))   # nothing half-done
        ok = self.api(self.staff).post(f"/api/v1/leads/{dup.pk}/convert/", self.convert_body(dup, confirm_duplicates=True, override_reason="brother's shop"), format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(Customer.objects.count(), 2)

    def test_existing_customer_gets_another_connection_not_a_duplicate_record(self):
        first = self.lead()
        self.api(self.staff).post(f"/api/v1/leads/{first.pk}/convert/", self.convert_body(first), format="json")
        again = self.lead(name="Bilal (shop)")
        r = self.api(self.staff).post(f"/api/v1/leads/{again.pk}/convert/", self.convert_body(again, existing_customer="KHT001"), format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((Customer.objects.count(), Connection.objects.filter(customer__customer_id="KHT001").count()), (1, 2))

    def test_lost_leads_are_kept_and_final(self):
        lead = self.lead()
        with self.assertRaises(BusinessRuleError):
            ls.mark_lost(actor=self.staff, lead=lead, reason="")
        ls.mark_lost(actor=self.staff, lead=lead, reason="chose another ISP")
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.lost_reason), ("lost", "chose another ISP"))
        for fn in (lambda: ls.update_lead(actor=self.staff, lead=lead, changes={"notes": "x"}), lambda: ls.mark_lost(actor=self.staff, lead=lead, reason="x")):
            with self.assertRaises(BusinessRuleError):
                fn()
        self.assertEqual(self.api(self.staff).get("/api/v1/leads/?status=lost").json()["count"], 1)

    def test_follow_up_notes_status_and_assignment(self):
        lead = self.lead()
        c = self.api(self.staff)
        self.assertEqual(c.patch(f"/api/v1/leads/{lead.pk}/", {"status": "contacted"}, format="json").json()["status"], "contacted")
        self.assertEqual(c.patch(f"/api/v1/leads/{lead.pk}/", {"status": "converted"}, format="json").status_code, 400)   # via convert only
        self.assertEqual(c.post(f"/api/v1/leads/{lead.pk}/note/", {"note": "called, wants 20 Mbps"}, format="json").json()["follow_ups"][0]["note"], "called, wants 20 Mbps")
        self.assertEqual(c.post(f"/api/v1/leads/{lead.pk}/note/", {"note": ""}, format="json").status_code, 400)
        self.assertEqual(c.patch(f"/api/v1/leads/{lead.pk}/", {"assigned_to": self.tech.pk}, format="json").status_code, 404)  # technicians don't work leads
        self.assertEqual(c.patch(f"/api/v1/leads/{lead.pk}/", {"assigned_to": self.staff.pk}, format="json").json()["assigned_to"], self.staff.pk)

    def test_scope_and_permissions(self):
        lead = self.lead()
        b = self.api(self.staff_b)
        self.assertEqual(b.get("/api/v1/leads/").json()["count"], 0)
        self.assertEqual(b.get(f"/api/v1/leads/{lead.pk}/").status_code, 404)
        self.assertEqual(b.post(f"/api/v1/leads/{lead.pk}/convert/", self.convert_body(lead), format="json").status_code, 404)
        self.assertEqual(b.post("/api/v1/leads/", {"office": self.a.pk, "name": "x", "phone": "1"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.tech).get("/api/v1/leads/").status_code, 403)
