"""CSV import. Idempotent (old IDs are unique), never merges or overwrites existing customers, never reactivates anything,
and reports every problem row. A dry run executes the full import and rolls it back."""
import csv
import hashlib
import io
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError, DuplicateWarning
from apps.core.money import money_str
from apps.customers import services as cs
from apps.customers.models import Connection, Customer
from apps.organization.models import Village
from apps.packages.models import Package

from .models import ImportBatch, ImportIssue, OpeningBalance

MAX_ROWS = 3000  # bounded work per request (serverless); split bigger files
CUSTOMER_COLUMNS = ["legacy_customer_id", "full_name", "phone", "village_code", "status", "package_code"]
OPTIONAL_COLUMNS = ["legacy_connection_id", "cnic", "address", "office_code", "installation_address", "expiry_date", "registration_date",
                    "outstanding_balance", "balance_verified", "notes"]
PACKAGE_COLUMNS = ["code", "name", "speed_mbps", "price_pkr", "validity_days"]


class RowProblem(Exception):
    def __init__(self, level, code, message):
        self.level, self.code, self.message = level, code, message


def _parse(csv_text, required, optional=()):
    if len(csv_text.encode("utf-8")) > 4_000_000:
        raise BusinessRuleError("The file is too large for one import. Split it into smaller files.")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in required if c not in headers]
    if missing:
        raise BusinessRuleError(f"Missing required column(s): {', '.join(missing)}.")
    unknown = [h for h in headers if h not in required and h not in optional]
    if unknown:
        raise BusinessRuleError(f"Unknown column(s): {', '.join(unknown)}.")
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if len(rows) > MAX_ROWS:
        raise BusinessRuleError(f"Too many rows ({len(rows)}). Import at most {MAX_ROWS} rows at a time.")
    if not rows:
        raise BusinessRuleError("The file has no data rows.")
    return rows


def _date(value, field):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise RowProblem("error", "invalid_date", f"{field} '{value}' is not a valid date (use YYYY-MM-DD).")


def _money(value, field):
    try:
        d = Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise RowProblem("error", "invalid_amount", f"{field} '{value}' is not a number.")
    if d < 0 or d != d.quantize(Decimal("0.01")):
        raise RowProblem("error", "invalid_amount", f"{field} '{value}' must be a non-negative amount with at most 2 decimals.")
    return d.quantize(Decimal("0.01"))


def _report(batch, issues, dry_run):
    return {
        "batch_id": None if dry_run else batch.pk, "kind": batch.kind, "dry_run": dry_run, "source_rows": batch.source_rows,
        "imported_rows": batch.imported_rows, "rejected_rows": batch.rejected_rows, "skipped_already_imported": batch.skipped_rows,
        "duplicate_or_conflict_rows": batch.conflict_rows, "rows_needing_manual_review": batch.review_rows,
        "opening_balance_verified": money_str(batch.opening_balance_verified), "opening_balance_unverified": money_str(batch.opening_balance_unverified),
        "reconciles": batch.source_rows == batch.imported_rows + batch.rejected_rows + batch.skipped_rows,
        "issues": issues[:500], "issues_truncated": len(issues) > 500,
    }


def _record(batch, issues, row_number, level, code, message, raw):
    issues.append({"row": row_number, "level": level, "code": code, "message": message})
    ImportIssue.objects.create(batch=batch, row_number=row_number, level=level, code=code, message=message, raw=raw)


