import threading
from datetime import timedelta
from unittest import skipUnless

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import PermissionGrant, Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services
from apps.customers.models import Connection, Customer

CUSTOMER = {"full_name": "Ali Khan", "phone": "03001234567", "cnic": "3520212345671",
            "permanent_address": "House 1, Kotha"}


class Base(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT", "Kotha"), make_village(self.b, "BDR", "Badar")
        self.staff_a = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr_a = make_user(Role.MANAGER, [self.a])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.pkg = make_package()
        self.c_staff = APIClient(); login(self.c_staff, self.staff_a)
        self.c_mgr = APIClient(); login(self.c_mgr, self.mgr_a)

    def new_customer(self, client=None, village=None, **over):
        body = {**CUSTOMER, "village": (village or self.va).pk, **over}
        return (client or self.c_staff).post("/api/v1/customers/", body, format="json")

    def new_connection(self, cust_id="KHT001", client=None):
        return (client or self.c_staff).post("/api/v1/connections/", {
            "customer": cust_id, "package": self.pkg.pk, "installation_address": "Shop 4"}, format="json")


class CustomerTests(Base):
    def test_ids_are_sequential_per_village_and_stable(self):
        ids = [self.new_customer(phone=f"0300000000{i}", full_name=f"N{i}", cnic="").json()["customer_id"] for i in range(3)]
        self.assertEqual(ids, ["KHT001", "KHT002", "KHT003"])
        r = self.c_staff.patch("/api/v1/customers/KHT002/", {"full_name": "Renamed", "permanent_address": "New"}, format="json")
        self.assertEqual(r.json()["customer_id"], "KHT002")
        self.assertEqual(Customer.objects.filter(customer_id="KHT002").count(), 1)

    def test_duplicates_warn_but_do_not_block(self):
        self.assertEqual(self.new_customer().status_code, 201)
        r = self.new_customer()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "possible_duplicate")
        self.assertEqual(Customer.objects.count(), 1)
        ok = self.c_staff.post("/api/v1/customers/", {**CUSTOMER, "village": self.va.pk, "confirm_duplicates": True,
                                                       "override_reason": "same family, new house"}, format="json")
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["customer_id"], "KHT002")
        self.assertTrue(AuditLog.objects.filter(action="customer.duplicate_override", reason="same family, new house").exists())

    def test_cnic_masked_by_default_and_revealed_with_permission(self):
        self.new_customer()
        self.assertEqual(self.c_staff.get("/api/v1/customers/KHT001/").json()["cnic"], "*****-*******-1")
        PermissionGrant.objects.create(employee=self.staff_a, office=self.a, code="customers.view_cnic")
        self.assertEqual(self.c_staff.get("/api/v1/customers/KHT001/").json()["cnic"], "35202-1234567-1")
        self.assertEqual(self.c_staff.get("/api/v1/customers/").json()["results"][0]["cnic"], "35202-1234567-1")
        self.assertNotIn("35202-1234567-1", str(AuditLog.objects.filter(entity_id__isnull=False).values("after")))

    def test_invalid_cnic_rejected(self):
        self.assertEqual(self.new_customer(cnic="123").status_code, 400)

    def test_cross_office_isolation(self):
        self.new_customer()
        cb = APIClient(); login(cb, self.staff_b)
        self.assertEqual(cb.get("/api/v1/customers/KHT001/").status_code, 404)  # ID guessing leaks nothing
        self.assertEqual(cb.get("/api/v1/customers/").json()["count"], 0)
        self.assertEqual(cb.patch("/api/v1/customers/KHT001/", {"full_name": "Hacked"}, format="json").status_code, 404)
        self.assertEqual(self.new_customer(client=cb, village=self.va).status_code, 404)  # can't register into office A
        self.assertEqual(self.new_customer(village=self.vb).status_code, 404)
        self.assertEqual(Customer.objects.get(customer_id="KHT001").full_name, "Ali Khan")

    def test_permission_required_for_actions(self):
        tech = make_user(Role.TECHNICIAN, [self.a]); c = APIClient(); login(c, tech)
        self.assertEqual(self.new_customer(client=c).status_code, 403)
        self.assertEqual(APIClient().get("/api/v1/customers/").status_code, 403)

    def test_address_history_preserved(self):
        self.new_customer()
        self.c_staff.patch("/api/v1/customers/KHT001/", {"permanent_address": "Second address"}, format="json")
        h = Customer.objects.get(customer_id="KHT001").address_history.get()
        self.assertEqual((h.old_address, h.new_address), ("House 1, Kotha", "Second address"))

    def test_customers_cannot_be_deleted_and_village_code_frozen(self):
        self.new_customer()
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            Customer.objects.get(customer_id="KHT001").delete()
        self.va.code = "NEW"
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.va.save()
        self.assertIn(self.c_staff.delete("/api/v1/customers/KHT001/").status_code, (403, 405))  # deny-by-default


