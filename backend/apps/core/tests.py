import threading
from datetime import datetime, timedelta, timezone as tz

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from unittest import skipUnless

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.customers.models import Connection
from apps.core import sequences
from apps.core.models import JobRun
from apps.core.terms import compute_term
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.accounts.models import Role

IS_PG = connection.vendor == "postgresql"


class TermTests(TestCase):
    def test_new_term_is_payment_plus_30_days(self):
        paid = datetime(2026, 1, 10, 8, 0, tzinfo=tz.utc)
        start, end = compute_term(paid)
        self.assertEqual((start, end), (paid, paid + timedelta(days=30)))

    def test_early_renewal_starts_after_existing_expiry(self):
        paid = datetime(2026, 1, 10, tzinfo=tz.utc)
        expiry = paid + timedelta(days=5)
        start, end = compute_term(paid, expiry)
        self.assertEqual(start, expiry)
        self.assertEqual(end, expiry + timedelta(days=30))

    def test_lapsed_connection_restarts_at_payment_time(self):
        paid = datetime(2026, 3, 1, tzinfo=tz.utc)
        start, _ = compute_term(paid, paid - timedelta(days=2))
        self.assertEqual(start, paid)


@skipUnless(IS_PG, "needs PostgreSQL row locks")
class SequenceConcurrencyTests(TransactionTestCase):
    def test_parallel_allocation_never_duplicates(self):
        results, errors = [], []

        def worker():
            try:
                from django.db import transaction
                with transaction.atomic():
                    results.append(sequences.next_value("invoice", "KHT"))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), list(range(1, 11)))


class AuditTests(TestCase):
    def test_records_cannot_be_updated_or_deleted(self):
        entry = audit.log(actor=None, action="x.test", entity=("thing", 1))
        entry.reason = "tamper"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().update(reason="x")
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().delete()

    @skipUnless(IS_PG, "trigger is PostgreSQL-only")
    def test_database_trigger_blocks_raw_sql(self):
        entry = audit.log(actor=None, action="x.test", entity=("thing", 1))
        from django.db import DatabaseError, transaction
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as c:
            c.execute("UPDATE audit_auditlog SET reason='x' WHERE id=%s", [entry.pk])

    def test_secrets_redacted_and_cnic_masked(self):
        entry = audit.log(actor=None, action="x.test", entity=("thing", 1),
                          after={"password": "hunter2", "session_key": "abc", "cnic": "12345-1234567-1", "ok": "keep"})
        self.assertEqual(entry.after["password"], "[REDACTED]")
        self.assertEqual(entry.after["session_key"], "[REDACTED]")
        self.assertEqual(entry.after["cnic"], "*****-*******-1")
        self.assertEqual(entry.after["ok"], "keep")


@override_settings(CRON_SECRET="s3cret")
class CronTests(TestCase):
    url = "/api/v1/jobs/expire-connections/"

    def _expired_connection(self):
        from apps.customers import services as cs
        office = make_office("KOT"); village = make_village(office, "KHT")
        actor = make_user(Role.MANAGER, [office])
        from apps.customers.models import Customer
        cust = Customer.objects.create(customer_id="KHT001", full_name="A", phone="0300", permanent_address="x",
                                       village=village, office=office, created_by=actor)
        conn = cs.create_connection(actor=actor, customer=cust, package=make_package(), installation_address="x")
        Connection.objects.filter(pk=conn.pk).update(status="active", expires_at=timezone.now() - timedelta(hours=1))
        return conn

    def test_rejects_missing_or_wrong_secret(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer nope").status_code, 403)

    @override_settings(CRON_SECRET="")
    def test_fails_closed_when_secret_unset(self):
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer ").status_code, 403)

    def test_runs_idempotently_and_logs(self):
        conn = self._expired_connection()
        hdr = {"HTTP_AUTHORIZATION": "Bearer s3cret"}
        r1 = self.client.get(self.url, **hdr)
        r2 = self.client.get(self.url, **hdr)
        self.assertEqual((r1.status_code, r1.json()["processed"]), (200, 1))
        self.assertEqual(r2.json()["processed"], 0)
        conn.refresh_from_db()
        self.assertEqual(conn.status, "expired")
        self.assertEqual(JobRun.objects.filter(name="expire_connections", status="succeeded").count(), 2)
        self.assertEqual(JobRun.objects.filter(name="apply_billing_terms", status="succeeded").count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="connection.expired").count(), 1)


@override_settings(GITHUB_BACKUP_TOKEN="ghp_secret_token_value", GITHUB_REPO="acme/isp", BACKUP_MIN_INTERVAL_SECONDS=300)
class BackupRequestTests(TestCase):
    def setUp(self):
        self.owner = make_user(Role.OWNER)
        self.mgr = make_user(Role.MANAGER, [make_office("AAA")])

    def api(self, u):
        from apps.accounts.tests import login
        from rest_framework.test import APIClient
        c = APIClient(); login(c, u); return c

    def fake_response(self, status=204):
        class R:
            def __init__(s): s.status = status
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return R()

    def test_owner_triggers_the_workflow_and_the_token_is_never_exposed(self):
        from unittest import mock
        with mock.patch("apps.core.backups.urllib.request.urlopen", return_value=self.fake_response()) as m:
            r = self.api(self.owner).post("/api/v1/system/backups/")
        self.assertEqual((r.status_code, r.json()["status"]), (202, "requested"))
        sent = m.call_args[0][0]
        self.assertEqual(sent.full_url, "https://api.github.com/repos/acme/isp/actions/workflows/backup.yml/dispatches")
        self.assertEqual(sent.get_method(), "POST")
        self.assertNotIn("ghp_secret_token_value", r.content.decode())
        self.assertNotIn("ghp_secret_token_value", str(AuditLog.objects.filter(action="backup.requested").values("after", "reason")))
        self.assertEqual(len(self.api(self.owner).get("/api/v1/system/backups/").json()), 1)

    def test_rate_limited_permission_checked_and_failures_recorded(self):
        from unittest import mock
        import urllib.error
        o = self.api(self.owner)
        with mock.patch("apps.core.backups.urllib.request.urlopen", return_value=self.fake_response()):
            o.post("/api/v1/system/backups/")
            self.assertEqual(o.post("/api/v1/system/backups/").status_code, 400)           # too soon after the last one
        self.assertEqual(self.api(self.mgr).post("/api/v1/system/backups/").status_code, 403)
        self.assertEqual(self.api(self.mgr).get("/api/v1/system/backups/").status_code, 403)
        from apps.core.models import BackupRequest
        BackupRequest.objects.all().delete()
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch("apps.core.backups.urllib.request.urlopen", side_effect=err):
            r = o.post("/api/v1/system/backups/")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["detail"]), (502, "failed", "GitHub responded 404"))
        self.assertTrue(AuditLog.objects.filter(action="backup.request_failed").exists())

    @override_settings(GITHUB_BACKUP_TOKEN="")
    def test_clear_error_when_not_configured(self):
        r = self.api(self.owner).post("/api/v1/system/backups/")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "not_configured"))