@transaction.atomic
def import_packages(*, actor, csv_text, filename="", dry_run=False, request=None):
    rows = _parse(csv_text, PACKAGE_COLUMNS)
    batch = ImportBatch.objects.create(kind="packages", filename=filename, source_sha256=hashlib.sha256(csv_text.encode()).hexdigest(),
                                       source_rows=len(rows), created_by=actor)
    issues, seen = [], set()
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            with transaction.atomic():
                code = row["code"].upper()
                if not code or not row["name"]:
                    raise RowProblem("error", "missing_field", "code and name are required.")
                if code in seen:
                    raise RowProblem("conflict", "duplicate_in_file", f"Package code {code} appears more than once in this file.")
                seen.add(code)
                try:
                    speed = int(row["speed_mbps"])
                except ValueError:
                    raise RowProblem("error", "invalid_speed", f"speed_mbps '{row['speed_mbps']}' is not a whole number.")
                price = _money(row["price_pkr"], "price_pkr")
                if speed <= 0 or price <= 0:
                    raise RowProblem("error", "invalid_value", "Speed and price must be greater than zero.")
                if row["validity_days"] != "30":
                    raise RowProblem("error", "invalid_validity", "Every package is valid for exactly 30 days.")
                existing = Package.objects.filter(code=code).first()
                if existing:
                    if (existing.name, existing.speed_mbps, existing.price_pkr) == (row["name"], speed, price):
                        batch.skipped_rows += 1
                        continue
                    raise RowProblem("conflict", "package_exists_different", f"Package {code} already exists with different details; it was not changed.")
                pkg = Package.objects.create(code=code, name=row["name"], speed_mbps=speed, price_pkr=price, created_by=actor, updated_by=actor)
                audit.log(actor=actor, action="package.imported", entity=pkg, request=request, after={"code": code, "price": str(price)})
                batch.imported_rows += 1
        except RowProblem as p:
            batch.rejected_rows += 1
            batch.conflict_rows += p.level == "conflict"
            _record(batch, issues, n, p.level, p.code, p.message, row)
    return _finish(batch, issues, dry_run, actor, request)


def _finish(batch, issues, dry_run, actor, request):
    batch.save()
    audit.log(actor=actor, action="import.dry_run" if dry_run else "import.completed", entity=batch, request=request,
              after={"kind": batch.kind, "rows": batch.source_rows, "imported": batch.imported_rows, "rejected": batch.rejected_rows,
                     "skipped": batch.skipped_rows})
    report = _report(batch, issues, dry_run)
    if dry_run:
        transaction.set_rollback(True)  # nothing from a dry run is kept
    return report


