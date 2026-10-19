#!/usr/bin/env python3
"""End-to-end smoke test through the *frontend* origin (Next.js proxy -> Django).

Covers: CSRF, login, customer -> connection -> invoice -> payment -> activation, PDFs, complaints lifecycle, expenses,
Base64 image upload/download, leads, reports (CSV), audit log, notifications, dashboard, customer portal (separate
login, isolation), logout. It CREATES REAL RECORDS (never deletable), so run it only against dev/staging.

    BASE_URL=http://localhost:3000 SMOKE_USER=owner SMOKE_PASSWORD=... python scripts/smoke_e2e.py
Needs: an office, a village, an active package, and an owner account.
"""
import base64
import json
import os
import random
import struct
import sys
import urllib.error
import urllib.request
import zlib
from http.cookiejar import CookieJar

BASE = os.environ.get("BASE_URL", "http://localhost:3000").rstrip("/")
USER, PASSWORD = os.environ["SMOKE_USER"], os.environ["SMOKE_PASSWORD"]
failures = []


class Session:
    def __init__(self):
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def call(self, method, path, body=None, expect=(200, 201, 204), raw=False):
        headers = {"Accept": "application/json", "Origin": BASE, "Referer": BASE + "/"}
        data = None
        if method != "GET":
            headers["X-CSRFToken"] = self.csrf()
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        req = urllib.request.Request(f"{BASE}/api/v1{path}", data=data, method=method, headers=headers)
        try:
            resp = self.opener.open(req, timeout=60)
            status, payload, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            status, payload, ctype = e.code, e.read(), e.headers.get("Content-Type", "")
        if raw:
            return status, payload, ctype
        parsed = json.loads(payload) if payload and "json" in ctype else None
        return status in expect, status, parsed


def step(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + str(detail)) if detail and not ok else ''}")
    if not ok:
        failures.append(name)


