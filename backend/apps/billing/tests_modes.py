from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing import services as bs
from apps.billing.models import Invoice
from apps.billing.tests import Base, D, new_customer
from apps.core import fiscal
from apps.core.exceptions import BusinessRuleError
from apps.core.settings_store import set_setting
from apps.core.testing import make_user
from apps.customers import services as cs
from apps.customers.models import Connection
from apps.finance import services as fs
from apps.imports.models import OpeningBalance


class SeparateInstallationFeeTests(Base):
    def setUp(self):
        super().setUp()
        set_setting("installation_fee_pkr", "500.00")
        set_setting("installation_fee_mode", "separate")

    def test_fee_is_its_own_invoice_and_must_be_paid_before_the_first_term(self):
        c = self.conn()
        fee = bs.create_invoice(actor=self.staff, connection=c)[0]
        self.assertEqual((fee.kind, fee.total_amount, fee.package_price, fee.installation_fee), ("installation", D("500.00"), D("0.00"), D("500.00")))
        self.assertEqual(list(fee.lines.values_list("kind", flat=True)), ["installation_fee"])
        self.assertEqual(bs.create_invoice(actor=self.staff, connection=c)[0].pk, fee.pk)          # reopened, not duplicated
        self.cash(fee)
        c.refresh_from_db()
        self.assertIsNone(c.initial_payment_confirmed_at)                                          # the fee alone is not the first term
        self.assertFalse(c.terms.exists())
        cs.complete_installation(actor=self.mgr, connection=c)
        with self.assertRaises(BusinessRuleError):
            cs.approve_activation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        first, created = bs.create_invoice(actor=self.staff, connection=Connection.objects.get(pk=c.pk))
        self.assertEqual((created, first.kind, first.total_amount, first.installation_fee), (True, "initial", D("1500.00"), D("0.00")))
        self.cash(first)
        active = cs.approve_activation(actor=self.mgr, connection=Connection.objects.get(pk=c.pk))
        self.assertEqual(active.status, "active")
        self.assertEqual(Invoice.objects.filter(connection=c, status="paid").count(), 2)

    def test_never_charged_twice_and_waiver_skips_the_fee_invoice(self):
        c = self.conn()
        fee = bs.create_invoice(actor=self.staff, connection=c)[0]
        again = bs.reissue_invoice(actor=self.mgr, invoice=fee, reason="typo")
        self.assertEqual((again.kind, again.total_amount), ("installation", D("500.00")))
        from apps.billing.models import InvoiceLine
        self.assertEqual(InvoiceLine.objects.filter(kind="installation_fee").exclude(invoice__status="void").count(), 1)
        c2 = self.conn()
        bs.waive_installation_fee(actor=self.mgr, connection=c2, reason="staff relative")
        self.assertEqual(bs.create_invoice(actor=self.staff, connection=c2)[0].kind, "initial")
        with self.assertRaises(BusinessRuleError):
            bs.waive_installation_fee(actor=self.mgr, connection=c, reason="late")                 # already on an invoice

    def test_zero_fee_goes_straight_to_the_first_term_and_included_mode_is_unchanged(self):
        set_setting("installation_fee_pkr", "0")
        self.assertEqual(bs.create_invoice(actor=self.staff, connection=self.conn())[0].kind, "initial")
        set_setting("installation_fee_pkr", "500.00"); set_setting("installation_fee_mode", "included")
        inv = bs.create_invoice(actor=self.staff, connection=self.conn())[0]
        self.assertEqual((inv.kind, inv.total_amount, inv.installation_fee), ("initial", D("2000.00"), D("500.00")))

    def test_correcting_the_fee_payment_respects_the_order(self):
        c = self.conn()
        fee_pay = self.cash(bs.create_invoice(actor=self.staff, connection=c)[0])
        first_pay = self.cash(bs.create_invoice(actor=self.staff, connection=Connection.objects.get(pk=c.pk))[0])
        corr = fs.request_correction(actor=self.staff, payment=fee_pay, reason="wrong customer")
        with self.assertRaises(BusinessRuleError):
            fs.approve_correction(actor=self.mgr, correction=corr)                                  # the first term was paid afterwards
        fs.approve_correction(actor=self.mgr, correction=fs.request_correction(actor=self.staff, payment=first_pay, reason="wrong customer"))
        fs.approve_correction(actor=self.mgr, correction=corr)                                      # now in the right order
        fee_pay.refresh_from_db(); self.assertEqual(fee_pay.status, "reversed")

    def test_database_allows_one_open_invoice_per_kind_only(self):
        c = self.conn()
        inv = bs.create_invoice(actor=self.staff, connection=c)[0]
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(number="X-2", office=self.a, customer=c.customer, connection=c, kind=inv.kind, package=self.pkg, package_code="P",
                                   package_name="p", speed_mbps=1, package_price=D("0"), installation_fee=D("5"), total_amount=D("5"), created_by=self.staff)


