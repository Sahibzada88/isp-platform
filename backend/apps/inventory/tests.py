import threading
from datetime import date
from decimal import Decimal
from unittest import skipUnless

from django.db import IntegrityError, connection as dbconn, transaction
from django.db.models import ProtectedError, Sum
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing.tests import new_customer
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.inventory import services as inv
from apps.inventory.models import Equipment, EquipmentAssignment, InventoryItem, MaterialUsage, StockMovement
from apps.notifications.models import Notification
from apps.operations import services as ops

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.labour = make_user(Role.LABOUR, [self.a])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.owner = make_user(Role.OWNER)
        cust = new_customer(self.staff, self.va, self.a)
        self.conn = cs.create_connection(actor=self.staff, customer=cust, package=make_package(), installation_address="x")
        self.complaint = ops.create_complaint(actor=self.staff, customer=cust, category="cable_damage", description="cut")
        self.cable = inv.create_item(actor=self.mgr, office=self.a, name="Fibre cable", category="cable", unit="m", minimum_stock=50)
        self.onu = inv.create_item(actor=self.mgr, office=self.a, name="ONU", category="router_onu", is_serialized=True, minimum_stock=1)

    def api(self, u):
        c = APIClient(); login(c, u); return c

    def stock(self, qty="500"):
        inv.record_purchase(actor=self.mgr, item=self.cable, quantity=qty, unit_price="20", purchase_date=date(2026, 10, 1))
        self.cable.refresh_from_db()

    def use(self, qty, key, **kw):
        kw.setdefault("complaint", self.complaint)
        return inv.use_materials(actor=self.tech, item=self.cable, quantity=qty, idempotency_key=key, **kw)

    def ledger_matches(self, item):
        item.refresh_from_db()
        total = StockMovement.objects.filter(item=item).aggregate(t=Sum("quantity_delta"))["t"] or D("0")
        faulty = StockMovement.objects.filter(item=item).aggregate(t=Sum("faulty_delta"))["t"] or D("0")
        self.assertEqual((item.quantity, item.faulty_quantity), (total, faulty))