@transaction.atomic
def import_customers(*, actor, csv_text, filename="", dry_run=False, request=None):
    rows = _parse(csv_text, CUSTOMER_COLUMNS, OPTIONAL_COLUMNS)
    batch = ImportBatch.objects.create(kind="customers", filename=filename, source_sha256=hashlib.sha256(csv_text.encode()).hexdigest(),
                                       source_rows=len(rows), created_by=actor)
    issues, seen_conn = [], set()
    today = timezone.localdate()
    now = timezone.now()
    for n, row in enumerate(rows, start=2):
        review = []
        try:
            with transaction.atomic():
                for col in CUSTOMER_COLUMNS:
                    if not row.get(col):
                        raise RowProblem("error", "missing_field", f"{col} is required.")
                legacy_c = row["legacy_customer_id"]
                legacy_k = row.get("legacy_connection_id") or legacy_c
                if legacy_k in seen_conn:
                    raise RowProblem("conflict", "duplicate_old_id", f"Old connection ID {legacy_k} appears more than once in this file.")
                seen_conn.add(legacy_k)
                if Connection.objects.filter(legacy_id=legacy_k).exists():
                    batch.skipped_rows += 1   # imported by an earlier run: leave it exactly as it is
                    continue
                village = Village.objects.select_related("office").filter(code=row["village_code"].upper()).first()
                if village is None:
                    raise RowProblem("error", "invalid_village", f"Unknown village code '{row['village_code']}'.")
                if row.get("office_code") and row["office_code"].upper() not in (village.office.code, village.office.doc_prefix):
                    raise RowProblem("error", "office_mismatch", f"Village {village.code} belongs to office {village.office.code}, not {row['office_code']}.")
                package = Package.objects.filter(code=row["package_code"].upper()).first()
                if package is None or not package.is_active:
                    raise RowProblem("error", "package_mismatch", f"Package '{row['package_code']}' does not exist or is inactive.")
                status = row["status"].lower()
                if status not in ("active", "expired", "cancelled"):
                    raise RowProblem("error", "invalid_status", "status must be active, expired or cancelled.")
                expiry = _date(row.get("expiry_date", ""), "expiry_date")
                registered = _date(row.get("registration_date", ""), "registration_date")
                if registered and registered > today:
                    raise RowProblem("error", "invalid_date", "registration_date is in the future.")
                if status in ("active", "expired") and expiry is None:
                    raise RowProblem("error", "missing_field", "expiry_date is required for active and expired connections.")
                balance = _money(row["outstanding_balance"], "outstanding_balance") if row.get("outstanding_balance") else Decimal("0.00")
                verified = row.get("balance_verified", "").lower() in ("yes", "y", "true", "1")
                cnic = row.get("cnic", "")
                try:
                    cnic = cs.normalize_cnic(cnic)
                except BusinessRuleError:
                    raise RowProblem("error", "invalid_cnic", f"CNIC '{cnic}' must contain 13 digits.")

                # the old customer ID is the ONLY thing that links rows to one customer - never name/phone matching
                customer = Customer.objects.filter(legacy_id=legacy_c).first()
                if customer is not None:
                    if (customer.full_name.lower(), customer.phone) != (row["full_name"].lower(), row["phone"]):
                        raise RowProblem("conflict", "legacy_id_different_person",
                                         f"Old customer ID {legacy_c} already maps to {customer.customer_id} with different name/phone. Not merged.")
                else:
                    dups = cs.find_duplicates(full_name=row["full_name"], phone=row["phone"], cnic=cnic, village_id=village.pk)
                    if dups:
                        review.append(("possible_duplicate", "Matches existing customer(s): " + ", ".join(f"{d['customer_id']} ({'/'.join(d['matches'])})" for d in dups)))
                    customer = cs.register_customer(actor=actor, confirm_duplicates=True, reason=f"import batch {batch.pk}", request=request, data={
                        "full_name": row["full_name"], "phone": row["phone"], "cnic": cnic, "permanent_address": row.get("address") or row.get("installation_address") or "-",
                        "village": village, "office": village.office, "notes": row.get("notes", ""), "legacy_id": legacy_c,
                        **({"registered_on": registered} if registered else {})})

                conn = cs.create_connection(actor=actor, customer=customer, package=package,
                                            installation_address=row.get("installation_address") or customer.permanent_address,
                                            village=village, office=village.office, request=request)
                final = status
                if status == "active" and expiry and timezone.make_aware(datetime.combine(expiry, time.max)) <= now:
                    final = "expired"   # never trust a stale 'active'; and expired is never auto-reactivated
                    review.append(("active_but_expired", f"Marked active but expiry {expiry} has passed; imported as expired."))
                fields = {"legacy_id": legacy_k, "status": final}
                if expiry:
                    fields["expires_at"] = timezone.make_aware(datetime.combine(expiry, time(23, 59)))
                if final in ("active", "expired"):
                    fields["installation_completed_at"] = now
                if final == "cancelled":
                    fields.update(cancelled_at=now, cancellation_reason="Imported as cancelled from the previous system")
                Connection.objects.filter(pk=conn.pk).update(**fields)
                audit.log(actor=actor, action="import.connection_imported", entity=conn, office=conn.office, request=request,
                          after={"legacy_id": legacy_k, "status": final, "customer": customer.customer_id})
                if balance > 0:
                    OpeningBalance.objects.create(customer=customer, connection=conn, office=village.office, amount=balance,
                                                  status="verified" if verified else "unverified", migration_date=today,
                                                  source_reference=legacy_k, batch=batch,
                                                  notes=row.get("notes", ""), verified_by=actor if verified else None,
                                                  verified_at=now if verified else None)
                    if verified:
                        batch.opening_balance_verified += balance
                    else:
                        batch.opening_balance_unverified += balance
                        review.append(("unverified_balance", f"Outstanding balance PKR {balance} is not verified."))
                batch.imported_rows += 1
        except RowProblem as p:
            batch.rejected_rows += 1
            batch.conflict_rows += p.level == "conflict"
            _record(batch, issues, n, p.level, p.code, p.message, row)
            continue
        except IntegrityError as e:  # a database rule refused the row: report it, keep the rest
            batch.rejected_rows += 1
            _record(batch, issues, n, "error", "database_constraint", f"Rejected by a database rule: {str(e).splitlines()[0][:150]}", row)
            continue
        if review:
            batch.review_rows += 1
            for code, msg in review:
                _record(batch, issues, n, "review", code, msg, row)
    return _finish(batch, issues, dry_run, actor, request)


@transaction.atomic
def verify_opening_balance(*, actor, balance, request=None):
    ob = OpeningBalance.objects.select_for_update().get(pk=balance.pk)
    if ob.status == OpeningBalance.Status.VERIFIED:
        raise BusinessRuleError("This balance is already verified.")
    ob.status, ob.verified_by, ob.verified_at = OpeningBalance.Status.VERIFIED, actor, timezone.now()
    ob.save()
    audit.log(actor=actor, action="opening_balance.verified", entity=ob, office=ob.office, request=request, after={"amount": str(ob.amount)})
    return ob