def png_base64():
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xd0\x20\x20" * 24 for _ in range(16))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 24, 16, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def staff_flow(s):
    ok, st, _ = s.call("GET", "/auth/csrf/")
    step("csrf cookie issued", ok and bool(s.csrf()), st)
    ok, st, _ = s.call("GET", "/auth/me/", expect=(403,))
    step("anonymous /auth/me is refused", ok, st)
    ok, st, _ = s.call("POST", "/auth/login/", {"username": USER, "password": "definitely-wrong"}, expect=(401,))
    step("wrong password -> 401", ok, st)
    ok, st, me = s.call("POST", "/auth/login/", {"username": USER, "password": PASSWORD})
    step("login", ok, st)
    if not ok:
        return None
    ok, st, villages = s.call("GET", "/villages/")
    ok2, st2, pkgs = s.call("GET", "/packages/?active=true")
    step("villages and packages exist", ok and ok2 and villages["count"] > 0 and pkgs["count"] > 0, "seed a village and an active package first")
    village, package = villages["results"][0], pkgs["results"][0]

    phone = f"03{random.randint(10**8, 10**9 - 1)}"
    name = f"Smoke {random.randint(10**5, 10**6 - 1)}"  # unique per run, or the duplicate check (rightly) warns
    ok, st, cust = s.call("POST", "/customers/", {"full_name": name, "phone": phone, "cnic": str(random.randint(10**12, 10**13 - 1)), "permanent_address": "Smoke address", "village": village["id"]})
    step("create customer (writes pass CSRF through the proxy)", ok, st)
    if not ok:
        return None
    ok, st, dup = s.call("POST", "/customers/", {"full_name": name, "phone": phone, "permanent_address": "x", "village": village["id"]}, expect=(409,))
    step("duplicate warning (409) instead of a silent second record", ok and dup["error"]["code"] == "possible_duplicate", st)
    ok, st, conn = s.call("POST", "/connections/", {"customer": cust["customer_id"], "package": package["id"], "installation_address": "Shop 1"})
    step("create connection", ok, st)
    cid = conn["connection_id"]
    ok, st, inv = s.call("POST", "/invoices/", {"connection": cid})
    step("create invoice", ok and inv["status"] == "open", st)
    ok, st, _ = s.call("POST", "/payments/", {"invoice": inv["number"], "method": "cash", "amount": "1.00"}, expect=(400,))
    step("partial payment refused", ok, st)
    ok, st, pay = s.call("POST", "/payments/", {"invoice": inv["number"], "method": "cash", "amount": inv["total_amount"]})
    step("record full cash payment", ok and pay["status"] == "confirmed", st)
    ok, st, c1 = s.call("GET", f"/connections/{cid}/")
    step("payment alone does not activate", ok and c1["status"] == "pending_installation", st)
    s.call("POST", f"/connections/{cid}/complete-installation/")
    ok, st, c2 = s.call("POST", f"/connections/{cid}/approve-activation/")
    step("approve activation -> active", ok and c2["status"] == "active", st)
    st, body, ctype = s.call("GET", f"/invoices/{inv['number']}/pdf/", raw=True)
    step("invoice PDF through proxy", st == 200 and body.startswith(b"%PDF"), st)
    st, body, ctype = s.call("GET", f"/receipts/{pay['receipt']}/pdf/", raw=True)
    step("receipt PDF through proxy", st == 200 and body.startswith(b"%PDF"), st)

    # ---- complaints lifecycle (owner acts as manager and technician)
    ok, st, cmp_ = s.call("POST", "/complaints/", {"customer": cust["customer_id"], "connection": cid, "category": "internet_down", "description": "smoke: no signal", "priority": "high"})
    step("log complaint", ok, st)
    n = cmp_["number"]
    ok, st, _ = s.call("POST", f"/complaints/{n}/assign/", {"technician": me["id"]})
    step("assign complaint", ok, st)
    for verb, body_ in (("start", None), ("work-log", {"notes": "checked line"}), ("resolve", {"resolution_notes": "reset port"}), ("close", {})):
        ok, st, d = s.call("POST", f"/complaints/{n}/{verb}/", body_)
        step(f"complaint {verb}", ok, st)
    ok, st, d = s.call("POST", f"/complaints/{n}/reopen/", {"reason": "smoke reopen"})
    step("reopen keeps the same record", ok and d["status"] == "reopened" and d["number"] == n and len(d["events"]) >= 6, st)

    # ---- expense + Base64 image
    ok, st, exp = s.call("POST", "/expenses/", {"office": village["office"], "category": "fuel", "description": "smoke diesel", "amount": "900", "expense_date": __import__("datetime").date.today().isoformat(), "payment_method": "cash"})
    step("create expense", ok, st)
    ok, st, e2 = s.call("POST", f"/expenses/{exp['id']}/approve/", {})
    step("approve expense", ok and e2["status"] == "approved", st)
    ok, st, att = s.call("POST", "/attachments/", {"entity_type": "expense", "entity_id": exp["id"], "kind": "expense_receipt", "filename": "bill.png", "data": png_base64()})
    step("upload Base64 image (processed by Django)", ok and att["mime_type"] == "image/jpeg" and "data" not in att, st)
    st, body, ctype = s.call("GET", f"/attachments/{att['id']}/thumbnail/", raw=True)
    step("image download is private JPEG", st == 200 and body[:2] == b"\xff\xd8" and "jpeg" in ctype, st)
    st, _, _ = Session().call("GET", f"/attachments/{att['id']}/image/", raw=True)
    step("no public image links (anonymous refused)", st == 403, st)

    # ---- lead -> customer
    ok, st, lead = s.call("POST", "/leads/", {"office": village["office"], "name": f"Lead {random.randint(10**5, 10**6 - 1)}", "phone": f"03{random.randint(10**8, 10**9 - 1)}", "source": "phone"})
    ok2, st2, conv = s.call("POST", f"/leads/{lead['id']}/convert/", {"village": village["id"], "package": package["id"], "installation_address": "Lead shop"})
    step("convert lead to customer + connection", ok and ok2 and conv["status"] == "converted" and conv["customer_id"], (st, st2))

    # ---- reports, dashboard, audit, notifications
    st, body, ctype = s.call("GET", "/reports/customers/?fmt=csv", raw=True)
    step("customers CSV export", st == 200 and "csv" in ctype and cust["customer_id"].encode() in body, st)
    ok, st, rep = s.call("GET", "/reports/collections/")
    step("collections report preview", ok and rep["count"] >= 1, st)
    ok, st, dash = s.call("GET", "/dashboard/")
    step("dashboard shows real figures", ok and dash["customers"]["total"] >= 1 and "not profit" in dash["scope_note"], st)
    ok, st, aud = s.call("GET", "/audit-logs/?action_prefix=payment.")
    step("audit log readable by owner", ok and aud["count"] >= 1, st)
    ok, st, nc = s.call("GET", "/notifications/unread-count/")
    step("notifications endpoint", ok and "count" in nc, st)
    ok, st, rl = s.call("GET", "/reports/")
    names = {r["name"] for r in rl} if ok else set()
    step("all 23 reports are listed for the owner", ok and len(names) == 23 and "financial-summary" in names, (st, len(names)))
    ok, st, cfg = s.call("GET", "/settings/billing/")
    step("billing settings readable (fee mode, financial year)", ok and cfg["installation_fee_mode"] in ("included", "separate") and 1 <= cfg["financial_year_start_month"] <= 12, st)
    ok, st, ob = s.call("GET", "/opening-balances/summary/")
    step("opening balances summary", ok and "not income" in ob["note"], st)
    ok1, st1, t1 = s.call("GET", "/transfers/office/")
    ok2, st2, t2 = s.call("GET", "/transfers/ownership/")
    step("transfer lists", ok1 and ok2, (st1, st2))
    ok, st, bk = s.call("GET", "/system/backups/")
    step("backup request log readable by owner", ok and isinstance(bk, list), st)
    ok, st, asg = s.call("GET", f"/employees/assignable/?office={village['office']}&for=complaints")
    step("assignable employees (basic fields only)", ok and isinstance(asg, list), st)
    return cust, conn, inv, pay