class StockTests(Base):
    def test_purchase_and_usage_keep_ledger_consistent(self):
        p = inv.record_purchase(actor=self.mgr, item=self.cable, quantity="500", unit_price="20.50", purchase_date=date(2026, 10, 1), supplier_notes="Ali Traders")
        self.assertEqual(p.total_amount, D("10250.00"))
        self.use("120", "k1")
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, D("380.00"))
        self.ledger_matches(self.cable)

    def test_retry_with_same_key_never_deducts_twice(self):
        self.stock()
        u1, c1 = self.use("100", "same-key")
        u2, c2 = self.use("100", "same-key")           # network retry
        self.assertEqual((c1, c2, u1.pk), (True, False, u2.pk))
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, D("400.00"))
        self.assertEqual(MaterialUsage.objects.count(), 1)
        self.use("10", "another-key")
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, D("390.00"))
        self.ledger_matches(self.cable)

    def test_reopening_a_complaint_or_editing_a_task_cannot_rededuct(self):
        self.stock()
        key = f"complaint-{self.complaint.pk}-fibre"
        self.use("30", key)
        ops.assign(actor=self.staff, complaint=self.complaint, technician=self.tech)
        ops.start_work(actor=self.tech, complaint=self.complaint)
        ops.resolve(actor=self.tech, complaint=self.complaint, resolution_notes="done")
        ops.reopen(actor=self.staff, complaint=self.complaint, reason="again")
        self.use("30", key)                              # client re-submits the same job form
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, D("470.00"))

    def test_no_silent_negative_stock(self):
        self.stock("10")
        with self.assertRaises(BusinessRuleError) as cm:
            self.use("11", "k")
        self.assertIn("Insufficient stock", str(cm.exception.detail))
        self.cable.refresh_from_db()
        self.assertEqual((self.cable.quantity, MaterialUsage.objects.count(), StockMovement.objects.filter(kind="usage").count()), (D("10.00"), 0, 0))
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryItem.objects.filter(pk=self.cable.pk).update(quantity=-1)   # the database refuses too

    def test_usage_validation(self):
        self.stock()
        with self.assertRaises(BusinessRuleError):
            inv.use_materials(actor=self.tech, item=self.cable, quantity="5", idempotency_key="")
        with self.assertRaises(BusinessRuleError):
            inv.use_materials(actor=self.tech, item=self.cable, quantity="5", idempotency_key="k")          # no job linked
        with self.assertRaises(BusinessRuleError):
            self.use("0", "k2")
        with self.assertRaises(BusinessRuleError):
            self.use("abc", "k3")
        with self.assertRaises(BusinessRuleError):
            inv.use_materials(actor=self.tech, item=self.onu, quantity="1", idempotency_key="k4", complaint=self.complaint)

    def test_correction_uses_compensating_movement(self):
        self.stock()
        u, _ = self.use("40", "k1")
        with self.assertRaises(BusinessRuleError):
            inv.reverse_usage(actor=self.mgr, usage=u, reason="")
        inv.reverse_usage(actor=self.mgr, usage=u, reason="entered 40 instead of 4")
        with self.assertRaises(BusinessRuleError):
            inv.reverse_usage(actor=self.mgr, usage=u, reason="again")
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.quantity, D("500.00"))
        kinds = list(StockMovement.objects.filter(item=self.cable).order_by("id").values_list("kind", "quantity_delta"))
        self.assertEqual(kinds, [("purchase", D("500.00")), ("usage", D("-40.00")), ("usage_reversal", D("40.00"))])  # original kept
        self.ledger_matches(self.cable)

    def test_low_stock_alert_fires_once_per_episode(self):
        self.stock("60")
        self.use("5", "a")                      # 55: above minimum
        self.assertEqual(Notification.objects.filter(kind="inventory.low_stock").count(), 0)
        self.use("10", "b")                     # 45: crosses 50
        n = Notification.objects.filter(kind="inventory.low_stock", recipient=self.mgr).count()
        self.assertEqual(n, 1)
        self.use("5", "c"); self.use("5", "d")  # still low: no repeats
        self.assertEqual(Notification.objects.filter(kind="inventory.low_stock", recipient=self.mgr).count(), 1)
        self.assertFalse(Notification.objects.filter(kind="inventory.low_stock", recipient=self.staff_b).exists())  # other office
        self.stock("100")                       # restocked above minimum: re-armed
        self.use("100", "e"); self.use("1", "f")
        self.assertEqual(Notification.objects.filter(kind="inventory.low_stock", recipient=self.mgr).count(), 2)

    def test_stock_adjustment_needs_a_different_approver(self):
        self.stock("100")
        req = inv.request_adjustment(actor=self.staff, item=self.cable, delta="-8", reason="drum was short")
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="inventory.adjustment").exists())
        with self.assertRaises(BusinessRuleError):
            inv.decide_adjustment(actor=self.staff, adjustment=req, approve=True)
        self.cable.refresh_from_db(); self.assertEqual(self.cable.quantity, D("100.00"))   # nothing changes before approval
        inv.decide_adjustment(actor=self.mgr, adjustment=req, approve=True, approved_delta="-5", note="counted again")
        req.refresh_from_db(); self.cable.refresh_from_db()
        self.assertEqual((req.status, req.requested_delta, req.approved_delta, self.cable.quantity), ("approved", D("-8.00"), D("-5.00"), D("95.00")))
        with self.assertRaises(BusinessRuleError):
            inv.decide_adjustment(actor=self.mgr, adjustment=req, approve=False, note="x")
        r2 = inv.request_adjustment(actor=self.staff, item=self.cable, delta="+3", reason="found stock")
        with self.assertRaises(BusinessRuleError):
            inv.decide_adjustment(actor=self.mgr, adjustment=r2, approve=False)       # rejection needs a reason
        inv.decide_adjustment(actor=self.mgr, adjustment=r2, approve=False, note="no evidence")
        self.cable.refresh_from_db(); self.assertEqual(self.cable.quantity, D("95.00"))
        with self.assertRaises(BusinessRuleError):
            inv.request_adjustment(actor=self.staff, item=self.cable, delta="0", reason="x")
        self.ledger_matches(self.cable)

    def test_returned_stock_is_inspected_before_it_is_usable(self):
        self.stock("100")
        good = inv.submit_return(actor=self.tech, item=self.cable, quantity="12")
        bad = inv.submit_return(actor=self.tech, item=self.cable, quantity="3", note="kinked")
        self.cable.refresh_from_db(); self.assertEqual(self.cable.quantity, D("100.00"))    # not usable yet
        inv.inspect_return(actor=self.mgr, record=good, condition="good")
        inv.inspect_return(actor=self.mgr, record=bad, condition="faulty", note="cannot be reused")
        self.cable.refresh_from_db()
        self.assertEqual((self.cable.quantity, self.cable.faulty_quantity), (D("112.00"), D("3.00")))   # faulty kept apart
        with self.assertRaises(BusinessRuleError):
            inv.inspect_return(actor=self.mgr, record=good, condition="good")
        self.ledger_matches(self.cable)

    def test_items_are_never_deleted_and_names_unique_per_office(self):
        with self.assertRaises(ProtectedError):
            self.cable.delete()
        with self.assertRaises(BusinessRuleError):
            inv.create_item(actor=self.mgr, office=self.a, name="fibre CABLE", category="cable")
        inv.create_item(actor=self.mgr, office=self.b, name="Fibre cable", category="cable")   # same name, other office is fine


