from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.core.testing import make_office, make_user
from apps.notifications import services as ns
from apps.notifications.models import Notification


class NotificationTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.mgr_a = make_user(Role.MANAGER, [self.a])
        self.mgr_b = make_user(Role.MANAGER, [self.b])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.owner = make_user(Role.OWNER)

    def test_routed_by_permission_and_office(self):
        ns.notify_permission("complaints.assign", self.a, kind="complaint.new", title="New complaint", dedupe_key="c-1")
        who = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertIn(self.mgr_a.username, who)
        self.assertIn(self.owner.username, who)
        self.assertNotIn(self.mgr_b.username, who)  # other office
        self.assertNotIn(self.tech.username, who)   # no permission

    def test_dedupe_prevents_repeats_and_exclude_skips_actor(self):
        for _ in range(3):
            ns.notify([self.mgr_a, self.tech], kind="x", title="t", dedupe_key="same-event", exclude=self.tech)
        self.assertEqual(Notification.objects.filter(recipient=self.mgr_a).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.tech).count(), 0)
        ns.notify([self.mgr_a], kind="x", title="t2", dedupe_key="other-event")
        self.assertEqual(Notification.objects.filter(recipient=self.mgr_a).count(), 2)

    def test_api_only_own_and_mark_read(self):
        ns.notify([self.mgr_a], kind="x", title="mine")
        ns.notify([self.mgr_b], kind="x", title="theirs")
        c = APIClient(); login(c, self.mgr_a)
        r = c.get("/api/v1/notifications/").json()
        self.assertEqual([n["title"] for n in r["results"]], ["mine"])
        self.assertEqual(c.get("/api/v1/notifications/unread-count/").json()["count"], 1)
        nid = r["results"][0]["id"]
        self.assertEqual(c.post(f"/api/v1/notifications/{nid}/read/").status_code, 200)
        self.assertEqual(c.get("/api/v1/notifications/unread-count/").json()["count"], 0)
        other = Notification.objects.get(title="theirs")
        self.assertEqual(c.post(f"/api/v1/notifications/{other.pk}/read/").status_code, 404)  # cannot touch others'
        self.assertEqual(APIClient().get("/api/v1/notifications/").status_code, 403)