def portal_flow(staff, cust, conn, inv, pay):
    ok, st, village_list = staff.call("GET", "/customers/" + cust["customer_id"] + "/")
    uname = f"smoke{random.randint(10**5, 10**6 - 1)}"
    ok, st, acc = staff.call("POST", "/portal-accounts/", {"customer": cust["customer_id"], "username": uname, "password": "Smoke-Portal-Passw0rd!"})
    step("staff creates portal account", ok, st)
    p = Session()
    p.call("GET", "/auth/csrf/")
    ok, st, _ = p.call("POST", "/portal/auth/login/", {"username": uname, "password": "wrong"}, expect=(401,))
    step("portal wrong password -> 401", ok, st)
    ok, st, me = p.call("POST", "/portal/auth/login/", {"username": uname, "password": "Smoke-Portal-Passw0rd!"})
    step("portal login", ok and me["must_change_password"], st)
    ok, st, _ = p.call("POST", "/portal/auth/change-password/", {"old_password": "Smoke-Portal-Passw0rd!", "new_password": "Smoke-Portal-New-Passw0rd!"}, expect=(204,))
    step("portal password change", ok, st)
    ok, st, conns = p.call("GET", "/portal/connections/")
    step("customer sees only own connection", ok and [c["connection_id"] for c in conns] == [conn["connection_id"]], st)
    ok, st, invs = p.call("GET", "/portal/invoices/")
    step("customer sees own invoice", ok and [i["number"] for i in invs] == [inv["number"]], st)
    st, body, _ = p.call("GET", f"/portal/invoices/{inv['number']}/pdf/", raw=True)
    step("customer downloads own invoice PDF", st == 200 and body.startswith(b"%PDF"), st)
    ok, st, c = p.call("POST", "/portal/complaints/", {"connection": conn["connection_id"], "category": "slow_speed", "description": "smoke: slow"})
    step("customer submits complaint", ok and c["status"] == "new", st)
    st, _, _ = p.call("GET", "/customers/", raw=True)
    step("portal session cannot reach staff API", st in (401, 403), st)
    st, _, _ = staff.call("GET", "/portal/connections/", raw=True)
    step("staff session cannot use the portal API", st in (401, 403), st)
    ok, st, _ = p.call("POST", "/portal/auth/logout/", expect=(204,))
    step("portal logout", ok, st)
    st, _, _ = p.call("GET", "/portal/auth/me/", raw=True)
    step("portal session gone after logout", st in (401, 403), st)


def main():
    s = Session()
    res = staff_flow(s)
    if res:
        portal_flow(s, *res)
    ok, st, _ = s.call("POST", "/auth/logout/", expect=(204,))
    step("staff logout", ok, st)
    ok, st, _ = s.call("GET", "/auth/me/", expect=(403,))
    step("staff session is gone after logout", ok, st)


main()
print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