class EquipmentTests(Base):
    def buy(self, serials):
        return inv.record_purchase(actor=self.mgr, item=self.onu, quantity=str(len(serials)), unit_price="4500", purchase_date=date(2026, 10, 1), serials=serials)

    def test_purchase_creates_one_record_per_serial_and_syncs_stock(self):
        self.buy(["SN1", "SN2", "SN3"])
        self.onu.refresh_from_db()
        self.assertEqual((Equipment.objects.count(), self.onu.quantity), (3, D("3.00")))
        with self.assertRaises(BusinessRuleError):
            self.buy(["SN1"])                                           # existing serial
        with self.assertRaises(BusinessRuleError):
            inv.record_purchase(actor=self.mgr, item=self.onu, quantity="2", unit_price="1", purchase_date=date(2026, 10, 1), serials=["A"])
        with self.assertRaises(BusinessRuleError):
            self.buy(["X1", "x1"])                                      # duplicate inside the same request
        self.assertEqual(Equipment.objects.count(), 3)

    def test_a_device_cannot_be_on_two_active_connections(self):
        self.buy(["SN1"])
        eq = Equipment.objects.get()
        inv.assign_equipment(actor=self.tech, equipment=eq, connection=self.conn)
        c2 = cs.create_connection(actor=self.staff, customer=self.conn.customer, package=self.conn.package, installation_address="y")
        with self.assertRaises(BusinessRuleError):
            inv.assign_equipment(actor=self.tech, equipment=eq, connection=c2)
        with self.assertRaises(IntegrityError), transaction.atomic():      # and the database agrees
            EquipmentAssignment.objects.create(equipment=eq, connection=c2, assigned_by=self.tech)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Equipment.objects.filter(pk=eq.pk).update(assigned_connection=None)   # status 'assigned' needs a connection
        self.onu.refresh_from_db(); self.assertEqual(self.onu.quantity, D("0.00"))

    def test_return_inspect_cycle_and_faulty_stays_out_of_stock(self):
        self.buy(["SN1", "SN2"])
        e1, e2 = Equipment.objects.order_by("serial_number")
        inv.assign_equipment(actor=self.tech, equipment=e1, connection=self.conn)
        inv.assign_equipment(actor=self.tech, equipment=e2, connection=self.conn)
        inv.unassign_equipment(actor=self.tech, equipment=e1)
        inv.unassign_equipment(actor=self.tech, equipment=e2)
        e1.refresh_from_db(); self.assertEqual((e1.status, e1.assigned_connection), ("pending_return", None))
        self.onu.refresh_from_db(); self.assertEqual(self.onu.quantity, D("0.00"))         # not usable until inspected
        with self.assertRaises(BusinessRuleError):
            inv.assign_equipment(actor=self.tech, equipment=e1, connection=self.conn)
        inv.inspect_equipment(actor=self.mgr, equipment=e1, result="good")
        inv.inspect_equipment(actor=self.mgr, equipment=e2, result="faulty", condition="burnt port")
        self.onu.refresh_from_db()
        self.assertEqual((self.onu.quantity, Equipment.objects.get(pk=e2.pk).status), (D("1.00"), "faulty"))
        inv.assign_equipment(actor=self.tech, equipment=e1, connection=self.conn)           # reused after inspection
        self.assertEqual(EquipmentAssignment.objects.filter(equipment=e1).count(), 2)       # history kept
        with self.assertRaises(BusinessRuleError):
            inv.assign_equipment(actor=self.tech, equipment=e2, connection=self.conn)       # faulty cannot be assigned
        with self.assertRaises(BusinessRuleError):
            inv.set_equipment_status(actor=self.mgr, equipment=e1, status="lost")           # still at a customer

    def test_office_and_cancelled_connection_rules(self):
        self.buy(["SN1"])
        eq = Equipment.objects.get()
        other = cs.create_connection(actor=self.staff_b, customer=new_customer(self.staff_b, self.vb, self.b), package=self.conn.package, installation_address="z")
        with self.assertRaises(BusinessRuleError):
            inv.assign_equipment(actor=self.tech, equipment=eq, connection=other)
        cs.cancel_connection(actor=self.mgr, connection=self.conn, reason="x")
        with self.assertRaises(BusinessRuleError):
            inv.assign_equipment(actor=self.tech, equipment=eq, connection=self.conn)

    def test_low_stock_for_serialized_items(self):
        self.buy(["SN1", "SN2"])
        for e in Equipment.objects.all():
            inv.assign_equipment(actor=self.tech, equipment=e, connection=self.conn)
        self.assertTrue(Notification.objects.filter(kind="inventory.low_stock", recipient=self.mgr).exists())


