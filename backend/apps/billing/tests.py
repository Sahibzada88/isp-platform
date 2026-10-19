import threading
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from django.db import IntegrityError, connection as dbconn, transaction
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing import services as bs
from apps.billing.models import BillingTerm, Invoice, InvoiceLine, Payment, Receipt
from apps.core.exceptions import BusinessRuleError, ConflictError
from apps.core.settings_store import set_setting
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.customers.models import Connection
from apps.organization.models import Office

D = Decimal
_phone = iter(range(10**9, 10**10))


def new_customer(actor, village, office):
    return cs.register_customer(actor=actor, confirm_duplicates=True, data={
        "full_name": "Ali Khan", "phone": f"0{next(_phone)}", "cnic": "", "permanent_address": "x",
        "village": village, "office": office})


class Base(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.owner = make_user(Role.OWNER)
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.collector = make_user(Role.COLLECTOR, [self.a])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.pkg = make_package("P10", "1500.00", 10)

    def conn(self, package=None, village=None, office=None):
        village, office = village or self.va, office or self.a
        cust = new_customer(self.staff, village, office)
        return cs.create_connection(actor=self.staff, customer=cust, package=package or self.pkg, installation_address="Shop")

    def active_conn(self, days=10, package=None):
        c = self.conn(package)
        now = timezone.now()
        Connection.objects.filter(pk=c.pk).update(
            status="active", expires_at=now + timedelta(days=days), installation_completed_at=now - timedelta(days=20),
            initial_payment_confirmed_at=now - timedelta(days=20), activation_approved_at=now - timedelta(days=20))
        return Connection.objects.get(pk=c.pk)

    def expired_conn(self):
        c = self.active_conn()
        Connection.objects.filter(pk=c.pk).update(status="expired", expires_at=timezone.now() - timedelta(days=2))
        return Connection.objects.get(pk=c.pk)

    def invoice(self, conn, actor=None):
        return bs.create_invoice(actor=actor or self.staff, connection=conn)[0]

    def cash(self, inv, actor=None):
        return bs.record_payment(actor=actor or self.staff, invoice=inv, method="cash", amount=inv.total_amount)

    def api(self, user):
        c = APIClient(); login(c, user); return c


class InvoiceTests(Base):
    def test_numbering_is_office_specific_and_sequential(self):
        i1, i2 = self.invoice(self.conn()), self.invoice(self.conn())
        ib = self.invoice(self.conn(village=self.vb, office=self.b), actor=self.staff_b)
        self.assertEqual([i1.number, i2.number, ib.number], ["AAA-INV-000001", "AAA-INV-000002", "BBB-INV-000001"])

    def test_existing_invoice_is_reopened_not_duplicated(self):
        c = self.conn()
        inv, created = bs.create_invoice(actor=self.staff, connection=c)
        again, created2 = bs.create_invoice(actor=self.staff, connection=c)
        self.assertEqual((created, created2, inv.pk), (True, False, again.pk))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_price_snapshot_survives_package_price_change(self):
        c = self.conn()
        inv = self.invoice(c)
        self.pkg.price_pkr = D("9999.00"); self.pkg.name = "Renamed"; self.pkg.save()
        inv.refresh_from_db()
        self.assertEqual((inv.package_price, inv.total_amount, inv.package_name), (D("1500.00"), D("1500.00"), "10 Mbps"))
        self.assertEqual(inv.lines.get().amount, D("1500.00"))
        # a *new* invoice for another connection uses the new price
        self.assertEqual(self.invoice(self.conn()).total_amount, D("9999.00"))

    def test_installation_fee_included_once(self):
        set_setting("installation_fee_pkr", "500.00")
        c = self.conn()
        inv = self.invoice(c)
        self.assertEqual((inv.installation_fee, inv.total_amount), (D("500.00"), D("2000.00")))
        self.assertEqual(sorted(inv.lines.values_list("kind", flat=True)), ["installation_fee", "package"])
        re = bs.reissue_invoice(actor=self.mgr, invoice=inv, reason="typo")
        self.assertEqual(re.total_amount, D("2000.00"))  # voided invoice's fee is re-charged exactly once
        self.assertEqual(InvoiceLine.objects.filter(kind="installation_fee").exclude(invoice__status="void").count(), 1)
        self.cash(re)
        # the initial payment is settled, so a second initial invoice is refused: the fee can never repeat
        with self.assertRaises(BusinessRuleError):
            bs.create_invoice(actor=self.staff, connection=Connection.objects.get(pk=c.pk))
        self.assertEqual(InvoiceLine.objects.filter(kind="installation_fee").count(), 2)  # voided + reissued
        self.assertEqual(InvoiceLine.objects.filter(kind="installation_fee", invoice__status="paid").count(), 1)

    def test_fee_not_repeated_on_renewal_and_waiver(self):
        set_setting("installation_fee_pkr", "500.00")
        c = self.active_conn()
        self.assertEqual(self.invoice(c).installation_fee, D("0.00"))
        c2 = self.conn()
        bs.waive_installation_fee(actor=self.mgr, connection=c2, reason="promo for relative")
        self.assertEqual(self.invoice(c2).installation_fee, D("0.00"))
        c3 = self.conn()
        self.invoice(c3)
        with self.assertRaises(BusinessRuleError):  # already on an invoice
            bs.waive_installation_fee(actor=self.mgr, connection=c3, reason="late")
        with self.assertRaises(BusinessRuleError):
            bs.waive_installation_fee(actor=self.mgr, connection=self.conn(), reason="")
        self.assertTrue(AuditLog.objects.filter(action="connection.installation_fee_waived", reason="promo for relative").exists())

    def test_void_and_reissue_with_current_price(self):
        c = self.conn()
        inv = self.invoice(c)
        with self.assertRaises(BusinessRuleError):
            bs.void_invoice(actor=self.mgr, invoice=inv, reason="")
        self.pkg.price_pkr = D("1800.00"); self.pkg.save()
        new = bs.reissue_invoice(actor=self.mgr, invoice=inv, reason="price corrected")
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.void_reason, new.total_amount, new.supersedes_id), ("void", "price corrected", D("1800.00"), inv.pk))
        self.assertNotEqual(inv.number, new.number)
        self.assertEqual(inv.total_amount, D("1500.00"))  # original never overwritten
        self.cash(new)
        with self.assertRaises(BusinessRuleError):  # paid invoices need the approved-correction workflow
            bs.void_invoice(actor=self.mgr, invoice=new, reason="oops")

    def test_cannot_invoice_cancelled_or_suspended(self):
        c = self.active_conn()
        cs.cancel_connection(actor=self.mgr, connection=c, reason="left")
        with self.assertRaises(BusinessRuleError):
            self.invoice(Connection.objects.get(pk=c.pk))

    def test_database_constraints_on_invoices(self):
        inv = self.invoice(self.conn())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=inv.pk).update(total_amount=D("1.00"))  # total != price + fee
        with self.assertRaises(ProtectedError):
            inv.delete()
        c2 = self.conn()
        self.invoice(c2)
        with self.assertRaises(IntegrityError), transaction.atomic():  # second unsettled invoice, same connection
            Invoice.objects.create(number="X-1", office=self.a, customer=c2.customer, connection=c2, kind="initial",
                                   package=self.pkg, package_code="P", package_name="p", speed_mbps=1,
                                   package_price=D("5"), installation_fee=D("0"), total_amount=D("5"), created_by=self.staff)

    def test_office_code_change_keeps_document_numbers_and_avoids_collisions(self):
        i1 = self.invoice(self.conn())
        Office.objects.filter(pk=self.a.pk).update(code="ZZZ")
        self.a.refresh_from_db()
        self.assertEqual(self.invoice(self.conn()).number, "AAA-INV-000002")
        new_office = make_office("AAA")  # takes the old code
        self.assertNotEqual(new_office.doc_prefix, "AAA")
        self.assertEqual(i1.number, "AAA-INV-000001")


