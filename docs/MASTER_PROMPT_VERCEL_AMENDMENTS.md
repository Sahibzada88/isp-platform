# Amendments to the ISP Master Prompt (Vercel / Supabase reality check)

These amendments **override** the matching sections of the original master prompt. Everything not mentioned stays as written.

## A. Deployment topology (replaces §1 "Deployment" and §23)
- One monorepo, **two Vercel projects**: `frontend/` (Next.js) and `backend/` (Django WSGI via `api/index.py` + `vercel.json` rewrite).
- Next.js proxies `/api/*` to the Django project (`rewrites` in `next.config.js`) so the browser talks to one origin. Session cookies stay first-party and most CORS/CSRF complexity disappears.
- **Vercel Pro is required** for a commercial business (Hobby is for personal, non-commercial use; confirm in Vercel's current terms) and it also unlocks per-minute cron. If paid hosting is refused, the backend moves to a small VPS/Render/Fly service; the Django code does not change.
- Never claim "deployed and working" until a real Vercel preview build has been exercised end to end (login, a DB write, a cron call).

## B. Limits that shape the design (replaces assumptions in §17, §20, §23)
| Limit | Consequence |
|---|---|
| Request/response body about 4.5 MB | Base64 image payload at most 4 MB (about 3 MB raw). Browser compresses to at most 1600 px JPEG/WebP before upload; server re-validates and re-compresses. Retrieval returns a thumbnail by default; full image only on request and only if the response stays under the limit. |
| Function max duration (Hobby 300 s) | Short requests only. Exports above about 4 MB are not produced in one response: require narrower filters or paginate. No long synchronous jobs. |
| Python bundle up to 500 MB | Fine for Django, DRF, Pillow, ReportLab, openpyxl. |
| No system libraries | **PDFs use ReportLab** (not WeasyPrint/wkhtmltopdf). |
| No persistent disk or worker | No Celery/Redis. Rate-limit counters use the database cache; job state uses the `JobRun` table. |
| Cron: Hobby once per day; Pro per minute; no retries, duplicates possible, UTC only | Expiry job every 15 min on Pro; every rule is idempotent and also enforced at access time. Cron endpoint is GET, checks `Authorization: Bearer $CRON_SECRET`, fails closed when the secret is unset. |

## C. Database (replaces §1 pooling wording)
- Runtime `DATABASE_URL` is the Supabase **transaction pooler** (port 6543): `CONN_MAX_AGE=0`, server-side cursors disabled, psycopg `prepare_threshold=None`.
- Migrations and backups use `DIRECT_DATABASE_URL` (port 5432) with `DJANGO_USE_DIRECT_DB=1`.
- `select_for_update` is used only inside `transaction.atomic()`; verified by multi-threaded tests on real PostgreSQL.
- Base64 images live in a **separate table**, deferred from every list query. Monitor Supabase database size (free tier is small).

## D. Authentication (replaces §3 session wording)
- Django **database-backed sessions** in HttpOnly, Secure, SameSite=Lax cookies (revocable, which "disable employee and terminate sessions" requires).
- CSRF via Django's cookie + `X-CSRFToken` header; the frontend calls `GET /api/v1/auth/csrf/` first.
- Lockout counters live on the employee row (works across serverless instances) plus DB-cache IP throttling on login.
- Customer portal accounts use a **separate** credential model and permission class so a portal login can never reach staff endpoints.

## E. Backups (replaces §24)
- Vercel cannot run `pg_dump`. A **GitHub Actions** workflow dumps daily from the direct connection, encrypts with `age`, and uploads to an S3-compatible bucket (Cloudflare R2 / Backblaze B2). A bucket lifecycle rule enforces 90-day retention.
- A monthly workflow restores the latest backup into a throw-away PostgreSQL and runs sanity queries.
- "Manual backup" is the same workflow's `workflow_dispatch`.

## F. Agent process rules (replaces §29/§32 wherever a shell is assumed)
- Build in vertical slices; after each, report what was built, the actual test results, and what is unverified.
- Never mark a module complete without passing tests; never claim a Vercel deployment works unless it was deployed.
