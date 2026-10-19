from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts import services as acl
from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.billing.tests import new_customer
from apps.notifications.models import Notification
from apps.operations import services as ops
from apps.operations.models import Complaint, Task


class Base(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.tech2 = make_user(Role.TECHNICIAN, [self.a])
        self.tech_b = make_user(Role.TECHNICIAN, [self.b])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.owner = make_user(Role.OWNER)
        self.cust = new_customer(self.staff, self.va, self.a)
        self.conn = cs.create_connection(actor=self.staff, customer=self.cust, package=make_package(), installation_address="x")

    def api(self, user):
        c = APIClient(); login(c, user); return c

    def complaint(self, **kw):
        d = dict(actor=self.staff, customer=self.cust, category="internet_down", description="No signal since morning")
        d.update(kw)
        return ops.create_complaint(**d)


class ComplaintWorkflowTests(Base):
    def test_full_lifecycle_with_history_and_notifications(self):
        c = self.complaint(connection=self.conn, priority="high", source="phone")
        self.assertEqual((c.number, c.status, c.office_id), ("AAA-CMP-000001", "new", self.a.pk))
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="complaint.new").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.staff, kind="complaint.new").exists())  # actor not spammed
        ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        self.assertTrue(Notification.objects.filter(recipient=self.tech, kind="complaint.assigned").exists())
        ops.start_work(actor=self.tech, complaint=c)
        ops.add_work_log(actor=self.tech, complaint=c, notes_text="Cable cut near pole 7", materials_used="10 m fibre", equipment_used="splicer")
        ops.set_waiting(actor=self.tech, complaint=c, reason="waiting for fibre stock")
        c.refresh_from_db()
        self.assertEqual((c.status, c.waiting_reason), ("waiting", "waiting for fibre stock"))
        ops.resume(actor=self.tech, complaint=c)
        ops.resolve(actor=self.tech, complaint=c, resolution_notes="Spliced the cable, signal restored")
        self.assertTrue(Notification.objects.filter(recipient=self.staff, kind="complaint.resolved").exists())
        ops.close(actor=self.staff, complaint=c, note="customer confirmed")
        c.refresh_from_db()
        self.assertEqual((c.status, c.waiting_reason), ("closed", ""))
        kinds = list(c.events.values_list("kind", flat=True))
        self.assertEqual(kinds, ["created", "assigned", "status", "work_log", "status", "status", "status", "status"])

    def test_reopen_keeps_original_record_assignment_and_history(self):
        c = self.complaint()
        ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        ops.start_work(actor=self.tech, complaint=c)
        ops.add_work_log(actor=self.tech, complaint=c, notes_text="first attempt")
        ops.resolve(actor=self.tech, complaint=c, resolution_notes="replaced router")
        ops.close(actor=self.staff, complaint=c)
        with self.assertRaises(BusinessRuleError):
            ops.reopen(actor=self.staff, complaint=c, reason="")
        ops.reopen(actor=self.staff, complaint=c, reason="still down after two days")
        c.refresh_from_db()
        self.assertEqual((c.status, c.assigned_to, c.reopen_count, c.work_logs.count()), ("reopened", self.tech, 1, 1))
        self.assertEqual(Complaint.objects.count(), 1)  # same record, not a new one
        self.assertTrue(Notification.objects.filter(recipient=self.tech, kind="complaint.reopened").exists())
        ops.start_work(actor=self.tech, complaint=c)
        ops.add_work_log(actor=self.tech, complaint=c, notes_text="second attempt")
        self.assertEqual(c.work_logs.count(), 2)
        self.assertIn("reopened", list(c.events.values_list("kind", flat=True)))

    def test_invalid_transitions_and_required_fields(self):
        c = self.complaint()
        with self.assertRaises(BusinessRuleError):
            ops.start_work(actor=self.mgr, complaint=c)           # still NEW
        with self.assertRaises(BusinessRuleError):
            ops.close(actor=self.staff, complaint=c)              # not resolved
        ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        ops.start_work(actor=self.tech, complaint=c)
        with self.assertRaises(BusinessRuleError):
            ops.set_waiting(actor=self.tech, complaint=c, reason="")
        with self.assertRaises(BusinessRuleError):
            ops.resolve(actor=self.tech, complaint=c, resolution_notes="")
        with self.assertRaises(BusinessRuleError):
            ops.start_work(actor=self.tech, complaint=c)          # already in progress

    def test_only_assignee_or_manager_can_work_on_it(self):
        c = self.complaint()
        ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        with self.assertRaises(BusinessRuleError):
            ops.start_work(actor=self.tech2, complaint=c)
        ops.start_work(actor=self.mgr, complaint=c)               # managers can step in

    def test_assignment_rules(self):
        c = self.complaint()
        with self.assertRaises(BusinessRuleError):
            ops.assign(actor=self.staff, complaint=c, technician=self.tech_b)   # other office
        with self.assertRaises(BusinessRuleError):
            ops.assign(actor=self.staff, complaint=c, technician=make_user(Role.COLLECTOR, [self.a]))  # no complaint rights
        ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        ops.assign(actor=self.staff, complaint=c, technician=self.tech2)    # reassign keeps status
        c.refresh_from_db()
        self.assertEqual((c.assigned_to, c.status), (self.tech2, "assigned"))
        self.assertEqual(c.events.filter(kind="assigned").count(), 2)

    def test_priority_change_is_audited(self):
        c = self.complaint()
        ops.set_priority(actor=self.staff, complaint=c, priority="urgent")
        log = AuditLog.objects.get(action="complaint.priority_changed")
        self.assertEqual((log.before["priority"], log.after["priority"]), ("medium", "urgent"))
        self.assertTrue(c.events.filter(kind="priority", to_value="urgent").exists())

    def test_connection_must_belong_to_customer_and_portal_source_has_no_actor(self):
        other = new_customer(self.staff, self.va, self.a)
        with self.assertRaises(BusinessRuleError):
            self.complaint(customer=other, connection=self.conn)
        c = self.complaint(actor=None, source="portal")
        self.assertIsNone(c.created_by)
        self.assertEqual(c.events.first().actor_label, "system")