class CashPaymentTests(Base):
    def test_cash_payment_confirms_but_does_not_activate(self):
        c = self.conn()
        p = self.cash(self.invoice(c))
        c.refresh_from_db()
        self.assertEqual((p.status, p.receipt.number, p.receipt.status), ("confirmed", "AAA-RCP-000001", "valid"))
        self.assertEqual(p.invoice.status, "paid")
        self.assertEqual(c.status, "pending_installation")
        self.assertEqual(c.expires_at, c.initial_payment_confirmed_at + timedelta(days=30))
        self.assertEqual(c.terms.count(), 1)

    def test_full_activation_flow_with_all_three_gates(self):
        c = self.conn()
        with self.assertRaises(BusinessRuleError):  # nothing done yet
            cs.approve_activation(actor=self.mgr, connection=c)
        self.cash(self.invoice(c))
        with self.assertRaises(BusinessRuleError):  # paid, but installation not complete
            cs.approve_activation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        cs.complete_installation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        active = cs.approve_activation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        self.assertEqual(active.status, "active")
        self.assertEqual(active.expires_at, active.initial_payment_confirmed_at + timedelta(days=30))

    def test_installation_and_approval_without_payment_never_activate(self):
        c = self.conn()
        cs.complete_installation(actor=self.mgr, connection=c)
        with self.assertRaises(BusinessRuleError):
            cs.approve_activation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))

    def test_partial_and_over_payment_rejected_without_side_effects(self):
        inv = self.invoice(self.conn())
        for amount in ("1000.00", "1500.01"):
            with self.assertRaises(BusinessRuleError):
                bs.record_payment(actor=self.staff, invoice=inv, method="cash", amount=amount)
        self.assertEqual((Payment.objects.count(), Receipt.objects.count()), (0, 0))
        inv.refresh_from_db()
        self.assertEqual(inv.status, "open")

    def test_second_payment_on_paid_invoice_rejected(self):
        inv = self.invoice(self.conn()); self.cash(inv)
        with self.assertRaises(BusinessRuleError):
            self.cash(inv)
        self.assertEqual(Payment.objects.count(), 1)

    def test_payment_after_connection_cancelled_is_refused(self):
        c = self.conn()
        inv = self.invoice(c)
        cs.cancel_connection(actor=self.mgr, connection=c, reason="changed mind")
        with self.assertRaises(BusinessRuleError):
            self.cash(inv)
        self.assertEqual(Payment.objects.count(), 0)

    def test_future_transaction_time_rejected(self):
        inv = self.invoice(self.conn())
        with self.assertRaises(BusinessRuleError):
            bs.record_payment(actor=self.staff, invoice=inv, method="easypaisa", amount=inv.total_amount,
                              reference="T1", paid_at=timezone.now() + timedelta(days=1))

    def test_audit_trail_written(self):
        inv = self.invoice(self.conn()); p = self.cash(inv)
        actions = set(AuditLog.objects.filter(entity_id=str(p.pk), entity_type="billing.payment").values_list("action", flat=True))
        self.assertEqual(actions, {"payment.recorded", "payment.confirmed"})