class OpeningBalanceCollectionTests(Base):
    def setUp(self):
        super().setUp()
        self.c = self.conn()
        self.cust = self.c.customer
        self.ob = OpeningBalance.objects.create(customer=self.cust, connection=self.c, office=self.a, amount=D("2500.50"), status="verified",
                                                migration_date=timezone.localdate(), source_reference="OLD-77")

    def test_collecting_a_balance_settles_it_without_touching_the_connection(self):
        Connection.objects.filter(pk=self.c.pk).update(status="expired", expires_at=timezone.now() - timedelta(days=9))
        inv, created = bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)
        again, created2 = bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)
        self.assertEqual((created, created2, again.pk), (True, False, inv.pk))
        self.assertEqual((inv.kind, inv.total_amount, inv.other_charges, inv.package_price, inv.customer), ("opening_balance", D("2500.50"), D("2500.50"), D("0.00"), self.cust))
        self.assertIn("OLD-77", inv.lines.get().description)
        p = self.cash(inv)
        self.ob.refresh_from_db(); conn = Connection.objects.get(pk=self.c.pk)
        self.assertEqual((p.status, self.ob.status, conn.status), ("confirmed", "settled", "expired"))   # connection untouched, still expired
        self.assertFalse(conn.terms.exists())
        with self.assertRaises(BusinessRuleError):
            bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)                            # already collected
        self.assertTrue(AuditLog.objects.filter(action="opening_balance.settled").exists())

    def test_even_a_cancelled_customers_old_debt_can_be_collected(self):
        Connection.objects.filter(pk=self.c.pk).update(status="cancelled")
        inv = bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)[0]
        self.cash(inv)
        self.ob.refresh_from_db(); self.assertEqual(self.ob.status, "settled")

    def test_only_verified_balances_with_a_valid_connection(self):
        self.ob.status = "unverified"; self.ob.save()
        with self.assertRaises(BusinessRuleError):
            bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)
        self.ob.status, self.ob.connection = "verified", None; self.ob.save()
        with self.assertRaises(BusinessRuleError):
            bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)                            # no connection chosen
        other = self.conn()
        with self.assertRaises(BusinessRuleError):
            bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob, connection=other)          # someone else's connection
        self.assertEqual(bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob, connection=Connection.objects.get(pk=self.c.pk))[1], True)

    def test_coexists_with_the_normal_invoice_and_void_makes_it_collectable_again(self):
        renewal_conn = self.c
        normal = bs.create_invoice(actor=self.staff, connection=renewal_conn)[0]
        bal = bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)[0]
        self.assertNotEqual(normal.pk, bal.pk)
        self.assertEqual(bs.create_invoice(actor=self.staff, connection=Connection.objects.get(pk=self.c.pk))[0].pk, normal.pk)   # not mistaken for the balance one
        bs.void_invoice(actor=self.mgr, invoice=bal, reason="wrong amount")
        self.ob.refresh_from_db(); self.assertIsNone(self.ob.invoice_id)
        self.assertNotEqual(bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)[0].pk, bal.pk)

    def test_reversal_puts_the_balance_back(self):
        p = self.cash(bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)[0])
        fs.approve_correction(actor=self.mgr, correction=fs.request_correction(actor=self.staff, payment=p, reason="wrong customer"))
        self.ob.refresh_from_db()
        self.assertEqual((self.ob.status, self.ob.settled_at), ("verified", None))
        self.cash(Invoice.objects.get(pk=p.invoice_id))                                                      # can be collected again
        self.ob.refresh_from_db(); self.assertEqual(self.ob.status, "settled")

    def test_recovered_money_is_visible_but_kept_apart_from_new_income(self):
        self.cash(bs.create_opening_balance_invoice(actor=self.staff, balance=self.ob)[0])
        owner = make_user(Role.OWNER)
        c = APIClient(); login(c, owner)
        d = c.get("/api/v1/dashboard/").json()["collections"]
        self.assertEqual((d["month"]["cash"], d["of_which_opening_balance_recovered_this_month"]), ("2500.50", "2500.50"))
        rows = c.get("/api/v1/reports/collections/").json()
        self.assertEqual((rows["columns"][4], rows["results"][0][4]), ("Type", "opening_balance"))
        fs_ = c.get("/api/v1/reports/financial-summary/").json()
        cols = fs_["columns"]
        month = next(r for r in fs_["results"] if r[0] == timezone.localdate().strftime("%Y-%m"))
        self.assertEqual((month[cols.index("New cash collected")], month[cols.index("Opening balances recovered (old money)")]), ("0.00", "2500.50"))

    def test_http_endpoint_and_scope(self):
        mgr, staff_b = make_user(Role.MANAGER, [self.a]), make_user(Role.OFFICE_STAFF, [self.b])
        s = APIClient(); login(s, mgr)
        r = s.post(f"/api/v1/opening-balances/{self.ob.pk}/invoice/", {}, format="json")
        self.assertEqual((r.status_code, r.json()["kind"], r.json()["total_amount"]), (201, "opening_balance", "2500.50"))
        self.assertEqual(s.post(f"/api/v1/opening-balances/{self.ob.pk}/invoice/", {}, format="json").status_code, 200)
        b = APIClient(); login(b, staff_b)
        self.assertEqual(b.post(f"/api/v1/opening-balances/{self.ob.pk}/invoice/", {}, format="json").status_code, 404)
        self.assertEqual(b.get(f"/api/v1/opening-balances/{self.ob.pk}/").status_code, 404)