class ComplaintApiTests(Base):
    def test_http_flow_and_permissions(self):
        s, m, t = self.api(self.staff), self.api(self.mgr), self.api(self.tech)
        r = s.post("/api/v1/complaints/", {"customer": self.cust.customer_id, "connection": self.conn.connection_id,
                                            "category": "slow_speed", "description": "very slow", "priority": "low"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        n = r.json()["number"]
        self.assertEqual(t.get(f"/api/v1/complaints/{n}/").status_code, 404)           # not assigned to the technician
        self.assertEqual(t.get("/api/v1/complaints/").json()["count"], 0)
        self.assertEqual(t.post(f"/api/v1/complaints/{n}/assign/", {"technician": self.tech.pk}, format="json").status_code, 403)
        self.assertEqual(s.post(f"/api/v1/complaints/{n}/assign/", {"technician": self.tech.pk}, format="json").status_code, 200)
        self.assertEqual(t.get("/api/v1/complaints/").json()["count"], 1)               # sees only assigned
        d = t.get(f"/api/v1/complaints/{n}/").json()
        self.assertEqual((d["status"], len(d["events"])), ("assigned", 2))
        self.assertEqual(self.api(self.tech2).post(f"/api/v1/complaints/{n}/start/").status_code, 404)  # not theirs
        self.assertEqual(t.post(f"/api/v1/complaints/{n}/start/").status_code, 200)
        self.assertEqual(t.post(f"/api/v1/complaints/{n}/work-log/", {"notes": "checking line"}, format="json").status_code, 200)
        self.assertEqual(t.post(f"/api/v1/complaints/{n}/resolve/", {"resolution_notes": "reset port"}, format="json").status_code, 200)
        self.assertEqual(t.post(f"/api/v1/complaints/{n}/close/", {}, format="json").status_code, 403)   # technicians cannot close
        self.assertEqual(s.post(f"/api/v1/complaints/{n}/close/", {}, format="json").status_code, 200)
        self.assertEqual(m.post(f"/api/v1/complaints/{n}/reopen/", {"reason": "back again"}, format="json").json()["status"], "reopened")
        self.assertEqual(m.get("/api/v1/complaints/?status=reopened&priority=low").json()["count"], 1)

    def test_cross_office_isolation(self):
        c = self.complaint()
        b = self.api(self.staff_b)
        self.assertEqual(b.get("/api/v1/complaints/").json()["count"], 0)
        self.assertEqual(b.get(f"/api/v1/complaints/{c.number}/").status_code, 404)
        self.assertEqual(b.post(f"/api/v1/complaints/{c.number}/close/", {}, format="json").status_code, 404)
        r = b.post("/api/v1/complaints/", {"customer": self.cust.customer_id, "category": "other", "description": "x"}, format="json")
        self.assertEqual(r.status_code, 404)  # customer of another office
        self.assertEqual(self.api(self.owner).get("/api/v1/complaints/").json()["count"], 1)

    def test_invalid_input(self):
        s = self.api(self.staff)
        self.assertEqual(s.post("/api/v1/complaints/", {"customer": self.cust.customer_id, "category": "bogus", "description": "x"}, format="json").status_code, 400)
        self.assertEqual(s.post("/api/v1/complaints/", {"customer": self.cust.customer_id, "category": "other", "description": "x", "priority": "asap"}, format="json").status_code, 400)


class TaskTests(Base):
    def test_task_flow_and_visibility(self):
        m, t, t2 = self.api(self.mgr), self.api(self.tech), self.api(self.tech2)
        r = m.post("/api/v1/tasks/", {"office": self.a.pk, "title": "Install at KHT001", "kind": "installation", "priority": "high",
                                       "assigned_to": self.tech.pk, "due_date": "2026-12-01"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        tid = r.json()["id"]
        self.assertTrue(Notification.objects.filter(recipient=self.tech, kind="task.assigned").exists())
        self.assertEqual(t.get("/api/v1/tasks/").json()["count"], 1)
        self.assertEqual(t2.get("/api/v1/tasks/").json()["count"], 0)
        self.assertEqual(t2.post(f"/api/v1/tasks/{tid}/start/").status_code, 404)
        self.assertEqual(t.post(f"/api/v1/tasks/{tid}/start/").json()["status"], "in_progress")
        self.assertEqual(t.post(f"/api/v1/tasks/{tid}/complete/", {"notes": ""}, format="json").status_code, 400)
        done = t.post(f"/api/v1/tasks/{tid}/complete/", {"notes": "installed, ONU serial noted"}, format="json").json()
        self.assertEqual((done["status"], done["completion_notes"]), ("done", "installed, ONU serial noted"))
        self.assertEqual(m.post(f"/api/v1/tasks/{tid}/cancel/", {"reason": "x"}, format="json").status_code, 400)  # finished
        self.assertEqual(t.post("/api/v1/tasks/", {"office": self.a.pk, "title": "x"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.staff_b).get("/api/v1/tasks/").json()["count"], 0)

    def test_cannot_assign_across_offices(self):
        with self.assertRaises(BusinessRuleError):
            ops.create_task(actor=self.mgr, office=self.a, title="x", assigned_to=self.tech_b)
        t = ops.create_task(actor=self.mgr, office=self.a, title="y")
        with self.assertRaises(BusinessRuleError):
            ops.assign_task(actor=self.mgr, task=t, employee=self.tech_b)
        ops.assign_task(actor=self.mgr, task=t, employee=self.tech)
        self.assertTrue(AuditLog.objects.filter(action="task.assigned").exists())


class OffboardingTests(Base):
    def _busy_tech(self):
        c1 = self.complaint(); c2 = self.complaint(description="second")
        c3 = self.complaint(description="third")
        for c in (c1, c2, c3):
            ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        ops.start_work(actor=self.tech, complaint=c2)
        ops.start_work(actor=self.tech, complaint=c3); ops.resolve(actor=self.tech, complaint=c3, resolution_notes="done")
        t1 = ops.create_task(actor=self.mgr, office=self.a, title="t1", assigned_to=self.tech)
        t2 = ops.create_task(actor=self.mgr, office=self.a, title="t2", assigned_to=self.tech)
        ops.start_task(actor=self.tech, task=t2)
        return c1, c2, c3, t1, t2

    def test_work_is_handed_to_chosen_colleague(self):
        c1, c2, c3, t1, t2 = self._busy_tech()
        acl.disable_employee(actor=self.owner, employee=self.tech, reason="resigned", reassign_to=self.tech2)
        for obj in (c1, c2, c3, t1, t2):
            obj.refresh_from_db()
        self.assertEqual((c1.assigned_to, c2.assigned_to, t1.assigned_to, t2.assigned_to), (self.tech2,) * 4)
        self.assertEqual((c2.status, t2.status), ("in_progress", "in_progress"))   # work continues where it stopped
        self.assertEqual(c3.assigned_to, self.tech)                                  # resolved work keeps its history
        self.assertTrue(Notification.objects.filter(recipient=self.tech2, kind="complaint.assigned").exists())

    def test_unassigned_work_goes_back_to_managers(self):
        c1, c2, c3, t1, t2 = self._busy_tech()
        acl.disable_employee(actor=self.owner, employee=self.tech, reason="left")
        c1.refresh_from_db(); c2.refresh_from_db(); t1.refresh_from_db()
        self.assertEqual((c1.assigned_to, c1.status, c2.status, t1.assigned_to), (None, "new", "new", None))
        self.assertTrue(c2.events.filter(kind="unassigned").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="complaint.unassigned").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.mgr, kind="task.unassigned").exists())

    def test_cannot_hand_over_to_inactive_or_same_employee(self):
        with self.assertRaises(BusinessRuleError):
            acl.disable_employee(actor=self.owner, employee=self.tech, reason="x", reassign_to=self.tech)
        self.tech2.is_active = False; self.tech2.save()
        with self.assertRaises(BusinessRuleError):
            acl.disable_employee(actor=self.owner, employee=self.tech, reason="x", reassign_to=self.tech2)

    def test_disable_over_http_with_reassign(self):
        c = self.complaint(); ops.assign(actor=self.staff, complaint=c, technician=self.tech)
        r = self.api(self.owner).post(f"/api/v1/employees/{self.tech.pk}/disable/", {"reason": "left", "reassign_to": self.tech2.pk}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.assigned_to, self.tech2)
