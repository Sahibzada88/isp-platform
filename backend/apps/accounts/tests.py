from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import Employee, PermissionGrant, Role
from apps.audit.models import AuditLog
from apps.core.testing import PASSWORD, make_office, make_user, make_village


def login(client, user, password=PASSWORD):
    return client.post("/api/v1/auth/login/", {"username": user.username, "password": password}, format="json")


class AuthTests(TestCase):
    def setUp(self):
        self.office = make_office("KOT")
        self.user = make_user(Role.OFFICE_STAFF, [self.office], username="alice")

    def test_login_logout_cycle(self):
        c = APIClient()
        self.assertEqual(c.get("/api/v1/auth/me/").status_code, 403)
        self.assertEqual(login(c, self.user).status_code, 200)
        self.assertEqual(c.get("/api/v1/auth/me/").json()["username"], "alice")
        self.assertEqual(c.post("/api/v1/auth/logout/").status_code, 204)
        self.assertEqual(c.get("/api/v1/auth/me/").status_code, 403)

    def test_login_by_email_and_generic_failure(self):
        c = APIClient()
        r = c.post("/api/v1/auth/login/", {"username": "alice@x.pk", "password": PASSWORD}, format="json")
        self.assertEqual(r.status_code, 200)
        bad = APIClient().post("/api/v1/auth/login/", {"username": "ghost", "password": "x"}, format="json")
        wrong = APIClient().post("/api/v1/auth/login/", {"username": "alice", "password": "x"}, format="json")
        self.assertEqual((bad.status_code, bad.json()), (wrong.status_code, wrong.json()))  # no user enumeration

    @override_settings(LOGIN_MAX_FAILURES=3)
    def test_lockout_after_repeated_failures(self):
        c = APIClient()
        for _ in range(3):
            self.assertEqual(login(c, self.user, "wrong").status_code, 401)
        self.assertEqual(login(c, self.user).status_code, 401)  # correct password, still locked
        self.assertTrue(AuditLog.objects.filter(action="auth.account_locked").exists())
        Employee.objects.filter(pk=self.user.pk).update(locked_until=None)
        self.assertEqual(login(c, self.user).status_code, 200)

    def test_passwords_are_hashed(self):
        self.assertNotIn(PASSWORD, self.user.password)  # never stored in plain text
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_staff_assisted_reset_forces_change_and_revokes_sessions(self):
        owner = make_user(Role.OWNER)
        c = APIClient(); login(c, self.user)
        oc = APIClient(); login(oc, owner)
        r = oc.post(f"/api/v1/employees/{self.user.pk}/reset-password/")
        self.assertEqual(r.status_code, 200)
        temp = r.json()["temporary_password"]
        self.assertEqual(c.get("/api/v1/auth/me/").status_code, 403)  # old session gone
        c2 = APIClient()
        self.assertEqual(login(c2, self.user, temp).status_code, 200)
        self.assertTrue(c2.get("/api/v1/auth/me/").json()["must_change_password"])
        self.assertFalse(AuditLog.objects.filter(action="employee.password_reset", after__isnull=False).exists())


class PermissionTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "VAA"), make_village(self.b, "VBB")
        self.owner = make_user(Role.OWNER)

    def test_role_defaults_and_office_isolation(self):
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.assertTrue(services.has_permission(staff, "customers.create", self.a.pk))
        self.assertFalse(services.has_permission(staff, "customers.create", self.b.pk))
        tech = make_user(Role.TECHNICIAN, [self.a])
        self.assertFalse(services.has_permission(tech, "customers.create", self.a.pk))

    def test_permission_in_one_office_does_not_leak_to_another(self):
        mgr = make_user(Role.MANAGER, [self.a, self.b])
        PermissionGrant.objects.filter(employee=mgr, office=self.b, code="connections.approve_activation").delete()
        self.assertTrue(services.has_permission(mgr, "connections.approve_activation", self.a.pk))
        self.assertFalse(services.has_permission(mgr, "connections.approve_activation", self.b.pk))

    def test_grant_requires_active_assignment(self):
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.assertTrue(services.has_permission(staff, "customers.view", self.a.pk))
        staff.assignments.update(is_active=False)
        self.assertFalse(services.has_permission(staff, "customers.view", self.a.pk))

    def test_owner_can_customise_individual_permissions_and_it_is_audited(self):
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        c = APIClient(); login(c, self.owner)
        r = c.post(f"/api/v1/employees/{staff.pk}/permissions/",
                   {"office": self.a.pk, "permissions": ["customers.view", "customers.view_cnic"]}, format="json")
        self.assertEqual(r.status_code, 204)
        self.assertTrue(services.has_permission(staff, "customers.view_cnic", self.a.pk))
        self.assertFalse(services.has_permission(staff, "customers.create", self.a.pk))
        log = AuditLog.objects.get(action="permissions.changed")
        self.assertIn("customers.create", log.before["permissions"])
        self.assertNotIn("customers.create", log.after["permissions"])

    def test_non_owner_cannot_change_permissions(self):
        mgr = make_user(Role.MANAGER, [self.a]); staff = make_user(Role.OFFICE_STAFF, [self.a])
        c = APIClient(); login(c, mgr)
        r = c.post(f"/api/v1/employees/{staff.pk}/permissions/", {"office": self.a.pk, "permissions": []}, format="json")
        self.assertIn(r.status_code, (403, 404))

    def test_invalid_permission_sets_rejected(self):
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        c = APIClient(); login(c, self.owner)
        r = c.post(f"/api/v1/employees/{staff.pk}/permissions/", {"office": self.a.pk, "permissions": ["audit.view"]}, format="json")
        self.assertEqual(r.status_code, 400)  # global permission can't be office-scoped
        r = c.post(f"/api/v1/employees/{staff.pk}/permissions/", {"office": self.a.pk, "permissions": ["nope.nope"]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_unmapped_actions_denied_by_default(self):
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        c = APIClient(); login(c, staff)
        self.assertEqual(c.get("/api/v1/employees/").status_code, 403)  # no employees.view


class EmployeeLifecycleTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.owner = make_user(Role.OWNER)

    def test_disable_revokes_sessions_and_blocks_login(self):
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        sc = APIClient(); login(sc, staff)
        oc = APIClient(); login(oc, self.owner)
        r = oc.post(f"/api/v1/employees/{staff.pk}/disable/", {"reason": "left company"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sc.get("/api/v1/auth/me/").status_code, 403)
        self.assertEqual(login(APIClient(), staff).status_code, 401)
        self.assertFalse(services.has_permission(staff, "customers.view", self.a.pk))
        self.assertTrue(AuditLog.objects.filter(action="employee.disabled", reason="left company").exists())

    def test_disable_requires_reason_and_protects_last_owner(self):
        c = APIClient(); login(c, self.owner)
        staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.assertEqual(c.post(f"/api/v1/employees/{staff.pk}/disable/", {}, format="json").status_code, 400)
        other_owner = make_user(Role.OWNER)
        oc = APIClient(); login(oc, other_owner)
        self.assertEqual(oc.post(f"/api/v1/employees/{self.owner.pk}/disable/", {"reason": "x"}, format="json").status_code, 200)
        self.assertEqual(oc.post(f"/api/v1/employees/{other_owner.pk}/disable/", {"reason": "x"}, format="json").status_code, 400)

    def test_disabled_signal_lets_other_modules_reassign_work(self):
        from apps.accounts.signals import employee_disabled
        seen = []
        handler = lambda sender, employee, actor, **kw: seen.append(employee.pk)
        employee_disabled.connect(handler)
        try:
            staff = make_user(Role.OFFICE_STAFF, [self.a])
            services.disable_employee(actor=self.owner, employee=staff, reason="left")
        finally:
            employee_disabled.disconnect(handler)
        self.assertEqual(seen, [staff.pk])

    def test_manager_cannot_manage_employee_of_other_office(self):
        mgr = make_user(Role.MANAGER, [self.a], extra_perms=["employees.manage"])
        outsider = make_user(Role.OFFICE_STAFF, [self.b])
        shared = make_user(Role.OFFICE_STAFF, [self.a])
        c = APIClient(); login(c, mgr)
        self.assertEqual(c.post(f"/api/v1/employees/{outsider.pk}/disable/", {"reason": "x"}, format="json").status_code, 404)
        self.assertEqual(c.post(f"/api/v1/employees/{shared.pk}/disable/", {"reason": "x"}, format="json").status_code, 200)

    def test_create_employee_via_api_and_office_scope(self):
        mgr = make_user(Role.MANAGER, [self.a], extra_perms=["employees.manage"])
        c = APIClient(); login(c, mgr)
        ok = c.post("/api/v1/employees/", {"username": "newbie", "password": PASSWORD, "role": "office_staff",
                                            "offices": [self.a.pk]}, format="json")
        self.assertEqual(ok.status_code, 201, ok.content)
        bad = c.post("/api/v1/employees/", {"username": "newbie2", "password": PASSWORD, "role": "office_staff",
                                             "offices": [self.b.pk]}, format="json")
        self.assertEqual(bad.status_code, 403)
        owner_try = c.post("/api/v1/employees/", {"username": "sneaky", "password": PASSWORD, "role": "owner",
                                                   "offices": [self.a.pk]}, format="json")
        self.assertEqual(owner_try.status_code, 400)
        weak = c.post("/api/v1/employees/", {"username": "weak", "password": "123", "role": "office_staff",
                                              "offices": [self.a.pk]}, format="json")
        self.assertEqual(weak.status_code, 400)


class AssignableEmployeesTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.tech = make_user(Role.TECHNICIAN, [self.a], username="tech_a")
        self.tech_b = make_user(Role.TECHNICIAN, [self.b], username="tech_b")
        self.collector = make_user(Role.COLLECTOR, [self.a], username="col_a")

    def get(self, user, **q):
        c = APIClient(); login(c, user)
        return c.get("/api/v1/employees/assignable/", q)

    def test_lists_only_eligible_people_of_that_office_and_only_basic_fields(self):
        r = self.get(self.staff, office=self.a.pk, **{"for": "complaints"})
        self.assertEqual(r.status_code, 200)
        names = {e["username"] for e in r.json()}
        self.assertIn("tech_a", names)
        self.assertNotIn("tech_b", names)       # other office
        self.assertNotIn("col_a", names)        # collectors do not take complaints
        self.assertEqual(set(r.json()[0]), {"id", "username", "name", "role"})
        self.assertIn("col_a", {e["username"] for e in self.get(self.mgr, office=self.a.pk, **{"for": "tasks"}).json()})

    def test_requires_the_matching_assign_right_in_that_office(self):
        self.assertEqual(self.get(self.tech, office=self.a.pk, **{"for": "complaints"}).status_code, 403)
        self.assertEqual(self.get(self.staff, office=self.b.pk, **{"for": "complaints"}).status_code, 403)
        self.assertEqual(self.get(self.staff, office="x").status_code, 400)
        self.assertEqual(self.get(self.staff, office=self.a.pk, **{"for": "bogus"}).status_code, 400)
        self.assertEqual(APIClient().get("/api/v1/employees/assignable/").status_code, 403)
