# Production checklist and known limitations

## Before the pilot (nothing here has been done from the build environment)
- [ ] Confirm Vercel plan terms (commercial use needs Pro; Pro also allows the 15-minute expiry cron).
- [ ] Create Supabase project; set `DATABASE_URL` (transaction pooler, port 6543) and `DIRECT_DATABASE_URL` (5432).
- [ ] `DJANGO_USE_DIRECT_DB=1 python manage.py migrate` then `python manage.py createcachetable`.
- [ ] Set `DJANGO_SECRET_KEY`, `CRON_SECRET`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` (the **frontend** URL).
- [ ] Deploy backend (Root Directory `backend`) and frontend (Root Directory `frontend`, `BACKEND_URL` = backend URL).
- [ ] Run `scripts/smoke_e2e.py` against **staging** and read every FAIL line. Also hit a cold-start and check response times.
- [ ] Create the owner: `OWNER_PASSWORD=... python manage.py create_owner --username <name>`.
- [ ] Create offices, villages, packages (UI or CSV import), then set the installation fee (`PATCH /api/v1/settings/billing/`).
- [ ] Create employees per office and review each person's permissions on the Employees page.
- [ ] Backups: add the GitHub secrets, create the Drive folder, run `database-backup` once manually, then run `backup-restore-test` and confirm it restores.
- [ ] Pilot ONE office: import its data with a dry run first, reconcile counts and balances against the paper registers before using it.
- [ ] Look at the app on a phone and a desktop, in English and Urdu, with real data.

## Honest status of what was verified
Verified here: 278 backend tests on PostgreSQL 16 (including multi-threaded race tests), 27 frontend tests (logic + jsdom component
tests: dialogs, login, search picker, Urdu/RTL switching, image attachments), TypeScript build, and a 51-step end-to-end script through
the Next.js proxy. **Not** verified: a real Vercel/Supabase deployment, the Supabase transaction pooler, cold-start latency, the GitHub
backup workflows and the manual-backup trigger against real GitHub, behaviour in real browsers (layout/visual QA, mobile), load at 5,000 customers.

## Known limitations / not built
- **Urdu/RTL is broad but not complete**: shared components (menus, buttons, headers, statuses, dialog titles) and common phrases are
  translated and alignment is direction-safe, but many page-specific sentences remain English and no native-speaker review or visual RTL QA
  has happened. Invoices/receipts are English-only by design.
- Bank/central account balances and a full cash ledger are not built (reports cover collections, deposits, expenses, refunds, variances).
  No profit figure is shown (bandwidth costs are not tracked).
- Cash that is already in a *reconciled* deposit cannot be reversed (needs an adjustment entry); cancel an unreconciled deposit first.
- A same-office address change does not auto-create a technician task (office/village transfers do).
- Manual WhatsApp sharing is "print the receipt and send it yourself" (no integration, by design). No payment/expiry notifications (by design).
- Lead conversion for an *existing* customer still takes the customer ID as text; complaints and ownership transfer use the search picker.
- No real-browser end-to-end tests; only jsdom component tests and API smoke tests.
- The expiry cron needs Vercel Pro for 15-minute runs (Hobby: once a day; the access-time check still applies).
- The manual-backup button needs `GITHUB_BACKUP_TOKEN` and `GITHUB_REPO` configured on the backend; without them it returns a clear 503.