class DigitalPaymentTests(Base):
    def digital(self, inv, ref="TX100", method="easypaisa", actor=None):
        return bs.record_payment(actor=actor or self.staff, invoice=inv, method=method, amount=inv.total_amount, reference=ref)

    def test_pending_until_verified_and_nothing_applied_meanwhile(self):
        c = self.conn(); inv = self.invoice(c)
        p = self.digital(inv, " tx100 ")
        c.refresh_from_db(); inv.refresh_from_db()
        self.assertEqual((p.status, p.reference, inv.status, p.receipt.status), ("pending_verification", "TX100", "payment_pending", "valid"))
        self.assertIsNone(c.initial_payment_confirmed_at)
        with self.assertRaises(BusinessRuleError):  # no double payment while one is pending
            self.cash(inv)
        with self.assertRaises(BusinessRuleError):  # maker-checker
            bs.verify_payment(actor=self.staff, payment=p)
        bs.verify_payment(actor=self.mgr, payment=p)
        c.refresh_from_db(); p.refresh_from_db()
        self.assertEqual((p.status, p.verified_by, c.initial_payment_confirmed_at is not None), ("confirmed", self.mgr, True))
        self.assertEqual(c.status, "pending_installation")  # verification alone never activates

    def test_reference_required_and_duplicates_blocked(self):
        i1, i2 = self.invoice(self.conn()), self.invoice(self.conn())
        with self.assertRaises(BusinessRuleError):
            self.digital(i1, ref="")
        self.digital(i1, "ABC123")
        with self.assertRaises(ConflictError):
            self.digital(i2, " abc123 ")
        self.digital(i2, "ABC123", method="jazzcash")  # same text, different channel is a different transaction id space
        self.assertEqual(Payment.objects.count(), 2)

    def test_rejection_invalidates_receipt_reopens_invoice_and_frees_reference(self):
        inv = self.invoice(self.conn())
        p = self.digital(inv, "FAKE1")
        with self.assertRaises(BusinessRuleError):
            bs.reject_payment(actor=self.mgr, payment=p, reason="")
        bs.reject_payment(actor=self.mgr, payment=p, reason="not found in statement")
        p.refresh_from_db(); inv.refresh_from_db()
        self.assertEqual((p.status, p.receipt.status, inv.status), ("rejected", "invalidated", "open"))
        self.assertTrue(Receipt.objects.filter(pk=p.receipt.pk).exists())  # kept, not deleted
        p2 = self.digital(inv, "FAKE1")  # corrected resubmission is possible
        self.assertEqual(p2.receipt.number, "AAA-RCP-000002")  # numbers never reused
        with self.assertRaises(BusinessRuleError):
            bs.verify_payment(actor=self.mgr, payment=p)  # a rejected payment can't be verified

    def test_owner_may_verify_own_entry_but_database_blocks_two_live_payments(self):
        inv = self.invoice(self.conn())
        p = self.digital(inv, "OWN1", actor=self.owner)
        bs.verify_payment(actor=self.owner, payment=p)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(invoice=inv, office=self.a, customer=inv.customer, connection=inv.connection,
                                   method="cash", amount=inv.total_amount, status="confirmed", paid_at=timezone.now(),
                                   entered_by=self.staff)

    def test_verification_fails_safely_if_connection_cancelled_meanwhile(self):
        c = self.conn(); inv = self.invoice(c)
        p = self.digital(inv, "LATE1")
        cs.cancel_connection(actor=self.mgr, connection=c, reason="x")
        with self.assertRaises(BusinessRuleError):
            bs.verify_payment(actor=self.mgr, payment=p)
        p.refresh_from_db()
        self.assertEqual(p.status, "pending_verification")  # kept for manual review, nothing applied


