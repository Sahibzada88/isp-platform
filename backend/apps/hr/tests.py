from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_user
from apps.hr import services as hr
from apps.hr.models import Attendance, LeaveRequest
from apps.notifications.models import Notification


class HrBase(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.tech = make_user(Role.TECHNICIAN, [self.a, self.b])
        self.mgr_b = make_user(Role.MANAGER, [self.b])
        self.owner = make_user(Role.OWNER)

    def api(self, u):
        c = APIClient(); login(c, u); return c


class AttendanceTests(HrBase):
    def test_check_in_out_cycle(self):
        rec = hr.check_in(actor=self.staff)
        self.assertEqual((rec.office, rec.date), (self.a, timezone.localdate()))
        with self.assertRaises(BusinessRuleError):
            hr.check_in(actor=self.staff)       # once per day
        out = hr.check_out(actor=self.staff)
        self.assertGreaterEqual(out.check_out, out.check_in)
        with self.assertRaises(BusinessRuleError):
            hr.check_out(actor=self.staff)
        with self.assertRaises(BusinessRuleError):
            hr.check_out(actor=self.mgr)        # never checked in

    def test_office_choice_for_multi_office_employee(self):
        with self.assertRaises(BusinessRuleError):
            hr.check_in(actor=self.tech)        # ambiguous
        with self.assertRaises(BusinessRuleError):
            hr.check_in(actor=self.staff, office=self.b)   # not assigned there
        self.assertEqual(hr.check_in(actor=self.tech, office=self.b).office, self.b)

    def test_database_prevents_duplicate_days_and_inverted_times(self):
        hr.check_in(actor=self.staff)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(employee=self.staff, office=self.a, date=timezone.localdate())
        now = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(employee=self.mgr, office=self.a, date=date(2026, 1, 1), check_in=now, check_out=now - timedelta(hours=1))

    def test_correction_keeps_old_and_new_values_reason_and_actor(self):
        rec = hr.check_in(actor=self.staff)
        old_in = rec.check_in
        new_in = old_in - timedelta(hours=2)
        with self.assertRaises(BusinessRuleError):
            hr.correct_attendance(actor=self.mgr, attendance=rec, check_in=new_in, check_out=None, reason="")
        with self.assertRaises(BusinessRuleError):
            hr.correct_attendance(actor=self.mgr, attendance=rec, check_in=new_in, check_out=new_in - timedelta(hours=1), reason="x")
        hr.correct_attendance(actor=self.mgr, attendance=rec, check_in=new_in, check_out=old_in, reason="phone died, arrived early")
        ch = rec.changes.get()
        self.assertEqual((ch.changed_by, ch.old_check_in, ch.new_check_in, ch.old_check_out, ch.new_check_out, ch.reason),
                         (self.mgr, old_in, new_in, None, old_in, "phone died, arrived early"))
        self.assertTrue(AuditLog.objects.filter(action="attendance.corrected").exists())

    def test_http_visibility_and_correction_permissions(self):
        rec = hr.check_in(actor=self.staff)
        hr.check_in(actor=self.tech, office=self.b)
        self.assertEqual(self.api(self.staff).get("/api/v1/attendance/").json()["count"], 1)    # own only
        self.assertEqual(self.api(self.mgr).get("/api/v1/attendance/").json()["count"], 1)      # office AAA only
        self.assertEqual(self.api(self.mgr_b).get("/api/v1/attendance/").json()["count"], 1)    # office BBB only
        self.assertEqual(self.api(self.owner).get("/api/v1/attendance/").json()["count"], 2)
        body = {"check_in": rec.check_in.isoformat(), "check_out": None, "reason": "test"}
        self.assertEqual(self.api(self.staff).post(f"/api/v1/attendance/{rec.pk}/correct/", body, format="json").status_code, 403)
        self.assertEqual(self.api(self.mgr_b).post(f"/api/v1/attendance/{rec.pk}/correct/", body, format="json").status_code, 404)
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/attendance/{rec.pk}/correct/", body, format="json").status_code, 200)
        c = self.api(self.tech)
        self.assertEqual(c.post("/api/v1/attendance/check-in/", {}, format="json").status_code, 400)  # office needed (already in B today anyway)

    def test_http_check_in_and_out(self):
        c = self.api(self.staff)
        self.assertEqual(c.post("/api/v1/attendance/check-in/", {}, format="json").status_code, 201)
        self.assertEqual(c.post("/api/v1/attendance/check-in/", {}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/v1/attendance/check-out/").status_code, 200)


class LeaveTests(HrBase):
    def dates(self, start=10, length=2):
        s = timezone.localdate() + timedelta(days=start)
        return s, s + timedelta(days=length)

    def test_request_approve_flow_with_notifications(self):
        s, e = self.dates()
        lv = hr.request_leave(actor=self.staff, start_date=s, end_date=e, reason="family event")
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="leave.requested").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.mgr_b, kind="leave.requested").exists())  # other office
        with self.assertRaises(BusinessRuleError):
            hr.decide_leave(actor=self.staff, leave=lv, approve=True)           # not your own
        hr.decide_leave(actor=self.mgr, leave=lv, approve=True, note="enjoy")
        lv.refresh_from_db()
        self.assertEqual((lv.status, lv.decided_by), ("approved", self.mgr))
        self.assertTrue(Notification.objects.filter(recipient=self.staff, kind="leave.decided").exists())
        with self.assertRaises(BusinessRuleError):
            hr.decide_leave(actor=self.mgr, leave=lv, approve=False, note="changed mind")  # already decided

    def test_validation_overlap_and_rejection_reason(self):
        s, e = self.dates()
        with self.assertRaises(BusinessRuleError):
            hr.request_leave(actor=self.staff, start_date=e, end_date=s, reason="x")
        with self.assertRaises(BusinessRuleError):
            hr.request_leave(actor=self.staff, start_date=s, end_date=e, reason="")
        lv = hr.request_leave(actor=self.staff, start_date=s, end_date=e, reason="x")
        with self.assertRaises(BusinessRuleError):
            hr.request_leave(actor=self.staff, start_date=s + timedelta(days=1), end_date=e + timedelta(days=3), reason="y")
        with self.assertRaises(BusinessRuleError):
            hr.decide_leave(actor=self.mgr, leave=lv, approve=False, note="")
        hr.decide_leave(actor=self.mgr, leave=lv, approve=False, note="short staffed")
        hr.request_leave(actor=self.staff, start_date=s, end_date=e, reason="try again")   # rejected leave doesn't block

    def test_cancel_only_own(self):
        s, e = self.dates()
        lv = hr.request_leave(actor=self.staff, start_date=s, end_date=e, reason="x")
        with self.assertRaises(BusinessRuleError):
            hr.cancel_leave(actor=self.mgr, leave=lv)
        self.assertEqual(hr.cancel_leave(actor=self.staff, leave=lv).status, "cancelled")

    def test_http_scope_and_permissions(self):
        s, e = self.dates()
        r = self.api(self.staff).post("/api/v1/leave/", {"start_date": str(s), "end_date": str(e), "reason": "trip"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        lid = r.json()["id"]
        self.assertEqual(self.api(self.staff).post(f"/api/v1/leave/{lid}/approve/", {}, format="json").status_code, 403)
        self.assertEqual(self.api(self.mgr_b).post(f"/api/v1/leave/{lid}/approve/", {}, format="json").status_code, 404)
        self.assertEqual(self.api(self.mgr_b).get("/api/v1/leave/").json()["count"], 0)
        self.assertEqual(self.api(self.mgr).get("/api/v1/leave/?status=pending").json()["count"], 1)
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/leave/{lid}/reject/", {"note": ""}, format="json").status_code, 400)
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/leave/{lid}/approve/", {}, format="json").json()["status"], "approved")