@skipUnless(connection.vendor == "postgresql", "needs PostgreSQL row locks")
class CustomerIdConcurrencyTests(TransactionTestCase):
    def test_parallel_registration_yields_unique_ids(self):
        office = make_office("AAA"); village = make_village(office, "KHT")
        actor = make_user(Role.OFFICE_STAFF, [office])
        ids, errors = [], []

        def worker(i):
            try:
                c = services.register_customer(actor=actor, confirm_duplicates=True, data={
                    "full_name": f"N{i}", "phone": f"0300{i}", "cnic": "", "permanent_address": "x",
                    "village": village, "office": office})
                ids.append(c.customer_id)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                connection.close()

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(ids), [f"KHT{n:03d}" for n in range(1, 9)])


class ConnectionTests(Base):
    def setUp(self):
        super().setUp()
        self.new_customer()

    def test_multiple_connections_per_customer_with_unique_ids(self):
        r1, r2 = self.new_connection(), self.new_connection()
        self.assertEqual([r1.json()["connection_id"], r2.json()["connection_id"]], ["CON-00001", "CON-00002"])
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Customer.objects.get().connections.count(), 2)

    def test_connection_creation_scoped_and_requires_active_package(self):
        cb = APIClient(); login(cb, self.staff_b)
        self.assertEqual(self.new_connection(client=cb).status_code, 404)
        self.pkg.is_active = False; self.pkg.save()
        self.assertEqual(self.new_connection().status_code, 400)

    def _pending_approval(self):
        cid = self.new_connection().json()["connection_id"]
        self.c_mgr.post(f"/api/v1/connections/{cid}/complete-installation/")
        return cid

    def test_activation_needs_payment_installation_and_approval(self):
        cid = self.new_connection().json()["connection_id"]
        url = f"/api/v1/connections/{cid}/approve-activation/"
        # installation not done -> refused (also: no approval can skip the state machine)
        self.assertEqual(self.c_mgr.post(url).status_code, 400)
        self.c_mgr.post(f"/api/v1/connections/{cid}/complete-installation/")
        # installation done but payment not confirmed -> still refused
        r = self.c_mgr.post(url)
        self.assertEqual(r.status_code, 400)
        self.assertIn("payment", r.json()["error"]["message"])
        self.assertEqual(Connection.objects.get(connection_id=cid).status, "pending_approval")
        # staff without approval permission can never activate
        paid = timezone.now() - timedelta(hours=2)
        Connection.objects.filter(connection_id=cid).update(initial_payment_confirmed_at=paid)  # billing module's job
        self.assertEqual(self.c_staff.post(url).status_code, 403)
        r = self.c_mgr.post(url)
        self.assertEqual(r.status_code, 200, r.content)
        conn = Connection.objects.get(connection_id=cid)
        self.assertEqual(conn.status, "active")
        self.assertEqual(conn.expires_at, paid + timedelta(days=30))
        self.assertEqual(conn.activation_approved_by, self.mgr_a)

    def test_payment_alone_does_not_activate(self):
        cid = self.new_connection().json()["connection_id"]
        Connection.objects.filter(connection_id=cid).update(initial_payment_confirmed_at=timezone.now())
        self.assertEqual(self.c_mgr.post(f"/api/v1/connections/{cid}/approve-activation/").status_code, 400)
        self.assertEqual(Connection.objects.get(connection_id=cid).status, "pending_installation")

    def test_cancel_preserves_history_and_isolates_other_connections(self):
        c1 = self.new_connection().json()["connection_id"]; c2 = self.new_connection().json()["connection_id"]
        Connection.objects.filter(connection_id=c2).update(status="active", expires_at=timezone.now() + timedelta(days=9))
        r = self.c_mgr.post(f"/api/v1/connections/{c1}/cancel/", {"reason": "moving", "equipment_unreturned": True}, format="json")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual((d["status"], d["equipment_return_status"], d["cancellation_reason"]), ("cancelled", "pending_return", "moving"))
        self.assertEqual(Connection.objects.get(connection_id=c2).status, "active")
        self.assertTrue(Connection.objects.filter(connection_id=c1).exists())
        self.assertEqual(self.c_mgr.post(f"/api/v1/connections/{c1}/cancel/", {"reason": "again"}, format="json").status_code, 400)

    def test_reactivation_resets_gates(self):
        cid = self._pending_approval()
        Connection.objects.filter(connection_id=cid).update(initial_payment_confirmed_at=timezone.now())
        self.c_mgr.post(f"/api/v1/connections/{cid}/approve-activation/")
        self.c_mgr.post(f"/api/v1/connections/{cid}/cancel/", {"reason": "x"}, format="json")
        self.assertEqual(self.c_mgr.post(f"/api/v1/connections/{cid}/approve-activation/").status_code, 400)  # cancelled can't skip
        r = self.c_mgr.post(f"/api/v1/connections/{cid}/reactivate/", {"reason": "customer returned"}, format="json")
        d = r.json()
        self.assertEqual((d["status"], d["installation_completed_at"], d["initial_payment_confirmed_at"], d["expires_at"]),
                         ("pending_installation", None, None, None))
        self.assertTrue(AuditLog.objects.filter(action="connection.reactivation_started").exists())

    def test_address_change_keeps_history(self):
        cid = self.new_connection().json()["connection_id"]
        r = self.c_staff.post(f"/api/v1/connections/{cid}/change-address/", {"new_address": "Shop 9", "reason": "moved"}, format="json")
        self.assertEqual(r.json()["installation_address"], "Shop 9")
        h = Connection.objects.get(connection_id=cid).address_history.get()
        self.assertEqual((h.old_address, h.new_address, h.changed_by), ("Shop 4", "Shop 9", self.staff_a))

    def test_package_change_applies_next_renewal_only(self):
        cid = self.new_connection().json()["connection_id"]
        p2 = make_package("P20", "2500.00", 20)
        r = self.c_mgr.post(f"/api/v1/connections/{cid}/schedule-package/", {"package": p2.pk}, format="json")
        d = r.json()
        self.assertEqual((d["package_code"], d["next_package_code"]), ("P10", "P20"))

    def test_cross_office_connection_isolation(self):
        cid = self.new_connection().json()["connection_id"]
        cb = APIClient(); login(cb, self.staff_b)
        self.assertEqual(cb.get(f"/api/v1/connections/{cid}/").status_code, 404)
        self.assertEqual(cb.get("/api/v1/connections/").json()["count"], 0)
        mgr_b = make_user(Role.MANAGER, [self.b]); cm = APIClient(); login(cm, mgr_b)
        self.assertEqual(cm.post(f"/api/v1/connections/{cid}/cancel/", {"reason": "x"}, format="json").status_code, 404)

    def test_filters_and_search(self):
        self.new_connection(); self.new_connection()
        self.assertEqual(self.c_staff.get("/api/v1/connections/?search=KHT001").json()["count"], 2)
        self.assertEqual(self.c_staff.get("/api/v1/connections/?status=active").json()["count"], 0)


