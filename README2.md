# ISP Management Platform — backend slices 1-3 + frontend slice 1

Django 5.2 + DRF backend for a multi-office ISP, built for Vercel (serverless) + Supabase PostgreSQL.
The Next.js frontend covers login, customers, connections, invoices, payments and receipts (see "Frontend" below).

## What exists (all modules are implemented and tested unless stated)
Backend: Django 5.2 + DRF, PostgreSQL. **278 automated tests** (incl. multi-threaded race tests). Frontend: Next.js 15 + TypeScript + Tailwind,
**27 tests** (logic + jsdom component tests), production build passes. `scripts/smoke_e2e.py` runs a **51-step** flow through the frontend proxy.

| Area | What it does |
|---|---|
| Auth & permissions | Session login, lockout, staff-assisted reset, 6 role templates + per-employee **per-office** permissions, deny-by-default API, office-scoped queries (out-of-scope = 404), customer-portal identity fully separate |
| Customers & connections | Permanent IDs (`KHT001`, `CON-00001`), duplicate *warnings*, masked CNIC, address history, 3-gate activation (payment + installation + approval), cancel/reactivate, package change at next renewal, **approved office/village transfers** (open work, devices and a relocation task follow the connection) and **approved ownership transfers** (unpaid balances are never moved silently) |
| Billing | Office-specific invoice/receipt numbers, price snapshots, one open invoice per connection, void/reissue, one-time installation fee (**included in the first bill or as its own invoice**, owner-selectable) + waiver, **collection of verified opening balances** through normal invoices/payments (reported as old money, not new income), PDFs (A4 invoice, 80 mm receipt), full-payment-only, digital payments verified by a different person, duplicate references blocked, 30-day terms, early renewal, expiry job |
| Financial controls | Corrections (reversal) with four-eyes approval, owner-only refunds, overpayment exceptions, cash deposits + reconciliation with variance, daily cash summary |
| Complaints & tasks | Full status workflow, technicians see only their own, reopen keeps history, work logs, tasks, hand-over of work when an employee leaves |
| HR | Check-in/out, corrections with old/new values, leave requests and approval |
| Inventory | Stock ledger, purchases, idempotent material usage (retries never double-deduct), no negative stock, adjustments with approval, inspected returns, serialized devices (never on two connections), low-stock alerts |
| Expenses & leads | Approval workflow (rejected stay visible), totals keep approved/pending/rejected apart; leads convert to customer + connection atomically |
| Images | Base64 in the database, Python/Pillow validation (real content, pixel + size limits, EXIF stripped), separate content table, private access that follows the owning record |
| Customer portal | Own connections/bills/receipts/PDFs, complaint submission, status without internal notes, charges + renewal instructions |
| Reports | 23 reports (incl. financial summary by **configurable financial year**, branch performance, packages, purchases, employees, receipts, opening balances), preview + CSV/XLSX/PDF, CSV formula-injection guard, CNIC masking, row/size caps, export auditing; dashboards built from real queries |
| Import | CSV import for packages and customers/connections/opening balances: dry run, idempotent re-run, validation report, never merges or reactivates |
| Audit, notifications & ops | Immutable audit log (code + DB trigger); in-app notifications with de-duplication; owner settings page; **manual backup trigger** (asks GitHub Actions to run the backup; token stays server-side, rate-limited, audited) |
| Frontend | 31 routes: dashboard, transfers, opening balances, settings, customers, connections, invoices, payments, receipts, cash/corrections/refunds, complaints, tasks, leads, inventory, expenses, attendance & leave, reports, employees + permissions, packages, audit log, import, notifications, customer portal, English/Urdu toggle with a ~200-phrase Urdu phrasebook applied to shared components (menus, buttons, table headers, statuses, dialogs) and RTL-safe alignment — page-specific sentences not in the phrasebook stay English |

See `docs/PRODUCTION_CHECKLIST.md` for the pre-pilot checklist and the honest list of limitations.

## Run locally
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in; for local dev set DJANGO_DEBUG=1 and DATABASE_URL to a local Postgres
export $(grep -v '^#' .env | xargs)
python manage.py migrate
python manage.py createcachetable
OWNER_PASSWORD='a-long-password' python manage.py create_owner --username owner
python manage.py runserver
python manage.py test          # needs a Postgres user that can CREATE DATABASE
```

## Frontend (`frontend/`)
```bash
cd frontend && cp .env.example .env.local     # BACKEND_URL=http://127.0.0.1:8000
npm install && npm run dev                    # http://localhost:3000
npm run typecheck && npm test && npm run build
```
Next proxies `/api/*` to Django (`next.config.mjs`), so cookies are first-party. Local dev needs the backend started with
`DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:3000` (Django checks the Origin header). On Vercel create a second project with Root Directory
`frontend`, set `BACKEND_URL`, and add the frontend URL to the backend's `DJANGO_CSRF_TRUSTED_ORIGINS`. The customer portal is at `/portal`.
After every deploy run `scripts/smoke_e2e.py` (**dev/staging only**: it creates real records).
Menus and buttons follow permissions as a convenience; the backend enforces them.

## Supabase
1. Create a project; copy the **transaction pooler** URI (port 6543) → `DATABASE_URL`, and the **direct** URI (5432) → `DIRECT_DATABASE_URL`.
2. `DJANGO_USE_DIRECT_DB=1 python manage.py migrate` then `python manage.py createcachetable`.

## Vercel (backend project, Root Directory = `backend`)
Set every variable from `.env.example` in the project settings. `vercel.json` rewrites all paths to `api/index.py`
and schedules the expiry cron every 15 min (**requires Pro**; on Hobby change the schedule to once per day).
Vercel sends `Authorization: Bearer $CRON_SECRET` automatically when `CRON_SECRET` is set.

## Backups (Google Drive via rclone)
Daily `backup.yml`: dump -> check dump is readable -> encrypt with `age` -> upload -> compare MD5 of local vs Drive copy ->
**only then** delete older backups (keeps newest `KEEP`, default 1; any failing step stops before the delete).
Setup: use a dedicated Google account with 2-step verification; create a Drive folder; run `rclone config` once locally
(type `drive`, scope `drive`) and publish the Google OAuth app **"In production"** (in "Testing" mode tokens can expire after ~7 days — verify in Google's current docs).
Repo secrets: `DIRECT_DATABASE_URL`, `BACKUP_AGE_PUBLIC_KEY`, `BACKUP_AGE_PRIVATE_KEY`, `GDRIVE_RCLONE_TOKEN` (the token JSON), `GDRIVE_BACKUP_FOLDER_ID`.
Generate keys with `age-keygen` and keep the private key somewhere safe offline too (lose it = backups are unreadable).
Monthly `restore-test.yml` restores the newest backup into a throw-away Postgres. Restore by hand: `age -d -i key.txt file.dump.age | pg_restore -d <url>`.
Note: with KEEP=1 a problem noticed days later cannot be rolled back; raise KEEP (e.g. 7) if you want that safety.

## Known limitations
See `docs/PRODUCTION_CHECKLIST.md`.
