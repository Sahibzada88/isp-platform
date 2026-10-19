from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import PermissionGrant, Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing import services as bs
from apps.billing.models import Invoice, Payment
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.customers.models import Connection, Customer
from apps.imports import services as im
from apps.imports.models import ImportBatch, OpeningBalance
from apps.packages.models import Package

HEADER = "legacy_customer_id,legacy_connection_id,full_name,phone,cnic,village_code,office_code,status,package_code,installation_address,expiry_date,registration_date,outstanding_balance,balance_verified"


def csv_of(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


class ImportBase(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va, self.vb = make_village(self.a, "KHT"), make_village(self.b, "BDR")
        self.owner = make_user(Role.OWNER)
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.pkg = make_package("P10", "1500.00", 10)
        self.future = str(timezone.localdate() + timedelta(days=12))
        self.past = str(timezone.localdate() - timedelta(days=40))

    def api(self, u):
        c = APIClient(); login(c, u); return c

    def run_import(self, text, dry_run=False, kind="customers"):
        fn = im.import_customers if kind == "customers" else im.import_packages
        return fn(actor=self.owner, csv_text=text, filename="old.csv", dry_run=dry_run)

    def good_rows(self):
        return [
            f"C1,K1,Ali Khan,03001111111,3520212345671,KHT,AAA,active,P10,Shop 1 Kotha,{self.future},2024-03-01,0,",
            f"C2,K2,Sara Bibi,03002222222,,KHT,,expired,P10,House 7,{self.past},2023-01-15,2500.50,yes",
            f"C3,K3,Bilal Ahmed,03003333333,,KHT,,cancelled,P10,Shop 9,,2022-06-01,800,no",
        ]


class PackageImportTests(ImportBase):
    def test_valid_rows_and_every_kind_of_problem(self):
        text = ("code,name,speed_mbps,price_pkr,validity_days\n"
                "P20,20 Mbps,20,2500,30\n"        # ok
                "P10,10 Mbps,10,1500,30\n"        # already exists, identical -> skipped
                "P10,10 Mbps,10,1700,30\n"        # duplicate in file -> conflict
                "P30,30 Mbps,30,3500,60\n"        # not 30 days
                "P40,40 Mbps,forty,4500,30\n"     # bad speed
                "P50,50 Mbps,50,-1,30\n"          # bad price
                ",,,,\n"                          # empty
                "P60,60 Mbps,60,5000.5,30\n")     # ok
        r = self.run_import(text, kind="packages")
        self.assertEqual((r["source_rows"], r["imported_rows"], r["rejected_rows"], r["skipped_already_imported"], r["duplicate_or_conflict_rows"]), (8, 2, 5, 1, 1))
        self.assertTrue(r["reconciles"])
        self.assertEqual({i["code"] for i in r["issues"]}, {"duplicate_in_file", "invalid_validity", "invalid_speed", "invalid_amount", "missing_field"})
        self.assertEqual(set(Package.objects.values_list("code", flat=True)), {"P10", "P20", "P60"})
        self.assertEqual(Package.objects.get(code="P60").price_pkr, Decimal("5000.50"))
        self.assertTrue(AuditLog.objects.filter(action="package.imported").count() == 2)

    def test_existing_package_with_different_price_is_never_overwritten(self):
        r = self.run_import("code,name,speed_mbps,price_pkr,validity_days\nP10,10 Mbps,10,9999,30\n", kind="packages")
        self.assertEqual((r["imported_rows"], r["duplicate_or_conflict_rows"], r["issues"][0]["code"]), (0, 1, "package_exists_different"))
        self.assertEqual(Package.objects.get(code="P10").price_pkr, Decimal("1500.00"))

    def test_bad_files_are_refused_up_front(self):
        for bad, msg in (("code,name\nP1,x\n", "Missing required"), ("code,name,speed_mbps,price_pkr,validity_days,extra\n", "Unknown column"),
                         ("code,name,speed_mbps,price_pkr,validity_days\n", "no data rows")):
            with self.assertRaises(BusinessRuleError) as cm:
                self.run_import(bad, kind="packages")
            self.assertIn(msg, str(cm.exception.detail))
        with mock.patch("apps.imports.services.MAX_ROWS", 1):
            with self.assertRaises(BusinessRuleError):
                self.run_import("code,name,speed_mbps,price_pkr,validity_days\nA,a,1,1,30\nB,b,1,1,30\n", kind="packages")


class CustomerImportTests(ImportBase):
    def test_happy_path_ids_statuses_and_opening_balances(self):
        r = self.run_import(csv_of(*self.good_rows()))
        self.assertEqual((r["source_rows"], r["imported_rows"], r["rejected_rows"]), (3, 3, 0))
        self.assertTrue(r["reconciles"])
        self.assertEqual(list(Customer.objects.order_by("customer_id").values_list("customer_id", "legacy_id")), [("KHT001", "C1"), ("KHT002", "C2"), ("KHT003", "C3")])
        conns = {c.legacy_id: c for c in Connection.objects.all()}
        self.assertEqual({k: v.status for k, v in conns.items()}, {"K1": "active", "K2": "expired", "K3": "cancelled"})
        self.assertEqual(conns["K1"].expires_at.date(), timezone.localdate() + timedelta(days=12))
        self.assertEqual(conns["K1"].connection_id, "CON-00001")                         # fresh permanent IDs; old ones kept as references
        self.assertEqual(Customer.objects.get(legacy_id="C1").cnic, "35202-1234567-1")
        self.assertEqual(str(Customer.objects.get(legacy_id="C2").registered_on), "2023-01-15")
        self.assertIsNotNone(conns["K3"].cancelled_at)
        self.assertEqual((r["opening_balance_verified"], r["opening_balance_unverified"]), ("2500.50", "800.00"))
        self.assertEqual(r["rows_needing_manual_review"], 1)
        self.assertEqual([i["code"] for i in r["issues"] if i["level"] == "review"], ["unverified_balance"])
        b = ImportBatch.objects.get()
        self.assertEqual((b.source_rows, b.imported_rows, len(b.source_sha256)), (3, 3, 64))

    def test_opening_balances_are_not_income_or_payments(self):
        self.run_import(csv_of(*self.good_rows()))
        self.assertEqual((Payment.objects.count(), Invoice.objects.count()), (0, 0))      # nothing invented
        d = self.api(self.owner).get("/api/v1/dashboard/").json()
        self.assertEqual((d["collections"]["month"]["cash"], d["collections"]["month"]["digital_verified"]), ("0.00", "0.00"))
        self.assertEqual(d["outstanding_invoices"]["count"], 0)
        s = self.api(self.mgr).get("/api/v1/opening-balances/summary/").json()
        self.assertEqual((s["verified"], s["unverified"], s["count"]), ("2500.50", "800.00", 2))
        self.assertIn("not income", s["note"])

    def test_dry_run_changes_nothing_and_consumes_no_ids(self):
        dry = self.run_import(csv_of(*self.good_rows()), dry_run=True)
        self.assertEqual((dry["dry_run"], dry["imported_rows"], dry["batch_id"]), (True, 3, None))
        self.assertEqual((Customer.objects.count(), Connection.objects.count(), OpeningBalance.objects.count(), ImportBatch.objects.count()), (0, 0, 0, 0))
        real = self.run_import(csv_of(*self.good_rows()))
        self.assertEqual(Customer.objects.order_by("customer_id").first().customer_id, "KHT001")   # the dry run did not burn KHT001-003
        self.assertEqual(real["imported_rows"], 3)

    def test_rerun_is_idempotent(self):
        text = csv_of(*self.good_rows())
        self.run_import(text)
        again = self.run_import(text)
        self.assertEqual((again["imported_rows"], again["skipped_already_imported"], again["rejected_rows"]), (0, 3, 0))
        self.assertTrue(again["reconciles"])
        self.assertEqual((Customer.objects.count(), Connection.objects.count(), OpeningBalance.objects.count()), (3, 3, 2))
        self.assertEqual(sum(b.amount for b in OpeningBalance.objects.all()), Decimal("3300.50"))

    def test_rejected_rows_are_reported_and_the_rest_still_import(self):
        rows = [
            f"E1,KE1,No Village,0300,,ZZZ,,active,P10,x,{self.future},,0,",                      # unknown village
            f"E2,KE2,Bad Package,0300,,KHT,,active,P99,x,{self.future},,0,",                      # package mismatch
            f"E3,KE3,Bad Date,0300,,KHT,,active,P10,x,31-31-2026,,0,",                            # invalid date
            f"E4,KE4,Bad CNIC,0300,123,KHT,,active,P10,x,{self.future},,0,",                      # invalid CNIC
            f"E5,KE5,Bad Status,0300,,KHT,,suspended,P10,x,{self.future},,0,",                    # invalid status
            "E6,KE6,No Expiry,0300,,KHT,,active,P10,x,,,0,",                                      # active without expiry
            f"E7,KE7,Bad Amount,0300,,KHT,,active,P10,x,{self.future},,12abc,",                   # invalid amount
            f"E8,KE8,Wrong Office,0300,,KHT,BBB,active,P10,x,{self.future},,0,",                  # office mismatch
            f"E9,KE9,,0300,,KHT,,active,P10,x,{self.future},,0,",                                 # missing name
            f"E10,KE10,Future Reg,0300,,KHT,,active,P10,x,{self.future},{self.future},0,",        # registered in the future
            f"OK1,KOK,Good Customer,03009999999,,KHT,,active,P10,x,{self.future},,0,",            # fine
        ]
        r = self.run_import(csv_of(*rows))
        self.assertEqual((r["source_rows"], r["imported_rows"], r["rejected_rows"]), (11, 1, 10))
        self.assertTrue(r["reconciles"])
        codes = {i["code"] for i in r["issues"]}
        self.assertEqual(codes, {"invalid_village", "package_mismatch", "invalid_date", "invalid_cnic", "invalid_status", "missing_field",
                                 "invalid_amount", "office_mismatch"})
        self.assertEqual(Customer.objects.get().legacy_id, "OK1")
        self.assertEqual(max(i["row"] for i in r["issues"]), 11)                         # row numbers match the spreadsheet (header = row 1)
        self.assertEqual(self.run_import(csv_of(*rows))["skipped_already_imported"], 1)  # fixing and re-running is safe

    def test_duplicates_are_flagged_never_silently_merged(self):
        existing = cs.register_customer(actor=self.staff, confirm_duplicates=True, data={
            "full_name": "Ali Khan", "phone": "03001111111", "cnic": "", "permanent_address": "x", "village": self.va, "office": self.a})
        rows = [
            f"D1,KD1,Ali Khan,03001111111,,KHT,,active,P10,x,{self.future},,0,",             # same name+phone as an existing customer
            f"D2,KD1,Other Person,03004444444,,KHT,,active,P10,x,{self.future},,0,",          # old connection ID repeated in the file
            f"D3,KD3,Zed,03005555555,,KHT,,active,P10,x,{self.future},,0,",                   # fine
            f"D3,KD4,Someone Else,03006666666,,KHT,,active,P10,y,{self.future},,0,",          # same old customer ID, different person
        ]
        r = self.run_import(csv_of(*rows))
        self.assertEqual((r["imported_rows"], r["rejected_rows"], r["duplicate_or_conflict_rows"]), (2, 2, 2))
        self.assertEqual({i["code"] for i in r["issues"] if i["level"] == "conflict"}, {"duplicate_old_id", "legacy_id_different_person"})
        self.assertIn("possible_duplicate", {i["code"] for i in r["issues"]})
        self.assertEqual(Customer.objects.filter(full_name="Ali Khan").count(), 2)         # kept separate: a human decides
        self.assertEqual(Customer.objects.get(pk=existing.pk).legacy_id, "")                # existing record untouched
        self.assertEqual(Customer.objects.get(legacy_id="D3").full_name, "Zed")

    def test_same_old_customer_id_links_several_connections_to_one_customer(self):
        rows = [f"M1,KM1,Multi Owner,03007777777,,KHT,,active,P10,Home,{self.future},,0,", f"M1,KM2,Multi Owner,03007777777,,KHT,,expired,P10,Shop,{self.past},,0,"]
        r = self.run_import(csv_of(*rows))
        self.assertEqual((r["imported_rows"], Customer.objects.count(), Connection.objects.count()), (2, 1, 2))

    def test_stale_active_status_is_imported_as_expired_and_flagged(self):
        r = self.run_import(csv_of(f"S1,KS1,Stale,03008888888,,KHT,,active,P10,x,{self.past},,0,"))
        self.assertEqual(Connection.objects.get().status, "expired")
        self.assertEqual([i["code"] for i in r["issues"]], ["active_but_expired"])
        self.assertEqual(r["rows_needing_manual_review"], 1)

    def test_nothing_is_reactivated_and_imported_connections_work_in_billing(self):
        self.run_import(csv_of(*self.good_rows()))
        expired, cancelled, active = (Connection.objects.get(legacy_id=k) for k in ("K2", "K3", "K1"))
        self.assertEqual(cs.process_expiries(), 0)                                        # job leaves expired/cancelled alone
        for c in (expired, cancelled):
            c.refresh_from_db(); self.assertNotEqual(c.status, "active")
        inv = bs.create_invoice(actor=self.staff, connection=active)[0]                   # an imported active connection can renew
        self.assertEqual((inv.kind, inv.total_amount), ("renewal", Decimal("1500.00")))
        p = bs.record_payment(actor=self.staff, invoice=inv, method="cash", amount=inv.total_amount)
        active.refresh_from_db()
        self.assertEqual(active.expires_at.date(), timezone.localdate() + timedelta(days=12 + 30))
        inv2 = bs.create_invoice(actor=self.staff, connection=expired)[0]
        bs.record_payment(actor=self.staff, invoice=inv2, method="cash", amount=inv2.total_amount)
        expired.refresh_from_db(); self.assertEqual(expired.status, "expired")           # paid, but staff must still confirm
        with self.assertRaises(BusinessRuleError):
            bs.create_invoice(actor=self.staff, connection=cancelled)

    def test_bounded_runtime_for_a_few_hundred_rows(self):
        import time
        rows = [f"B{i},KB{i},Bulk {i},0300{i:07d},,KHT,,active,P10,x,{self.future},,{'100' if i % 5 == 0 else '0'},{'yes' if i % 10 == 0 else ''}" for i in range(300)]
        t0 = time.time()
        r = self.run_import(csv_of(*rows))
        self.assertEqual((r["imported_rows"], r["rejected_rows"]), (300, 0))
        self.assertLess(time.time() - t0, 60)
        self.assertEqual(Customer.objects.count(), 300)
        self.assertEqual(r["opening_balance_verified"], "3000.00")                         # i % 10 == 0 -> 30 rows
        self.assertEqual(r["opening_balance_unverified"], "3000.00")


class ImportApiTests(ImportBase):
    def test_permissions_and_flow(self):
        body = {"kind": "customers", "csv": csv_of(*self.good_rows()), "filename": "old.csv"}
        self.assertEqual(self.api(self.mgr).post("/api/v1/imports/", body, format="json").status_code, 403)
        self.assertEqual(self.api(self.staff).get("/api/v1/imports/").status_code, 403)
        o = self.api(self.owner)
        dry = o.post("/api/v1/imports/", {**body, "dry_run": True}, format="json")
        self.assertEqual((dry.status_code, dry.json()["imported_rows"], Customer.objects.count()), (200, 3, 0))
        real = o.post("/api/v1/imports/", {**body, "dry_run": False}, format="json")
        self.assertEqual((real.status_code, real.json()["imported_rows"]), (201, 3))
        bid = real.json()["batch_id"]
        self.assertEqual(o.get(f"/api/v1/imports/{bid}/").json()["imported_rows"], 3)
        self.assertEqual(o.get(f"/api/v1/imports/{bid}/issues/?level=review").json()["count"], 1)
        bad = o.post("/api/v1/imports/", {"kind": "customers", "csv": "x,y\n1,2\n", "dry_run": True}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Missing required", bad.json()["error"]["message"] + str(bad.json()["error"]["details"]))
        PermissionGrant.objects.create(employee=self.mgr, office=None, code="imports.manage")
        self.assertEqual(self.api(self.mgr).get("/api/v1/imports/").status_code, 200)       # global right can be delegated by the owner

    def test_balance_verification_and_office_scope(self):
        self.run_import(csv_of(*self.good_rows()))
        ob = OpeningBalance.objects.get(status="unverified")
        self.assertEqual(self.api(self.mgr).post(f"/api/v1/opening-balances/{ob.pk}/verify/").status_code, 403)
        r = self.api(self.owner).post(f"/api/v1/opening-balances/{ob.pk}/verify/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "verified"))
        self.assertEqual(self.api(self.owner).post(f"/api/v1/opening-balances/{ob.pk}/verify/").status_code, 400)
        mgr_b = make_user(Role.MANAGER, [self.b])
        self.assertEqual(self.api(mgr_b).get("/api/v1/opening-balances/").json()["count"], 0)
        self.assertEqual(self.api(self.mgr).get("/api/v1/opening-balances/?status=verified").json()["count"], 2)
        self.assertTrue(AuditLog.objects.filter(action="opening_balance.verified").exists())
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            ob.delete()