class ExpiryTests(Base):
    def setUp(self):
        super().setUp()
        self.new_customer()
        self.cust = Customer.objects.get()

    def _conn(self, status, expires):
        c = services.create_connection(actor=self.mgr_a, customer=self.cust, package=self.pkg, installation_address="x")
        Connection.objects.filter(pk=c.pk).update(status=status, expires_at=expires)
        return c.pk

    def test_only_active_connections_expire_and_job_is_idempotent(self):
        past = timezone.now() - timedelta(minutes=1)
        active = self._conn("active", past)
        future = self._conn("active", timezone.now() + timedelta(days=3))
        cancelled = self._conn("cancelled", past)
        pending_i = self._conn("pending_installation", past)
        pending_a = self._conn("pending_approval", past)
        suspended = self._conn("suspended", past)
        self.assertEqual(services.process_expiries(), 1)
        self.assertEqual(services.process_expiries(), 0)
        status = lambda pk: Connection.objects.get(pk=pk).status
        self.assertEqual([status(p) for p in (active, future, cancelled, pending_i, pending_a, suspended)],
                         ["expired", "active", "cancelled", "pending_installation", "pending_approval", "suspended"])
        self.assertEqual(AuditLog.objects.filter(action="connection.expired").count(), 1)

    def test_batch_is_bounded(self):
        past = timezone.now() - timedelta(minutes=1)
        for _ in range(5):
            self._conn("active", past)
        self.assertEqual(services.process_expiries(batch_size=2), 2)
        self.assertEqual(services.process_expiries(batch_size=10), 3)

    def test_access_time_check_uses_same_rule(self):
        pk = self._conn("active", timezone.now() - timedelta(minutes=1))
        conn = services.expire_if_due(Connection.objects.get(pk=pk))
        self.assertEqual(conn.status, "expired")
        pk2 = self._conn("cancelled", timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.expire_if_due(Connection.objects.get(pk=pk2)).status, "cancelled")

    def test_db_rejects_active_without_expiry(self):
        from django.db import IntegrityError
        pk = self._conn("pending_installation", None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Connection.objects.filter(pk=pk).update(status="active")
