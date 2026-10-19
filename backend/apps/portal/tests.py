from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing import services as bs
from apps.billing.tests import new_customer
from apps.core.testing import PASSWORD, make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.customers.models import Connection
from apps.operations import services as ops
from apps.operations.models import Complaint
from apps.portal import services as ps

PW = "Portal-Passw0rd-123"


class PortalBase(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.pkg = make_package()
        self.c1 = new_customer(self.staff, self.va, self.a)
        self.c2 = new_customer(self.staff, self.va, self.a)
        self.conn1 = cs.create_connection(actor=self.staff, customer=self.c1, package=self.pkg, installation_address="Home 1")
        self.conn2 = cs.create_connection(actor=self.staff, customer=self.c2, package=self.pkg, installation_address="Home 2")
        self.acc1 = ps.create_account(actor=self.staff, customer=self.c1, username="ali.khan", password=PW)
        self.acc2 = ps.create_account(actor=self.staff, customer=self.c2, username="sara.bibi", password=PW)

    def portal(self, username="ali.khan", password=PW):
        c = APIClient()
        r = c.post("/api/v1/portal/auth/login/", {"username": username, "password": password}, format="json")
        assert r.status_code == 200, r.content
        return c

    def paid_invoice(self, conn):
        inv = bs.create_invoice(actor=self.staff, connection=conn)[0]
        pay = bs.record_payment(actor=self.staff, invoice=inv, method="cash", amount=inv.total_amount)
        return inv, pay


class AccountTests(PortalBase):
    def test_staff_create_accounts_with_scope_and_validation(self):
        c3 = new_customer(self.staff, self.va, self.a)
        s = APIClient(); login(s, self.staff)
        self.assertEqual(s.post("/api/v1/portal-accounts/", {"customer": c3.customer_id, "username": "x", "password": PW}, format="json").status_code, 400)
        self.assertEqual(s.post("/api/v1/portal-accounts/", {"customer": c3.customer_id, "username": "newuser", "password": "123"}, format="json").status_code, 400)
        r = s.post("/api/v1/portal-accounts/", {"customer": c3.customer_id, "username": "NewUser", "password": PW}, format="json")
        self.assertEqual((r.status_code, r.json()["username"], r.json()["must_change_password"]), (201, "newuser", True))
        self.assertNotIn("password", r.json())
        self.assertEqual(s.post("/api/v1/portal-accounts/", {"customer": c3.customer_id, "username": "other", "password": PW}, format="json").status_code, 409)  # one per customer
        b = APIClient(); login(b, self.staff_b)
        self.assertEqual(b.post("/api/v1/portal-accounts/", {"customer": self.c1.customer_id, "username": "intruder", "password": PW}, format="json").status_code, 404)
        self.assertEqual(b.get("/api/v1/portal-accounts/").json()["count"], 0)
        t = APIClient(); login(t, self.tech)
        self.assertEqual(t.get("/api/v1/portal-accounts/").status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action="portal.account_created").exists())

    def test_login_logout_and_generic_failure(self):
        c = APIClient()
        bad1 = c.post("/api/v1/portal/auth/login/", {"username": "ghost", "password": "x"}, format="json")
        bad2 = c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": "x"}, format="json")
        self.assertEqual((bad1.status_code, bad1.json()), (bad2.status_code, bad2.json()))
        p = self.portal()
        me = p.get("/api/v1/portal/auth/me/").json()
        self.assertEqual((me["customer_id"], me["must_change_password"]), (self.c1.customer_id, True))
        self.assertEqual(p.post("/api/v1/portal/auth/logout/").status_code, 204)
        self.assertEqual(p.get("/api/v1/portal/auth/me/").status_code, 401)

    @override_settings(LOGIN_MAX_FAILURES=3)
    def test_lockout_and_disable(self):
        c = APIClient()
        for _ in range(3):
            c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": "wrong"}, format="json")
        self.assertEqual(c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": PW}, format="json").status_code, 401)
        ps.reset_password(actor=self.staff, account=self.acc1)   # staff reset clears the lock
        self.acc1.refresh_from_db()
        self.assertIsNone(self.acc1.locked_until)
        p = self.portal("sara.bibi")
        ps.set_active(actor=self.staff, account=self.acc2, active=False)
        self.assertEqual(p.get("/api/v1/portal/auth/me/").status_code, 401)            # live sessions die immediately
        self.assertEqual(APIClient().post("/api/v1/portal/auth/login/", {"username": "sara.bibi", "password": PW}, format="json").status_code, 401)

    def test_staff_reset_gives_temporary_password_and_kills_sessions(self):
        p = self.portal()
        s = APIClient(); login(s, self.staff)
        r = s.post(f"/api/v1/portal-accounts/{self.acc1.pk}/reset-password/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(p.get("/api/v1/portal/auth/me/").status_code, 401)
        p2 = self.portal(password=r.json()["temporary_password"])
        self.assertTrue(p2.get("/api/v1/portal/auth/me/").json()["must_change_password"])
        self.assertEqual(p2.post("/api/v1/portal/auth/change-password/", {"old_password": "wrong", "new_password": "Another-Passw0rd-9"}, format="json").status_code, 400)
        self.assertEqual(p2.post("/api/v1/portal/auth/change-password/", {"old_password": r.json()["temporary_password"], "new_password": "123"}, format="json").status_code, 400)
        self.assertEqual(p2.post("/api/v1/portal/auth/change-password/", {"old_password": r.json()["temporary_password"], "new_password": "Another-Passw0rd-9"}, format="json").status_code, 204)
        self.assertFalse(p2.get("/api/v1/portal/auth/me/").json()["must_change_password"])

    def test_csrf_is_enforced_for_portal_writes(self):
        c = APIClient(enforce_csrf_checks=True)
        token = c.get("/api/v1/auth/csrf/").json()["csrfToken"]
        self.assertEqual(c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": PW}, format="json").status_code, 403)
        ok = c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": PW}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(ok.status_code, 200)
        token = c.cookies["csrftoken"].value
        body = {"category": "other", "description": "test"}
        self.assertEqual(c.post("/api/v1/portal/complaints/", body, format="json").status_code, 403)       # session alone is not enough
        self.assertEqual(c.post("/api/v1/portal/complaints/", body, format="json", HTTP_X_CSRFTOKEN=token).status_code, 201)


class IsolationTests(PortalBase):
    def test_customers_see_only_their_own_records(self):
        i1, p1 = self.paid_invoice(self.conn1)
        i2, p2 = self.paid_invoice(self.conn2)
        c = self.portal()
        self.assertEqual([x["connection_id"] for x in c.get("/api/v1/portal/connections/").json()], [self.conn1.connection_id])
        self.assertEqual([x["number"] for x in c.get("/api/v1/portal/invoices/").json()], [i1.number])
        self.assertEqual(len(c.get("/api/v1/portal/payments/").json()), 1)
        self.assertEqual([x["number"] for x in c.get("/api/v1/portal/receipts/").json()], [p1.receipt.number])
        for url in (f"/api/v1/portal/connections/{self.conn2.connection_id}/", f"/api/v1/portal/invoices/{i2.number}/",
                    f"/api/v1/portal/invoices/{i2.number}/pdf/", f"/api/v1/portal/receipts/{p2.receipt.number}/",
                    f"/api/v1/portal/receipts/{p2.receipt.number}/pdf/"):
            self.assertEqual(c.get(url).status_code, 404, url)       # guessing another customer's number reveals nothing
        for url in (f"/api/v1/portal/invoices/{i1.number}/pdf/", f"/api/v1/portal/receipts/{p1.receipt.number}/pdf/"):
            r = c.get(url)
            self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
            self.assertTrue(r.content.startswith(b"%PDF"))

    def test_portal_and_staff_identities_never_cross(self):
        p = self.portal()
        for url in ("/api/v1/customers/", "/api/v1/invoices/", "/api/v1/connections/", "/api/v1/auth/me/", "/api/v1/employees/",
                    "/api/v1/notifications/", "/api/v1/portal-accounts/", "/api/v1/audit/"):
            self.assertIn(p.get(url).status_code, (403, 404), url)
        s = APIClient(); login(s, self.staff)
        for url in ("/api/v1/portal/connections/", "/api/v1/portal/auth/me/", "/api/v1/portal/invoices/"):
            self.assertEqual(s.get(url).status_code, 401, url)

    def test_one_identity_per_browser_session(self):
        c = APIClient()
        c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": PW}, format="json")
        self.assertEqual(c.get("/api/v1/portal/auth/me/").status_code, 200)
        c.post("/api/v1/auth/login/", {"username": self.staff.username, "password": PASSWORD}, format="json")   # staff signs in on the same browser
        self.assertEqual(c.get("/api/v1/portal/auth/me/").status_code, 401)
        self.assertEqual(c.get("/api/v1/auth/me/").status_code, 200)
        c.post("/api/v1/portal/auth/login/", {"username": "ali.khan", "password": PW}, format="json")
        self.assertEqual(c.get("/api/v1/auth/me/").status_code, 403)

    def test_void_invoices_are_hidden_and_nothing_is_writable(self):
        inv = bs.create_invoice(actor=self.staff, connection=self.conn1)[0]
        c = self.portal()
        self.assertEqual(len(c.get("/api/v1/portal/invoices/").json()), 1)
        bs.void_invoice(actor=self.mgr, invoice=inv, reason="reissue")
        self.assertEqual(c.get("/api/v1/portal/invoices/").json(), [])
        for method, url in (("post", "/api/v1/portal/invoices/"), ("post", "/api/v1/portal/payments/"), ("patch", f"/api/v1/portal/connections/{self.conn1.connection_id}/"),
                            ("delete", f"/api/v1/portal/connections/{self.conn1.connection_id}/")):
            self.assertIn(getattr(c, method)(url, {}, format="json").status_code, (403, 404, 405), url)


class ChargesAndStatusTests(PortalBase):
    def test_pending_connection_message_then_invoice_amount(self):
        c = self.portal()
        d = c.get("/api/v1/portal/connections/").json()[0]
        self.assertEqual((d["status"], d["charges"]["amount_due"]), ("pending_installation", None))
        inv = bs.create_invoice(actor=self.staff, connection=self.conn1)[0]
        ch = c.get(f"/api/v1/portal/connections/{self.conn1.connection_id}/").json()["charges"]
        self.assertEqual((ch["amount_due"], ch["source"], ch["invoice"]), ("1500.00", "open_invoice", inv.number))

    def test_expired_customer_sees_status_expiry_charges_and_instructions(self):
        Connection.objects.filter(pk=self.conn1.pk).update(status="expired", expires_at=timezone.now() - timedelta(days=3),
                                                           installation_completed_at=timezone.now(), initial_payment_confirmed_at=timezone.now())
        d = self.portal().get(f"/api/v1/portal/connections/{self.conn1.connection_id}/").json()
        self.assertEqual(d["status"], "expired")
        self.assertIsNotNone(d["expires_at"])
        self.assertEqual((d["charges"]["amount_due"], d["charges"]["source"]), ("1500.00", "renewal_price"))
        self.assertIn("contact your office", d["charges"]["instructions"])

    def test_scheduled_package_change_shows_next_price_and_cancelled_has_no_charge(self):
        p20 = make_package("P20", "2500.00", 20)
        Connection.objects.filter(pk=self.conn1.pk).update(status="active", expires_at=timezone.now() + timedelta(days=5),
                                                           installation_completed_at=timezone.now(), initial_payment_confirmed_at=timezone.now())
        cs.schedule_package_change(actor=self.mgr, connection=Connection.objects.get(pk=self.conn1.pk), package=p20)
        d = self.portal().get(f"/api/v1/portal/connections/{self.conn1.connection_id}/").json()
        self.assertEqual((d["charges"]["amount_due"], d["next_package_name"]), ("2500.00", "20 Mbps"))
        cs.cancel_connection(actor=self.mgr, connection=Connection.objects.get(pk=self.conn1.pk), reason="x")
        self.assertIsNone(self.portal().get(f"/api/v1/portal/connections/{self.conn1.connection_id}/").json()["charges"]["amount_due"])


class PortalComplaintTests(PortalBase):
    def test_customer_submits_and_follows_complaint_without_seeing_internals(self):
        c = self.portal()
        r = c.post("/api/v1/portal/complaints/", {"connection": self.conn1.connection_id, "category": "internet_down", "description": "No internet"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        n = r.json()["number"]
        cmp = Complaint.objects.get(number=n)
        self.assertEqual((cmp.source, cmp.customer, cmp.created_by), ("portal", self.c1, None))
        from apps.notifications.models import Notification
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="complaint.new").exists())     # staff are told
        ops.assign(actor=self.staff, complaint=cmp, technician=self.tech)
        ops.start_work(actor=self.tech, complaint=cmp)
        ops.add_work_log(actor=self.tech, complaint=cmp, notes_text="INTERNAL: suspect bad splice, tech Imran", materials_used="secret stock note")
        ops.resolve(actor=self.tech, complaint=cmp, resolution_notes="Replaced a damaged cable")
        d = c.get(f"/api/v1/portal/complaints/{n}/").json()
        self.assertEqual((d["status"], d["resolution_notes"]), ("resolved", "Replaced a damaged cable"))
        self.assertEqual([u["status"] for u in d["updates"]], ["new", "in_progress", "resolved"])   # the assignment event names the technician, so it stays internal
        text = str(d)
        for hidden in ("INTERNAL", "Imran", "secret stock", self.tech.username, "work_logs", "assigned_to"):
            self.assertNotIn(hidden, text)

    def test_only_own_connections_and_complaints(self):
        c = self.portal()
        self.assertEqual(c.post("/api/v1/portal/complaints/", {"connection": self.conn2.connection_id, "category": "other", "description": "x"}, format="json").status_code, 404)
        other = ops.create_complaint(actor=self.staff, customer=self.c2, category="other", description="theirs")
        self.assertEqual(c.get(f"/api/v1/portal/complaints/{other.number}/").status_code, 404)
        self.assertEqual(c.get("/api/v1/portal/complaints/").json(), [])
        self.assertEqual(c.post("/api/v1/portal/complaints/", {"category": "nonsense", "description": "x"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/v1/portal/complaints/", {"category": "other", "description": ""}, format="json").status_code, 400)
