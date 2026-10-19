from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_user
from apps.expenses import services as ex
from apps.expenses.models import Expense
from apps.notifications.models import Notification


class ExpenseTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.mgr_b = make_user(Role.MANAGER, [self.b])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.owner = make_user(Role.OWNER)
        self.today = timezone.localdate()

    def api(self, u):
        c = APIClient(); login(c, u); return c

    def new(self, amount="1000.00", category="fuel", actor=None, office=None, method="cash"):
        return ex.create_expense(actor=actor or self.staff, office=office or self.a, category=category, description="diesel",
                                 amount=amount, expense_date=self.today, payment_method=method)

    def test_numbering_validation_and_notification(self):
        e1, e2 = self.new(), self.new()
        self.assertEqual((e1.number, e2.number, e1.status), ("AAA-EXP-000001", "AAA-EXP-000002", "pending"))
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="expense.pending").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.mgr_b, kind="expense.pending").exists())
        for bad in ({"amount": "0"}, {"amount": "-5"}, {"method": "cheque"}):
            with self.assertRaises(BusinessRuleError):
                self.new(**{k: v for k, v in bad.items()}) if "method" not in bad else self.new(method=bad["method"])
        with self.assertRaises(BusinessRuleError):
            ex.create_expense(actor=self.staff, office=self.a, category="fuel", description="x", amount="5", expense_date=self.today + timedelta(days=1), payment_method="cash")

    def test_approval_rules_and_rejected_stays_visible(self):
        e = self.new()
        with self.assertRaises(BusinessRuleError):
            ex.decide_expense(actor=self.staff, expense=e, approve=True)         # own entry
        with self.assertRaises(BusinessRuleError):
            ex.decide_expense(actor=self.mgr, expense=e, approve=False)          # reject needs a reason
        ex.decide_expense(actor=self.mgr, expense=e, approve=False, reason="no bill attached")
        e.refresh_from_db()
        self.assertEqual((e.status, e.decision_reason, e.decided_by), ("rejected", "no bill attached", self.mgr))
        self.assertTrue(Notification.objects.filter(recipient=self.staff, kind="expense.decided").exists())
        with self.assertRaises(BusinessRuleError):
            ex.decide_expense(actor=self.mgr, expense=e, approve=True)           # already decided
        self.assertEqual(self.api(self.staff).get("/api/v1/expenses/?status=rejected").json()["count"], 1)
        self.assertTrue(AuditLog.objects.filter(action="expense.rejected", reason="no bill attached").exists())

    def test_summary_never_mixes_unapproved_amounts(self):
        a1, a2, p, r = self.new("1000"), self.new("500", "repair", method="easypaisa"), self.new("7000"), self.new("300")
        ex.decide_expense(actor=self.mgr, expense=a1, approve=True)
        ex.decide_expense(actor=self.mgr, expense=a2, approve=True)
        ex.decide_expense(actor=self.mgr, expense=r, approve=False, reason="personal")
        s = self.api(self.mgr).get("/api/v1/expenses/summary/").json()
        self.assertEqual(s["totals"]["approved"], {"total": "1500.00", "count": 2})
        self.assertEqual(s["totals"]["pending"], {"total": "7000.00", "count": 1})
        self.assertEqual(s["totals"]["rejected"], {"total": "300.00", "count": 1})
        self.assertEqual((s["approved_by_category"], s["approved_by_method"]), ({"fuel": "1000.00", "repair": "500.00"}, {"cash": "1000.00", "easypaisa": "500.00"}))

    def test_http_scope_permissions_and_filters(self):
        r = self.api(self.staff).post("/api/v1/expenses/", {"office": self.a.pk, "category": "rent", "description": "office rent", "amount": "25000",
                                                            "expense_date": str(self.today), "payment_method": "bank_transfer"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        eid = r.json()["id"]
        self.assertEqual(self.api(self.tech).get("/api/v1/expenses/").status_code, 403)
        self.assertEqual(self.api(self.tech).post("/api/v1/expenses/", {}, format="json").status_code, 403)
        self.assertEqual(self.api(self.mgr_b).get("/api/v1/expenses/").json()["count"], 0)
        self.assertEqual(self.api(self.mgr_b).post(f"/api/v1/expenses/{eid}/approve/", {}, format="json").status_code, 404)
        self.assertEqual(self.api(self.staff).post(f"/api/v1/expenses/{eid}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(self.api(self.mgr_b).post("/api/v1/expenses/", {"office": self.a.pk, "category": "rent", "description": "x", "amount": "1",
                                                                         "expense_date": str(self.today), "payment_method": "cash"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/expenses/{eid}/approve/", {}, format="json").json()["status"], "approved")
        self.assertEqual(self.api(self.owner).get("/api/v1/expenses/?category=rent&date_from=2000-01-01").json()["count"], 1)
        self.assertEqual(self.api(self.owner).get("/api/v1/expenses/?category=fuel").json()["count"], 0)

    def test_never_deleted_and_amount_constraint(self):
        e = self.new()
        with self.assertRaises(ProtectedError):
            e.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Expense.objects.filter(pk=e.pk).update(amount=Decimal("0"))