class RenewalTests(Base):
    def test_early_renewal_starts_after_current_term(self):
        c = self.active_conn(days=5)
        old = c.expires_at
        self.cash(self.invoice(c))
        c.refresh_from_db()
        term = c.terms.get()
        self.assertEqual((term.starts_at, term.ends_at), (old, old + timedelta(days=30)))
        self.assertEqual(c.expires_at, old + timedelta(days=30))  # paid period not shortened
        self.assertEqual(c.status, "active")

    def test_package_change_applies_when_prepaid_term_starts(self):
        c = self.active_conn(days=5)
        p20 = make_package("P20", "2500.00", 20)
        cs.schedule_package_change(actor=self.mgr, connection=c, package=p20)
        inv = self.invoice(Connection.objects.get(pk=c.pk))
        self.assertEqual(inv.total_amount, D("2500.00"))
        self.cash(inv)
        c.refresh_from_db()
        self.assertEqual((c.package.code, c.next_package.code), ("P10", "P20"))  # current term keeps old package
        start = c.terms.get().starts_at
        self.assertEqual(bs.apply_due_terms(now=start - timedelta(hours=1)), 0)
        self.assertEqual(bs.apply_due_terms(now=start + timedelta(hours=1)), 1)
        self.assertEqual(bs.apply_due_terms(now=start + timedelta(hours=2)), 0)  # idempotent
        c.refresh_from_db()
        self.assertEqual((c.package.code, c.next_package), ("P20", None))

    def test_expired_connection_needs_staff_confirmation(self):
        c = self.expired_conn()
        self.cash(self.invoice(c))
        c.refresh_from_db()
        self.assertEqual(c.status, "expired")  # payment alone does not reactivate
        self.assertEqual(cs.process_expiries(), 0)
        confirmed = bs.confirm_renewal(actor=self.mgr, connection=c)
        self.assertEqual(confirmed.status, "active")
        self.assertEqual(confirmed.expires_at, confirmed.terms.latest("starts_at").ends_at)
        with self.assertRaises(BusinessRuleError):
            bs.confirm_renewal(actor=self.mgr, connection=confirmed)

    def test_confirm_renewal_without_payment_fails(self):
        with self.assertRaises(BusinessRuleError):
            bs.confirm_renewal(actor=self.mgr, connection=self.expired_conn())

    def test_renewal_invoice_uses_current_price_at_issue_time(self):
        c = self.active_conn()
        self.pkg.price_pkr = D("1700.00"); self.pkg.save()
        self.assertEqual(self.invoice(c).total_amount, D("1700.00"))


