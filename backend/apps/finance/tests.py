import threading
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from django.db import IntegrityError, connection as dbconn, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.billing import services as bs
from apps.billing.models import BillingTerm, Invoice, Payment, Receipt
from apps.billing.tests import Base, new_customer
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.customers.models import Connection
from apps.finance import services as fs
from apps.finance.models import CashDeposit, DepositItem, OverpaymentException, PaymentCorrection, Refund

D = Decimal


class FinBase(Base):
    def paid_initial(self, actor=None):
        c = self.conn()
        inv = self.invoice(c)
        return c, inv, self.cash(inv, actor)

    def reverse(self, p, requester=None, approver=None, reason="entered against wrong customer"):
        corr = fs.request_correction(actor=requester or self.staff, payment=p, reason=reason)
        return fs.approve_correction(actor=approver or self.mgr, correction=corr, note="checked")


class CorrectionTests(FinBase):
    def test_reversal_of_initial_payment_restores_connection_and_reopens_invoice(self):
        c, inv, p = self.paid_initial()
        self.reverse(p)
        for o in (c, inv, p):
            o.refresh_from_db()
        self.assertEqual((p.status, p.receipt.status, inv.status), ("reversed", "invalidated", "open"))
        self.assertIsNone(c.initial_payment_confirmed_at)
        self.assertIsNone(c.expires_at)
        self.assertIsNotNone(inv.terms.get().reversed_at)
        self.assertTrue(Payment.objects.filter(pk=p.pk).exists())  # never deleted
        again = self.cash(inv)  # the correct payment can now be entered
        self.assertEqual(again.receipt.number, "AAA-RCP-000002")  # numbers are never reused
        c.refresh_from_db()
        self.assertEqual(c.expires_at, c.initial_payment_confirmed_at + timedelta(days=30))

    def test_four_eyes_rule(self):
        c, inv, p = self.paid_initial()
        corr = fs.request_correction(actor=self.mgr, payment=p, reason="wrong amount keyed")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)
        fs.approve_correction(actor=self.owner, correction=corr)  # owner may decide
        p.refresh_from_db()
        self.assertEqual(p.status, "reversed")

    def test_reject_keeps_payment_and_allows_new_request(self):
        c, inv, p = self.paid_initial()
        corr = fs.request_correction(actor=self.staff, payment=p, reason="maybe wrong")
        with self.assertRaises(ConflictError):
            fs.request_correction(actor=self.staff, payment=p, reason="again")
        with self.assertRaises(BusinessRuleError):
            fs.reject_correction(actor=self.mgr, correction=corr, note="")
        fs.reject_correction(actor=self.mgr, correction=corr, note="receipt is correct")
        p.refresh_from_db(); corr.refresh_from_db()
        self.assertEqual((p.status, corr.status, corr.decision_note), ("confirmed", "rejected", "receipt is correct"))
        fs.request_correction(actor=self.staff, payment=p, reason="second attempt")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)  # already decided

    def test_reason_required_and_only_confirmed_payments(self):
        c, inv, p = self.paid_initial()
        with self.assertRaises(BusinessRuleError):
            fs.request_correction(actor=self.staff, payment=p, reason="")
        inv2 = self.invoice(self.conn())
        pending = bs.record_payment(actor=self.staff, invoice=inv2, method="easypaisa", amount=inv2.total_amount, reference="P1")
        with self.assertRaises(BusinessRuleError):  # must be rejected, not "corrected"
            fs.request_correction(actor=self.staff, payment=pending, reason="x")

    def test_cannot_reverse_once_connection_is_active(self):
        c, inv, p = self.paid_initial()
        cs.complete_installation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        cs.approve_activation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        corr = fs.request_correction(actor=self.staff, payment=p, reason="oops")
        with self.assertRaises(BusinessRuleError) as cm:
            fs.approve_correction(actor=self.mgr, correction=corr)
        self.assertIn("activated", str(cm.exception.detail))
        p.refresh_from_db(); corr.refresh_from_db()
        self.assertEqual((p.status, corr.status), ("confirmed", "pending"))  # nothing half-applied

    def test_early_renewal_reversal_restores_expiry_until_term_starts(self):
        c = self.active_conn(days=5)
        before = c.expires_at
        inv = self.invoice(c); p = self.cash(inv)
        c.refresh_from_db()
        self.assertEqual(c.expires_at, before + timedelta(days=30))
        self.reverse(p)
        c.refresh_from_db()
        self.assertEqual(c.expires_at, before)
        # a new early renewal whose term has already started cannot be reversed
        inv.refresh_from_db()
        p2 = self.cash(inv)
        BillingTerm.objects.filter(invoice=inv, reversed_at__isnull=True).update(starts_at=timezone.now() - timedelta(days=1))
        corr = fs.request_correction(actor=self.staff, payment=p2, reason="late")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)

    def test_expired_renewal_reversal_ok_until_confirmed(self):
        c = self.expired_conn()
        inv = self.invoice(c); p = self.cash(inv)
        self.reverse(p)
        c.refresh_from_db()
        self.assertEqual(c.status, "expired")
        with self.assertRaises(BusinessRuleError):  # reversed payment can't be confirmed as a renewal
            bs.confirm_renewal(actor=self.mgr, connection=c)
        inv.refresh_from_db()
        p2 = self.cash(inv)
        bs.confirm_renewal(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        corr = fs.request_correction(actor=self.staff, payment=p2, reason="x")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)

    def test_verified_digital_payment_reversal_frees_reference(self):
        c = self.conn(); inv = self.invoice(c)
        p = bs.record_payment(actor=self.staff, invoice=inv, method="jazzcash", amount=inv.total_amount, reference="JC1")
        bs.verify_payment(actor=self.mgr, payment=p)
        self.reverse(p)
        inv.refresh_from_db()
        again = bs.record_payment(actor=self.staff, invoice=inv, method="jazzcash", amount=inv.total_amount, reference="JC1")
        self.assertEqual(again.status, "pending_verification")

    def test_reversal_voids_invoice_when_another_is_already_open(self):
        c = self.active_conn(days=5)
        inv1 = self.invoice(c); p = self.cash(inv1)
        inv2 = self.invoice(Connection.objects.get(pk=c.pk))
        self.assertNotEqual(inv1.pk, inv2.pk)
        self.reverse(p)
        inv1.refresh_from_db(); inv2.refresh_from_db()
        self.assertEqual((inv1.status, inv2.status), ("void", "open"))

    def test_blocked_by_deposit_and_by_refund(self):
        col = self.collector
        c = self.conn(); inv = self.invoice(c); p = self.cash(inv, actor=col)
        dep = fs.create_deposit(actor=col, office=self.a)
        corr = fs.request_correction(actor=self.staff, payment=p, reason="wrong")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)
        fs.cancel_deposit(actor=self.mgr, deposit=dep, reason="recount")
        fs.request_refund(actor=self.staff, payment=p, amount="100.00", reason="goodwill")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)
        Refund.objects.update(status="rejected")
        fs.approve_correction(actor=self.mgr, correction=corr)
        p.refresh_from_db()
        self.assertEqual(p.status, "reversed")

    def test_http_flow_permissions_and_isolation(self):
        c, inv, p = self.paid_initial()
        s, m, b = self.api(self.staff), self.api(self.mgr), self.api(self.staff_b)
        self.assertEqual(b.post("/api/v1/corrections/", {"payment": p.pk, "reason": "x"}, format="json").status_code, 404)
        r = s.post("/api/v1/corrections/", {"payment": p.pk, "reason": "wrong customer"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()["id"]
        self.assertEqual(s.post(f"/api/v1/corrections/{cid}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(b.get(f"/api/v1/corrections/{cid}/").status_code, 404)
        ok = m.post(f"/api/v1/corrections/{cid}/approve/", {"note": "verified"}, format="json")
        self.assertEqual((ok.status_code, ok.json()["status"]), (200, "approved"))
        self.assertEqual(s.get(f"/api/v1/payments/{p.pk}/").json()["status"], "reversed")
        self.assertEqual(s.get(f"/api/v1/receipts/{p.receipt.number}/").json()["status"], "invalidated")
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"correction.requested", "correction.approved", "payment.reversed"} <= actions)


class RefundTests(FinBase):
    def test_owner_approval_caps_and_payout(self):
        c, inv, p = self.paid_initial()  # PKR 1500 cash
        r1 = fs.request_refund(actor=self.staff, payment=p, amount="500.00", reason="service not started")
        with self.assertRaises(PermissionDenied):
            fs.approve_refund(actor=self.mgr, refund=r1)  # managers can't approve refunds
        with self.assertRaises(BusinessRuleError):
            fs.mark_refund_paid(actor=self.mgr, refund=r1)  # not approved yet
        with self.assertRaises(BusinessRuleError):
            fs.request_refund(actor=self.staff, payment=p, amount="1100.00", reason="too much")  # 1000 remaining
        fs.approve_refund(actor=self.owner, refund=r1, note="ok")
        paid = fs.mark_refund_paid(actor=self.mgr, refund=r1)
        self.assertEqual((paid.status, paid.method, paid.paid_by, paid.decided_by), ("paid", "cash", self.mgr, self.owner))
        r2 = fs.request_refund(actor=self.staff, payment=p, amount="1000.00", reason="rest")
        fs.reject_refund(actor=self.owner, refund=r2, note="not justified")
        fs.request_refund(actor=self.staff, payment=p, amount="1000.00", reason="again")  # rejected one no longer counts
        with self.assertRaises(BusinessRuleError):
            fs.request_refund(actor=self.staff, payment=p, amount="0.01", reason="over")

    def test_other_method_needs_exception_reason(self):
        c = self.conn(); inv = self.invoice(c)
        p = bs.record_payment(actor=self.staff, invoice=inv, method="easypaisa", amount=inv.total_amount, reference="E1")
        bs.verify_payment(actor=self.mgr, payment=p)
        r = fs.request_refund(actor=self.staff, payment=p, amount="100.00", reason="x")
        fs.approve_refund(actor=self.owner, refund=r)
        with self.assertRaises(BusinessRuleError):
            fs.mark_refund_paid(actor=self.mgr, refund=r, method="cash")
        done = fs.mark_refund_paid(actor=self.mgr, refund=r, method="cash", exception_reason="customer has no wallet")
        self.assertEqual((done.method, done.exception_reason), ("cash", "customer has no wallet"))

    def test_only_confirmed_payments_refundable(self):
        inv = self.invoice(self.conn())
        pending = bs.record_payment(actor=self.staff, invoice=inv, method="bank_transfer", amount=inv.total_amount, reference="B1")
        with self.assertRaises(BusinessRuleError):
            fs.request_refund(actor=self.staff, payment=pending, amount="10.00", reason="x")
        c, inv2, p = self.paid_initial()
        self.reverse(p)
        with self.assertRaises(BusinessRuleError):
            fs.request_refund(actor=self.staff, payment=p, amount="10.00", reason="x")

    def test_http_permissions(self):
        c, inv, p = self.paid_initial()
        s, m, o, b = self.api(self.staff), self.api(self.mgr), self.api(self.owner), self.api(self.staff_b)
        self.assertEqual(b.post("/api/v1/refunds/", {"payment": p.pk, "amount": "10", "reason": "x"}, format="json").status_code, 404)
        r = s.post("/api/v1/refunds/", {"payment": p.pk, "amount": "200.00", "reason": "duplicate"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        rid = r.json()["id"]
        self.assertEqual(m.post(f"/api/v1/refunds/{rid}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(o.post(f"/api/v1/refunds/{rid}/approve/", {"note": "fine"}, format="json").status_code, 200)
        self.assertEqual(s.post(f"/api/v1/refunds/{rid}/mark-paid/", {}, format="json").status_code, 403)
        done = m.post(f"/api/v1/refunds/{rid}/mark-paid/", {}, format="json")
        self.assertEqual((done.status_code, done.json()["status"]), (200, "paid"))
        self.assertEqual(b.get("/api/v1/refunds/").json()["count"], 0)


class OverpaymentTests(FinBase):
    def test_exception_flow_preserves_everything_and_creates_no_payment(self):
        inv = self.invoice(self.conn())
        now = timezone.now()
        exc = fs.record_overpayment(actor=self.staff, office=self.a, method="easypaisa", amount_received="2000.00",
                                    transaction_at=now, reference=" ep77 ", invoice=inv, notes="screenshot seen on phone")
        self.assertEqual((exc.status, exc.reference, exc.amount_expected), ("open", "EP77", D("1500.00")))
        self.assertEqual(Payment.objects.count(), 0)  # nothing auto-applied or credited
        inv.refresh_from_db()
        self.assertEqual(inv.status, "open")
        with self.assertRaises(ConflictError):
            fs.record_overpayment(actor=self.staff, office=self.a, method="easypaisa", amount_received="2000.00",
                                  transaction_at=now, reference="EP77")
        with self.assertRaises(BusinessRuleError):  # not actually an overpayment
            fs.record_overpayment(actor=self.staff, office=self.a, method="jazzcash", amount_received="1500.00",
                                  transaction_at=now, reference="J1", invoice=inv)
        with self.assertRaises(PermissionDenied):
            fs.decide_overpayment(actor=self.mgr, exception=exc, decision="refund", note="x")
        with self.assertRaises(BusinessRuleError):
            fs.decide_overpayment(actor=self.owner, exception=exc, decision="refund", note="")
        fs.decide_overpayment(actor=self.owner, exception=exc, decision="refund", note="return the 500 excess")
        with self.assertRaises(BusinessRuleError):
            fs.decide_overpayment(actor=self.owner, exception=exc, decision="settlement", note="changed my mind")
        done = fs.mark_overpayment_refunded(actor=self.mgr, exception=exc, method="easypaisa")
        self.assertEqual((done.status, done.decided_by, done.refunded_by), ("refunded", self.owner, self.mgr))
        self.assertTrue({"overpayment.recorded", "overpayment.refund_approved", "overpayment.refunded"}
                        <= set(AuditLog.objects.values_list("action", flat=True)))

    def test_settlement_cannot_be_marked_refunded(self):
        exc = fs.record_overpayment(actor=self.staff, office=self.a, method="cash", amount_received="9999.00", transaction_at=timezone.now())
        fs.decide_overpayment(actor=self.owner, exception=exc, decision="settlement", note="applies to next two months by agreement")
        with self.assertRaises(BusinessRuleError):
            fs.mark_overpayment_refunded(actor=self.mgr, exception=exc, method="cash")

    def test_http_scope_and_permissions(self):
        s, m, o, b = self.api(self.staff), self.api(self.mgr), self.api(self.owner), self.api(self.staff_b)
        body = {"office": self.a.pk, "method": "bank_transfer", "amount_received": "5000.00", "reference": "BK9",
                "transaction_at": timezone.now().isoformat(), "notes": "bank slip"}
        self.assertEqual(b.post("/api/v1/overpayments/", body, format="json").status_code, 404)
        r = s.post("/api/v1/overpayments/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        eid = r.json()["id"]
        self.assertEqual(m.post(f"/api/v1/overpayments/{eid}/decide/", {"decision": "refund", "note": "x"}, format="json").status_code, 403)
        self.assertEqual(o.post(f"/api/v1/overpayments/{eid}/decide/", {"decision": "refund", "note": "return"}, format="json").status_code, 200)
        self.assertEqual(b.get("/api/v1/overpayments/").json()["count"], 0)


class CashDepositTests(FinBase):
    def setUp(self):
        super().setUp()
        self.col2 = make_user(Role.COLLECTOR, [self.a])

    def pay(self, collector, method="cash"):
        inv = self.invoice(self.conn())
        if method == "cash":
            return self.cash(inv, actor=collector)
        p = bs.record_payment(actor=collector, invoice=inv, method=method, amount=inv.total_amount, reference=f"R{inv.pk}")
        return bs.verify_payment(actor=self.mgr, payment=p)

    def test_only_own_confirmed_undeposited_cash_is_included(self):
        p1, p2 = self.pay(self.collector), self.pay(self.collector)
        other = self.pay(self.col2)
        self.pay(self.collector, method="easypaisa")  # digital never goes through cash deposit
        with self.assertRaises(BusinessRuleError):
            fs.create_deposit(actor=self.collector, office=self.a, payment_ids=[p1.pk, other.pk])  # not theirs
        self.assertEqual(CashDeposit.objects.count(), 0)
        dep = fs.create_deposit(actor=self.collector, office=self.a, declared_amount="2900.00")
        self.assertEqual((dep.number, dep.expected_amount, dep.declared_amount, dep.items.count()), ("AAA-DEP-000001", D("3000.00"), D("2900.00"), 2))
        with self.assertRaises(BusinessRuleError):  # nothing left: no double counting
            fs.create_deposit(actor=self.collector, office=self.a)
        with self.assertRaises(BusinessRuleError):
            fs.create_deposit(actor=self.collector, office=self.a, payment_ids=[p1.pk])
        fs.create_deposit(actor=self.col2, office=self.a)

    def test_reconciliation_exact_and_with_variance(self):
        self.pay(self.collector)
        dep = fs.create_deposit(actor=self.collector, office=self.a)
        with self.assertRaises(BusinessRuleError):
            fs.reconcile_deposit(actor=self.collector, deposit=dep, received_amount="1500.00")  # can't count own cash
        with self.assertRaises(BusinessRuleError):
            fs.reconcile_deposit(actor=self.mgr, deposit=dep, received_amount="1400.00")  # variance needs a note
        done = fs.reconcile_deposit(actor=self.mgr, deposit=dep, received_amount="1400.00", variance_note="Rs100 short, collector to explain")
        self.assertEqual((done.status, done.variance, done.reconciled_by), ("reconciled", D("-100.00"), self.mgr))
        with self.assertRaises(BusinessRuleError):
            fs.reconcile_deposit(actor=self.mgr, deposit=dep, received_amount="1500.00")
        with self.assertRaises(BusinessRuleError):
            fs.cancel_deposit(actor=self.mgr, deposit=dep, reason="x")
        self.pay(self.collector)
        dep2 = fs.create_deposit(actor=self.collector, office=self.a)
        ok = fs.reconcile_deposit(actor=self.mgr, deposit=dep2, received_amount="1500.00")
        self.assertEqual(ok.variance, D("0.00"))
        self.assertTrue(AuditLog.objects.filter(action="deposit.reconciled_with_variance").exists())

    def test_cancel_releases_cash_for_a_new_deposit(self):
        p = self.pay(self.collector)
        dep = fs.create_deposit(actor=self.collector, office=self.a)
        with self.assertRaises(BusinessRuleError):
            fs.cancel_deposit(actor=self.mgr, deposit=dep, reason="")
        fs.cancel_deposit(actor=self.mgr, deposit=dep, reason="miscounted")
        dep.refresh_from_db()
        self.assertEqual(dep.status, "cancelled")
        again = fs.create_deposit(actor=self.collector, office=self.a, payment_ids=[p.pk])
        self.assertEqual(again.number, "AAA-DEP-000002")
        self.assertEqual(DepositItem.objects.filter(payment=p).count(), 2)  # history kept

    def test_database_blocks_double_counting(self):
        p = self.pay(self.collector)
        d1 = fs.create_deposit(actor=self.collector, office=self.a)
        d2 = CashDeposit.objects.create(number="X-1", office=self.a, collector=self.collector, expected_amount=1, declared_amount=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DepositItem.objects.create(deposit=d2, payment=p, amount=p.amount)

    def test_daily_summary_keeps_categories_separate(self):
        self.pay(self.collector); self.pay(self.collector)
        self.pay(self.collector, method="easypaisa")
        inv = self.invoice(self.conn())
        bs.record_payment(actor=self.staff, invoice=inv, method="jazzcash", amount=inv.total_amount, reference="PEND1")
        m = self.api(self.mgr)
        row = m.get("/api/v1/cash/summary/").json()["results"][0]
        self.assertEqual((row["office"], row["cash_collected"], row["digital_verified"], row["digital_pending_verification"],
                          row["cash_undeposited_total"]), ("AAA", "3000.00", "1500.00", "1500.00", "3000.00"))
        dep = fs.create_deposit(actor=self.collector, office=self.a)
        row = m.get("/api/v1/cash/summary/").json()["results"][0]
        self.assertEqual((row["cash_undeposited_total"], row["deposits_submitted_awaiting_count"],
                          row["deposits_submitted_awaiting_amount"]), ("0.00", 1, "3000.00"))
        fs.reconcile_deposit(actor=self.mgr, deposit=dep, received_amount="2950.00", variance_note="short")
        row = m.get("/api/v1/cash/summary/").json()["results"][0]
        self.assertEqual((row["deposits_reconciled_expected"], row["deposits_reconciled_received"], row["cash_variance"]),
                         ("3000.00", "2950.00", "-50.00"))
        self.assertEqual(self.api(self.staff_b).get("/api/v1/cash/summary/").json()["results"][0]["cash_collected"], "0.00")
        self.assertEqual(m.get("/api/v1/cash/summary/?date=bad").status_code, 400)

    def test_http_flow_and_isolation(self):
        p = self.pay(self.collector)
        col, m, b = self.api(self.collector), self.api(self.mgr), self.api(self.staff_b)
        self.assertEqual(b.post("/api/v1/cash/deposits/", {"office": self.a.pk}, format="json").status_code, 404)
        r = col.post("/api/v1/cash/deposits/", {"office": self.a.pk, "payments": [p.pk]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        n = r.json()["number"]
        self.assertEqual(col.post(f"/api/v1/cash/deposits/{n}/reconcile/", {"received_amount": "1500"}, format="json").status_code, 403)
        self.assertEqual(b.get(f"/api/v1/cash/deposits/{n}/").status_code, 404)
        ok = m.post(f"/api/v1/cash/deposits/{n}/reconcile/", {"received_amount": "1500.00"}, format="json")
        self.assertEqual((ok.status_code, ok.json()["variance"]), (200, "0.00"))


@skipUnless(dbconn.vendor == "postgresql", "needs PostgreSQL row locks")
class FinanceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.a = make_office("AAA"); self.va = make_village(self.a, "KHT")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.col = make_user(Role.COLLECTOR, [self.a])
        self.owner = make_user(Role.OWNER)
        self.pkg = make_package()

    def _cash_payment(self, actor):
        c = cs.create_connection(actor=self.staff, customer=new_customer(self.staff, self.va, self.a), package=self.pkg, installation_address="x")
        inv = bs.create_invoice(actor=self.staff, connection=c)[0]
        return bs.record_payment(actor=actor, invoice=inv, method="cash", amount=inv.total_amount)

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

    def test_parallel_deposits_never_double_count(self):
        for _ in range(3):
            self._cash_payment(self.col)
        out, errs = self._run(lambda: fs.create_deposit(actor=self.col, office=self.a), 4)
        self.assertEqual(len(out), 1)
        self.assertTrue(all(isinstance(e, BusinessRuleError) for e in errs))
        self.assertEqual(DepositItem.objects.filter(released_at__isnull=True).count(), 3)
        self.assertEqual(CashDeposit.objects.count(), 1)

    def test_parallel_refund_requests_cannot_exceed_payment(self):
        p = self._cash_payment(self.staff)  # 1500
        out, errs = self._run(lambda: fs.request_refund(actor=self.staff, payment=p, amount="1000.00", reason="x"), 4)
        self.assertEqual(len(out), 1)
        self.assertEqual(sum(r.amount for r in Refund.objects.all()), D("1000.00"))

    def test_parallel_correction_approvals_reverse_once(self):
        p = self._cash_payment(self.staff)
        corr = fs.request_correction(actor=self.staff, payment=p, reason="wrong")
        out, errs = self._run(lambda: fs.approve_correction(actor=self.owner, correction=corr), 4)
        self.assertEqual(len(out), 1)
        self.assertEqual(Payment.objects.get(pk=p.pk).status, "reversed")
        self.assertEqual(BillingTerm.objects.filter(reversed_at__isnull=False).count(), 1)
