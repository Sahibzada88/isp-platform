from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing import services as bs
from apps.billing.tests import new_customer
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.customers import transfers as tr
from apps.customers.models import Connection, Customer
from apps.imports.models import OpeningBalance
from apps.inventory import services as inv
from apps.notifications.models import Notification
from apps.operations import services as ops
from apps.operations.models import Complaint, Task


class TransferBase(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.va2 = make_village(self.a, "KH2", "Kotha 2")
        self.owner = make_user(Role.OWNER)
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr_a = make_user(Role.MANAGER, [self.a])
        self.mgr_b = make_user(Role.MANAGER, [self.b])
        self.tech_b = make_user(Role.TECHNICIAN, [self.b])
        self.pkg = make_package()
        self.cust = new_customer(self.staff, self.va, self.a)
        self.conn = cs.create_connection(actor=self.staff, customer=self.cust, package=self.pkg, installation_address="Old house, Kotha")

    def api(self, u):
        c = APIClient(); login(c, u); return c

    def request(self, **kw):
        d = dict(actor=self.staff, connection=self.conn, to_village=self.vb, new_address="New shop, Badar", reason="family moved")
        d.update(kw)
        return tr.request_office_transfer(**d)


class OfficeTransferTests(TransferBase):
    def test_approved_transfer_moves_connection_keeps_ids_history_and_creates_task(self):
        t = self.request(move_customer=True)
        self.assertTrue(Notification.objects.filter(recipient=self.mgr_b, kind="transfer.requested").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.staff, kind="transfer.requested").exists())
        cid, kid = self.cust.customer_id, self.conn.connection_id
        tr.approve_office_transfer(actor=self.mgr_b, transfer=t, note="ok")
        self.conn.refresh_from_db(); self.cust.refresh_from_db(); t.refresh_from_db()
        self.assertEqual((self.conn.office, self.conn.village, self.conn.installation_address), (self.b, self.vb, "New shop, Badar"))
        self.assertEqual((self.cust.office, self.cust.village), (self.b, self.vb))
        self.assertEqual((self.cust.customer_id, self.conn.connection_id), (cid, kid))                  # IDs never change
        h = self.conn.address_history.get()
        self.assertEqual((h.old_address, h.new_address), ("Old house, Kotha", "New shop, Badar"))
        self.assertEqual((t.status, t.relocation_task.office, t.relocation_task.kind, t.relocation_task.connection), ("approved", self.b, "installation", self.conn))
        self.assertTrue(AuditLog.objects.filter(action="connection.office_changed").exists())
        # future work belongs to the new office
        inv_ = bs.create_invoice(actor=self.mgr_b, connection=self.conn)[0]
        self.assertTrue(inv_.number.startswith("BBB-INV-"))
        self.assertEqual(inv_.office, self.b)

    def test_customer_record_stays_put_unless_asked(self):
        t = self.request()
        tr.approve_office_transfer(actor=self.mgr_b, transfer=t)
        self.cust.refresh_from_db(); self.assertEqual(self.cust.office, self.a)

    def test_rules_around_requesting_and_approving(self):
        with self.assertRaises(BusinessRuleError):
            self.request(to_village=self.va)                                  # same village and office
        with self.assertRaises(BusinessRuleError):
            self.request(reason="")
        t = self.request()
        with self.assertRaises(ConflictError):
            self.request()                                                    # one pending request per connection
        with self.assertRaises(BusinessRuleError):
            tr.approve_office_transfer(actor=self.staff, transfer=t)         # requester cannot approve
        with self.assertRaises(BusinessRuleError):
            tr.approve_office_transfer(actor=self.mgr_a, transfer=t)         # origin manager has no rights in the destination office
        tr.approve_office_transfer(actor=self.owner, transfer=t)
        with self.assertRaises(BusinessRuleError):
            tr.approve_office_transfer(actor=self.owner, transfer=t)         # already decided
        t2 = self.request(connection=Connection.objects.get(pk=self.conn.pk), to_village=self.va2, actor=self.mgr_b)   # same office, new village is fine too
        self.assertEqual(t2.from_office, self.b)

    def test_reject_keeps_everything(self):
        t = self.request()
        with self.assertRaises(BusinessRuleError):
            tr.reject_office_transfer(actor=self.mgr_b, transfer=t, note="")
        tr.reject_office_transfer(actor=self.mgr_b, transfer=t, note="no capacity in that village")
        self.conn.refresh_from_db(); t.refresh_from_db()
        self.assertEqual((self.conn.office, t.status, t.decision_note), (self.a, "rejected", "no capacity in that village"))
        self.assertTrue(Notification.objects.filter(recipient=self.staff, kind="transfer.decided").exists())
        self.request()                                                        # a new request is allowed after rejection

    def test_unpaid_invoice_blocks_until_settled_and_cancelled_connection_is_refused(self):
        invoice = bs.create_invoice(actor=self.staff, connection=self.conn)[0]
        t = self.request()
        with self.assertRaises(BusinessRuleError) as cm:
            tr.approve_office_transfer(actor=self.mgr_b, transfer=t)
        self.assertIn("unpaid invoice", str(cm.exception.detail))
        bs.void_invoice(actor=self.mgr_a, invoice=invoice, reason="re-issue later")
        tr.approve_office_transfer(actor=self.mgr_b, transfer=t)
        c2 = cs.create_connection(actor=self.staff, customer=self.cust, package=self.pkg, installation_address="x")
        cs.cancel_connection(actor=self.mgr_a, connection=c2, reason="x")
        with self.assertRaises(BusinessRuleError):
            self.request(connection=Connection.objects.get(pk=c2.pk))

    def test_open_work_and_devices_follow_the_connection(self):
        tech_a = make_user(Role.TECHNICIAN, [self.a])
        cmp_ = ops.create_complaint(actor=self.staff, customer=self.cust, connection=self.conn, category="other", description="x")
        ops.assign(actor=self.staff, complaint=cmp_, technician=tech_a)
        task = ops.create_task(actor=self.mgr_a, office=self.a, title="check line", assigned_to=tech_a, connection=self.conn)
        item = inv.create_item(actor=self.mgr_a, office=self.a, name="ONU", category="router_onu", is_serialized=True)
        inv.record_purchase(actor=self.mgr_a, item=item, quantity="1", unit_price="1", purchase_date=timezone.localdate(), serials=["SN9"])
        from apps.inventory.models import Equipment
        eq = Equipment.objects.get()
        inv.assign_equipment(actor=tech_a, equipment=eq, connection=self.conn)
        t = self.request()
        tr.approve_office_transfer(actor=self.mgr_b, transfer=t)
        for o in (cmp_, task, eq):
            o.refresh_from_db()
        self.assertEqual((cmp_.office, cmp_.assigned_to, cmp_.status), (self.b, None, "new"))
        self.assertEqual((task.office, task.assigned_to), (self.b, None))
        self.assertEqual(eq.office, self.b)
        self.assertTrue(cmp_.events.filter(kind="unassigned").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.mgr_b, kind="complaint.unassigned").exists())
        self.assertEqual(Task.objects.filter(office=self.b, kind="installation").count(), 1)   # exactly the relocation task

    def test_http_flow_scope_and_permissions(self):
        s, mb, ma, tb = self.api(self.staff), self.api(self.mgr_b), self.api(self.mgr_a), self.api(self.tech_b)
        body = {"connection": self.conn.connection_id, "to_village": self.vb.pk, "new_installation_address": "Badar shop", "reason": "moved"}
        self.assertEqual(tb.post("/api/v1/transfers/office/", body, format="json").status_code, 403)
        self.assertEqual(self.api(make_user(Role.OFFICE_STAFF, [self.b])).post("/api/v1/transfers/office/", body, format="json").status_code, 404)
        r = s.post("/api/v1/transfers/office/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        tid = r.json()["id"]
        self.assertEqual(s.post(f"/api/v1/transfers/office/{tid}/approve/", {}, format="json").status_code, 403)       # staff cannot approve
        self.assertEqual(ma.post(f"/api/v1/transfers/office/{tid}/approve/", {}, format="json").status_code, 404)      # not their destination
        self.assertEqual(mb.get("/api/v1/transfers/office/").json()["count"], 1)                                       # destination office sees it
        self.assertEqual(self.api(self.mgr_a).get("/api/v1/transfers/office/?status=pending").json()["count"], 1)
        ok = mb.post(f"/api/v1/transfers/office/{tid}/approve/", {"note": "welcome"}, format="json")
        self.assertEqual((ok.status_code, ok.json()["status"]), (200, "approved"))
        self.assertEqual(self.api(self.owner).get("/api/v1/connections/").json()["results"][0]["office_code"], "BBB")


class OwnershipTransferTests(TransferBase):
    def setUp(self):
        super().setUp()
        self.new_owner = new_customer(self.staff, self.va, self.a)
        self.today = timezone.localdate()

    def req(self, **kw):
        d = dict(actor=self.staff, connection=self.conn, new_customer=self.new_owner, effective_date=self.today,
                 balance_handling="none_outstanding", reason="house sold")
        d.update(kw)
        return tr.request_ownership_transfer(**d)

    def test_transfer_keeps_old_owner_history_and_new_owner_gets_future_bills(self):
        inv_ = bs.create_invoice(actor=self.staff, connection=self.conn)[0]
        bs.record_payment(actor=self.staff, invoice=inv_, method="cash", amount=inv_.total_amount)     # paid by the old owner
        t = self.req()
        tr.approve_ownership_transfer(actor=self.mgr_a, transfer=t, note="docs checked")
        self.conn.refresh_from_db(); inv_.refresh_from_db()
        self.assertEqual(self.conn.customer, self.new_owner)
        self.assertEqual(inv_.customer, self.cust)                                         # old invoice stays with the old owner
        self.assertEqual(Customer.objects.count(), 2)
        self.conn.refresh_from_db()
        Connection.objects.filter(pk=self.conn.pk).update(status="active", expires_at=timezone.now() + timedelta(days=3))
        new_inv = bs.create_invoice(actor=self.staff, connection=Connection.objects.get(pk=self.conn.pk))[0]
        self.assertEqual(new_inv.customer, self.new_owner)
        self.assertEqual((t.from_customer, t.to_customer), (self.cust, self.new_owner))
        self.assertTrue(AuditLog.objects.filter(action="connection.owner_changed").exists())

    def test_unpaid_balance_is_never_moved_silently(self):
        bs.create_invoice(actor=self.staff, connection=self.conn)                            # unpaid
        t = self.req()
        with self.assertRaises(BusinessRuleError) as cm:
            tr.approve_ownership_transfer(actor=self.mgr_a, transfer=t)
        self.assertIn("never moved to the new owner", str(cm.exception.detail))
        tr.reject_ownership_transfer(actor=self.mgr_a, transfer=t, note="redo with balance arrangement")
        with self.assertRaises(BusinessRuleError):
            self.req(balance_handling="old_owner_responsible")                              # arrangement must be explained
        t2 = self.req(balance_handling="old_owner_responsible", handling_note="Ali Khan will pay the Rs 1500 himself")
        tr.approve_ownership_transfer(actor=self.mgr_a, transfer=t2)
        from apps.billing.models import Invoice
        self.assertEqual(Invoice.objects.get().customer, self.cust)                          # the unpaid invoice still belongs to the old owner

    def test_unpaid_opening_balance_also_counts(self):
        OpeningBalance.objects.create(customer=self.cust, connection=self.conn, office=self.a, amount=Decimal("500"), migration_date=self.today, source_reference="K1")
        with self.assertRaises(BusinessRuleError):
            tr.approve_ownership_transfer(actor=self.mgr_a, transfer=self.req())

    def test_validation_and_approval_rules(self):
        other_office = new_customer(make_user(Role.OFFICE_STAFF, [self.b]), self.vb, self.b)
        for kw in ({"new_customer": self.cust}, {"new_customer": other_office}, {"balance_handling": "bogus"}, {"reason": ""}):
            with self.assertRaises(BusinessRuleError):
                self.req(**kw)
        t = self.req(effective_date=self.today + timedelta(days=5))
        with self.assertRaises(BusinessRuleError):
            tr.approve_ownership_transfer(actor=self.mgr_a, transfer=t)                      # future-dated is not supported
        with self.assertRaises(ConflictError):
            self.req()                                                                       # a pending request already exists
        t3 = OwnershipTransferHelper.fresh(self, t)
        with self.assertRaises(BusinessRuleError):
            tr.approve_ownership_transfer(actor=self.staff, transfer=t3)                     # requester cannot approve

    def test_http_flow_and_portal_visibility_follows_the_new_owner(self):
        from apps.portal import services as ps
        ps.create_account(actor=self.staff, customer=self.cust, username="old.owner", password="Portal-Passw0rd-1")
        ps.create_account(actor=self.staff, customer=self.new_owner, username="new.owner", password="Portal-Passw0rd-1")
        body = {"connection": self.conn.connection_id, "new_customer": self.new_owner.customer_id, "effective_date": str(self.today),
                "balance_handling": "none_outstanding", "reason": "sold"}
        r = self.api(self.staff).post("/api/v1/transfers/ownership/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        tid = r.json()["id"]
        self.assertEqual(self.api(self.staff).post(f"/api/v1/transfers/ownership/{tid}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(self.api(self.mgr_b).post(f"/api/v1/transfers/ownership/{tid}/approve/", {}, format="json").status_code, 404)
        self.assertEqual(self.api(self.mgr_a).post(f"/api/v1/transfers/ownership/{tid}/approve/", {}, format="json").json()["status"], "approved")
        old, new = APIClient(), APIClient()
        old.post("/api/v1/portal/auth/login/", {"username": "old.owner", "password": "Portal-Passw0rd-1"}, format="json")
        new.post("/api/v1/portal/auth/login/", {"username": "new.owner", "password": "Portal-Passw0rd-1"}, format="json")
        self.assertEqual(old.get("/api/v1/portal/connections/").json(), [])
        self.assertEqual([c["connection_id"] for c in new.get("/api/v1/portal/connections/").json()], [self.conn.connection_id])
        self.assertEqual(self.api(self.mgr_b).get("/api/v1/transfers/ownership/").json()["count"], 0)                 # other office sees nothing


class OwnershipTransferHelper:
    @staticmethod
    def fresh(case, previous):
        previous.status = "rejected"; previous.save()
        return case.req()