class DocumentTests(Base):
    def test_pdfs(self):
        inv = self.invoice(self.conn()); p = self.cash(inv)
        c = self.api(self.staff)
        r = c.get(f"/api/v1/invoices/{inv.number}/pdf/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        r = c.get(f"/api/v1/receipts/{p.receipt.number}/pdf/")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn(b"226.7", r.content)  # 80 mm thermal roll width
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_invalidated_and_void_documents_still_render(self):
        inv = self.invoice(self.conn())
        p = bs.record_payment(actor=self.staff, invoice=inv, method="bank_transfer", amount=inv.total_amount, reference="B1")
        bs.reject_payment(actor=self.mgr, payment=p, reason="bounced")
        c = self.api(self.staff)
        self.assertEqual(c.get(f"/api/v1/receipts/{p.receipt.number}/").json()["status"], "invalidated")
        self.assertEqual(c.get(f"/api/v1/receipts/{p.receipt.number}/pdf/").status_code, 200)
        bs.void_invoice(actor=self.mgr, invoice=inv, reason="dup")
        self.assertEqual(c.get(f"/api/v1/invoices/{inv.number}/pdf/").status_code, 200)


class ApiTests(Base):
    def test_end_to_end_over_http(self):
        c = self.api(self.staff); m = self.api(self.mgr)
        conn = self.conn()
        r = c.post("/api/v1/invoices/", {"connection": conn.connection_id}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(c.post("/api/v1/invoices/", {"connection": conn.connection_id}, format="json").status_code, 200)
        number, total = r.json()["number"], r.json()["total_amount"]
        bad = c.post("/api/v1/payments/", {"invoice": number, "method": "cash", "amount": "10.00"}, format="json")
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, "business_rule_violation"))
        ok = c.post("/api/v1/payments/", {"invoice": number, "method": "easypaisa", "amount": total, "reference": "EP9"}, format="json")
        self.assertEqual((ok.status_code, ok.json()["status"], ok.json()["receipt_status"]), (201, "pending_verification", "valid"))
        dup = c.post("/api/v1/payments/", {"invoice": number, "method": "easypaisa", "amount": total, "reference": "EP9"}, format="json")
        self.assertEqual(dup.status_code, 400)  # invoice already has a payment awaiting verification
        self.assertEqual(c.post(f"/api/v1/payments/{ok.json()['id']}/verify/").status_code, 403)  # staff cannot verify
        v = m.post(f"/api/v1/payments/{ok.json()['id']}/verify/")
        self.assertEqual((v.status_code, v.json()["status"]), (200, "confirmed"))
        self.assertEqual(c.get("/api/v1/payments/?status=confirmed").json()["count"], 1)
        self.assertEqual(c.get(f"/api/v1/receipts/?search={ok.json()['receipt']}").json()["count"], 1)
        # activation over HTTP
        self.assertEqual(m.post(f"/api/v1/connections/{conn.connection_id}/complete-installation/").status_code, 200)
        a = m.post(f"/api/v1/connections/{conn.connection_id}/approve-activation/")
        self.assertEqual((a.status_code, a.json()["status"]), (200, "active"))

    def test_cross_office_isolation(self):
        inv = self.invoice(self.conn()); p = self.cash(inv)
        b = self.api(self.staff_b)
        for url in (f"/api/v1/invoices/{inv.number}/", f"/api/v1/invoices/{inv.number}/pdf/",
                    f"/api/v1/receipts/{p.receipt.number}/", f"/api/v1/receipts/{p.receipt.number}/pdf/",
                    f"/api/v1/payments/{p.pk}/"):
            self.assertEqual(b.get(url).status_code, 404, url)
        self.assertEqual(b.get("/api/v1/invoices/").json()["count"], 0)
        conn = self.active_conn()
        self.assertEqual(b.post("/api/v1/invoices/", {"connection": conn.connection_id}, format="json").status_code, 404)
        inv2 = self.invoice(conn)
        self.assertEqual(b.post("/api/v1/payments/", {"invoice": inv2.number, "method": "cash", "amount": "1500.00"}, format="json").status_code, 404)
        self.assertEqual(Payment.objects.count(), 1)

    def test_permissions(self):
        tech = make_user(Role.TECHNICIAN, [self.a])
        self.assertEqual(self.api(tech).get("/api/v1/invoices/").status_code, 403)
        inv = self.invoice(self.conn())
        self.assertEqual(self.api(self.staff).post(f"/api/v1/invoices/{inv.number}/void/", {"reason": "x"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/invoices/{inv.number}/void/", {"reason": "x"}, format="json").status_code, 200)
        conn = self.conn(); inv = self.invoice(conn)
        r = self.api(self.collector).post("/api/v1/payments/", {"invoice": inv.number, "method": "cash", "amount": "1500.00"}, format="json")
        self.assertEqual(r.status_code, 201)  # collectors record cash
        self.assertEqual(self.api(self.staff).post(f"/api/v1/connections/{conn.connection_id}/waive-installation-fee/", {"reason": "x"}, format="json").status_code, 403)

    def test_billing_settings_owner_only_and_audited(self):
        self.assertEqual(self.api(self.mgr).patch("/api/v1/settings/billing/", {"installation_fee_pkr": "1"}, format="json").status_code, 403)
        o = self.api(self.owner)
        self.assertEqual(o.patch("/api/v1/settings/billing/", {"installation_fee_pkr": "750.00"}, format="json").status_code, 200)
        self.assertEqual(o.get("/api/v1/settings/billing/").json()["installation_fee_pkr"], "750.00")
        self.assertEqual(self.invoice(self.conn()).installation_fee, D("750.00"))
        self.assertTrue(AuditLog.objects.filter(action="settings.changed").exists())
        self.assertEqual(o.patch("/api/v1/settings/billing/", {"installation_fee_pkr": "-5"}, format="json").status_code, 400)

    def test_cron_applies_terms_and_expires(self):
        from django.test import override_settings
        with override_settings(CRON_SECRET="s"):
            c = self.api(self.staff)
            r = APIClient().get("/api/v1/jobs/expire-connections/", HTTP_AUTHORIZATION="Bearer s")
            self.assertEqual((r.status_code, r.json()["terms_applied"]), (200, 0))


@skipUnless(dbconn.vendor == "postgresql", "needs PostgreSQL row locks")
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.a = make_office("AAA"); self.va = make_village(self.a, "KHT")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.pkg = make_package()

    def _conn(self):
        return cs.create_connection(actor=self.staff, customer=new_customer(self.staff, self.va, self.a),
                                    package=self.pkg, installation_address="x")

    def _run(self, fn, n):
        out, errs = [], []

        def worker():
            try:
                out.append(fn())
            except Exception as exc:  # noqa: BLE001
                errs.append(exc)
            finally:
                dbconn.close()

        ts = [threading.Thread(target=worker) for _ in range(n)]
        [t.start() for t in ts]; [t.join() for t in ts]
        return out, errs

    def test_parallel_invoice_creation_yields_one_invoice(self):
        c = self._conn()
        out, errs = self._run(lambda: bs.create_invoice(actor=self.staff, connection=c), 6)
        self.assertEqual(errs, [])
        self.assertEqual(sum(1 for _, created in out if created), 1)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_parallel_cash_payments_on_one_invoice_yield_one_payment(self):
        inv = bs.create_invoice(actor=self.staff, connection=self._conn())[0]
        out, errs = self._run(lambda: bs.record_payment(actor=self.staff, invoice=inv, method="cash", amount=inv.total_amount), 5)
        self.assertEqual(len(out), 1)
        self.assertTrue(all(isinstance(e, BusinessRuleError) for e in errs))
        self.assertEqual((Payment.objects.count(), Receipt.objects.count(), BillingTerm.objects.count()), (1, 1, 1))

    def test_parallel_duplicate_reference_only_one_wins(self):
        invs = [bs.create_invoice(actor=self.staff, connection=self._conn())[0] for _ in range(2)]
        it = iter(invs)
        lock = threading.Lock()

        def pay():
            with lock:
                inv = next(it)
            return bs.record_payment(actor=self.staff, invoice=inv, method="jazzcash", amount=inv.total_amount, reference="SAME")

        out, errs = self._run(pay, 2)
        self.assertEqual((len(out), len(errs)), (1, 1))
        self.assertIsInstance(errs[0], ConflictError)
        self.assertEqual(Payment.objects.filter(reference="SAME", status="pending_verification").count(), 1)

    def test_parallel_receipt_numbers_are_unique(self):
        invs = [bs.create_invoice(actor=self.staff, connection=self._conn())[0] for _ in range(6)]
        it = iter(invs); lock = threading.Lock()

        def pay():
            with lock:
                inv = next(it)
            return bs.record_payment(actor=self.staff, invoice=inv, method="cash", amount=inv.total_amount).receipt.number

        out, errs = self._run(pay, 6)
        self.assertEqual(errs, [])
        self.assertEqual(sorted(out), [f"AAA-RCP-{n:06d}" for n in range(1, 7)])