class InventoryApiTests(Base):
    def test_permissions_and_scope(self):
        t, m, b = self.api(self.tech), self.api(self.mgr), self.api(self.staff_b)
        self.assertEqual(t.get("/api/v1/inventory/items/").json()["count"], 2)
        self.assertEqual(b.get("/api/v1/inventory/items/").json()["count"], 0)
        self.assertEqual(b.get(f"/api/v1/inventory/items/{self.cable.pk}/").status_code, 404)
        self.assertEqual(t.post("/api/v1/inventory/items/", {"office": self.a.pk, "name": "X", "category": "other"}, format="json").status_code, 403)
        r = m.post("/api/v1/inventory/items/", {"office": self.a.pk, "name": "Clamps", "category": "pole_clamp", "minimum_stock": "20"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(t.post("/api/v1/inventory/purchases/", {"item": self.cable.pk, "quantity": "5", "unit_price": "1", "purchase_date": "2026-10-01"}, format="json").status_code, 403)
        self.assertEqual(m.post("/api/v1/inventory/purchases/", {"item": self.cable.pk, "quantity": "300", "unit_price": "21.5", "purchase_date": "2026-10-01"}, format="json").status_code, 201)
        self.assertEqual(self.api(self.staff_b).post("/api/v1/inventory/purchases/", {"item": self.cable.pk, "quantity": "1", "unit_price": "1", "purchase_date": "2026-10-01"}, format="json").status_code, 404)

    def test_usage_over_http_is_idempotent(self):
        self.stock("200")
        t = self.api(self.tech)
        body = {"item": self.cable.pk, "quantity": "25", "idempotency_key": "form-123", "complaint": self.complaint.number}
        r1 = t.post("/api/v1/inventory/usages/", body, format="json")
        r2 = t.post("/api/v1/inventory/usages/", body, format="json")      # browser re-sent after a timeout
        self.assertEqual((r1.status_code, r2.status_code, r1.json()["id"] == r2.json()["id"]), (201, 200, True))
        self.cable.refresh_from_db(); self.assertEqual(self.cable.quantity, D("175.00"))
        over = t.post("/api/v1/inventory/usages/", {**body, "quantity": "9999", "idempotency_key": "form-124"}, format="json")
        self.assertEqual(over.status_code, 400)
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/inventory/usages/{r1.json()['id']}/reverse/", {"reason": "typo"}, format="json").status_code, 200)
        self.assertEqual(t.post(f"/api/v1/inventory/usages/{r1.json()['id']}/reverse/", {"reason": "x"}, format="json").status_code, 403)
        mv = self.api(self.mgr).get(f"/api/v1/inventory/movements/?item={self.cable.pk}").json()
        self.assertEqual(mv["count"], 3)

    def test_adjustment_and_equipment_flow_over_http(self):
        self.stock("100")
        s, m = self.api(self.staff), self.api(self.mgr)
        r = s.post("/api/v1/inventory/adjustments/", {"item": self.cable.pk, "delta": "-4", "reason": "damaged"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(s.post(f"/api/v1/inventory/adjustments/{r.json()['id']}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(m.post(f"/api/v1/inventory/adjustments/{r.json()['id']}/approve/", {}, format="json").json()["status"], "approved")
        inv.record_purchase(actor=self.mgr, item=self.onu, quantity="1", unit_price="4000", purchase_date=date(2026, 10, 1), serials=["HTTP1"])
        eq = Equipment.objects.get(serial_number="HTTP1")
        t = self.api(self.tech)
        a = t.post(f"/api/v1/inventory/equipment/{eq.pk}/assign/", {"connection": self.conn.connection_id}, format="json")
        self.assertEqual((a.status_code, a.json()["status"], a.json()["connection"]), (200, "assigned", self.conn.connection_id))
        self.assertEqual(t.post(f"/api/v1/inventory/equipment/{eq.pk}/inspect/", {"condition": "good"}, format="json").status_code, 403)
        self.assertEqual(t.post(f"/api/v1/inventory/equipment/{eq.pk}/unassign/").json()["status"], "pending_return")
        self.assertEqual(m.post(f"/api/v1/inventory/equipment/{eq.pk}/inspect/", {"condition": "good"}, format="json").json()["status"], "available")
        self.assertEqual(self.api(self.staff_b).get("/api/v1/inventory/equipment/").json()["count"], 0)

    def test_low_stock_filter(self):
        self.stock("40")
        self.assertEqual(self.api(self.mgr).get("/api/v1/inventory/items/?low=true").json()["count"], 2)   # cable and (empty) ONU
        self.stock("500")
        self.assertEqual(self.api(self.mgr).get("/api/v1/inventory/items/?low=true").json()["count"], 1)


@skipUnless(dbconn.vendor == "postgresql", "needs PostgreSQL row locks")
class InventoryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.a = make_office("AAA"); self.va = make_village(self.a, "KHT")
        self.mgr = make_user(Role.MANAGER, [self.a]); self.tech = make_user(Role.TECHNICIAN, [self.a])
        cust = new_customer(self.mgr, self.va, self.a)
        self.conn = cs.create_connection(actor=self.mgr, customer=cust, package=make_package(), installation_address="x")
        self.complaint = ops.create_complaint(actor=self.mgr, customer=cust, category="other", description="x")
        self.item = inv.create_item(actor=self.mgr, office=self.a, name="Cable", category="cable", unit="m")
        self.onu = inv.create_item(actor=self.mgr, office=self.a, name="ONU", category="router_onu", is_serialized=True)

    def _run(self, fn, n):
        out, errs = [], []

        def w():
            try:
                out.append(fn())
            except Exception as e:  # noqa: BLE001
                errs.append(e)
            finally:
                dbconn.close()

        ts = [threading.Thread(target=w) for _ in range(n)]
        [t.start() for t in ts]; [t.join() for t in ts]
        return out, errs

    def test_parallel_retries_of_the_same_usage_deduct_once(self):
        inv.record_purchase(actor=self.mgr, item=self.item, quantity="100", unit_price="1", purchase_date=date(2026, 10, 1))
        out, errs = self._run(lambda: inv.use_materials(actor=self.tech, item=self.item, quantity="30", idempotency_key="dup", complaint=self.complaint), 6)
        self.assertEqual(errs, [])
        self.assertEqual(sum(1 for _, created in out if created), 1)
        self.item.refresh_from_db(); self.assertEqual(self.item.quantity, D("70.00"))
        self.assertEqual(MaterialUsage.objects.count(), 1)

    def test_parallel_usage_cannot_drive_stock_negative(self):
        inv.record_purchase(actor=self.mgr, item=self.item, quantity="100", unit_price="1", purchase_date=date(2026, 10, 1))
        n = iter(range(100)); lock = threading.Lock()

        def take():
            with lock:
                k = f"k{next(n)}"
            return inv.use_materials(actor=self.tech, item=self.item, quantity="30", idempotency_key=k, complaint=self.complaint)

        out, errs = self._run(take, 6)
        self.assertEqual(len(out), 3)                       # 3 x 30 fits in 100, the rest are refused
        self.assertTrue(all(isinstance(e, BusinessRuleError) for e in errs))
        self.item.refresh_from_db(); self.assertEqual(self.item.quantity, D("10.00"))

    def test_parallel_assignment_of_one_device_succeeds_once(self):
        inv.record_purchase(actor=self.mgr, item=self.onu, quantity="1", unit_price="1", purchase_date=date(2026, 10, 1), serials=["RACE1"])
        eq = Equipment.objects.get()
        out, errs = self._run(lambda: inv.assign_equipment(actor=self.tech, equipment=eq, connection=self.conn), 5)
        self.assertEqual(len(out), 1)
        self.assertEqual(EquipmentAssignment.objects.filter(equipment=eq, returned_at__isnull=True).count(), 1)