class FinancialYearAndSettingsTests(Base):
    def test_fy_helpers_follow_the_configured_month(self):
        self.assertEqual(fiscal.fy_start_month(), 7)                              # default: July
        self.assertEqual(fiscal.fy_range(2026), (date(2026, 7, 1), date(2027, 6, 30)))
        self.assertEqual((fiscal.fy_label_for(date(2026, 6, 30)), fiscal.fy_label_for(date(2026, 7, 1))), (2025, 2026))
        set_setting("financial_year_start_month", 1)
        self.assertEqual(fiscal.fy_range(2026), (date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(fiscal.fy_label_for(date(2026, 12, 31)), 2026)
        set_setting("financial_year_start_month", 99)
        self.assertEqual(fiscal.fy_start_month(), 7)                              # junk falls back safely

    def test_owner_settings_api_and_fy_filter_on_reports(self):
        o = APIClient(); login(o, self.owner)
        self.assertEqual(o.get("/api/v1/settings/billing/").json(), {"installation_fee_pkr": "0", "installation_fee_mode": "included", "financial_year_start_month": 7})
        r = o.patch("/api/v1/settings/billing/", {"installation_fee_mode": "separate", "financial_year_start_month": 1}, format="json")
        self.assertEqual((r.status_code, r.json()["installation_fee_mode"], r.json()["financial_year_start_month"]), (200, "separate", 1))
        for bad in ({"installation_fee_mode": "weird"}, {"financial_year_start_month": 13}, {"installation_fee_pkr": "-1"}):
            self.assertEqual(o.patch("/api/v1/settings/billing/", bad, format="json").status_code, 400)
        self.assertEqual(APIClient().get("/api/v1/settings/billing/").status_code, 403)
        c = self.conn(); self.cash(self.invoice(c))
        year = timezone.localdate().year
        self.assertEqual(o.get(f"/api/v1/reports/collections/?fy={year}").json()["count"], 1)           # this calendar year == FY (Jan start)
        self.assertEqual(o.get(f"/api/v1/reports/collections/?fy={year - 1}").json()["count"], 0)
        self.assertEqual(o.get("/api/v1/reports/collections/?fy=abc").status_code, 400)
        fy = o.get(f"/api/v1/reports/financial-summary/?fy={year}").json()
        self.assertEqual(len(fy["results"]), 24)                                                          # 12 months x the owner's 2 offices
        one = o.get(f"/api/v1/reports/financial-summary/?fy={year}&office={self.a.pk}").json()
        self.assertEqual(len(one["results"]), 12)
        self.assertTrue(AuditLog.objects.filter(action="settings.changed").exists())

    def test_new_reports_run_and_respect_scope(self):
        c = self.conn(); self.cash(self.invoice(c))
        for name in ("packages", "purchases", "employees", "branch-performance", "opening-balances", "receipts"):
            r = APIClient(); login(r, self.mgr)
            resp = r.get(f"/api/v1/reports/{name}/")
            self.assertEqual(resp.status_code, 200, name)
        m = APIClient(); login(m, self.mgr)
        self.assertEqual(m.get("/api/v1/reports/receipts/").json()["count"], 1)
        bp = m.get("/api/v1/reports/branch-performance/").json()
        self.assertEqual([r[0] for r in bp["results"]], ["AAA"])                                          # only the manager's office
        self.assertEqual(bp["results"][0][4], "1500.00")                                                  # cash this month
        self.assertEqual(m.get("/api/v1/reports/packages/").json()["results"][0][-1], "1")
        b = APIClient(); login(b, self.staff_b)
        self.assertEqual(b.get("/api/v1/reports/receipts/").json()["count"], 0)
        self.assertEqual(b.get("/api/v1/reports/employees/").status_code, 403)
