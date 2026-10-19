# Backend environment variables: where each value comes from

**Where to put them**
- **Vercel (production/staging):** Vercel dashboard → your *backend* project → **Settings → Environment Variables** → add each name/value, tick *Production* (and *Preview* if you use it) → **Save** → then **Redeploy** (changes apply only to new deployments).
- **Your computer (local dev / running `migrate`):** copy `backend/.env.local.example` (quick start, SQLite) or `backend/.env.example` (Postgres/Supabase) to `backend/.env` and fill it in. **The app loads `.env` automatically** (python-dotenv) on every `manage.py` command and on `runserver`, on Windows too; real environment variables override it. Quote values that contain `#` or spaces. Never commit `.env` (it is in `.gitignore`).

**Minimum for a first deployment:** `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `CRON_SECRET`.
Everything else has a safe default or is optional.

| Variable | You must set? | Where the value comes from |
|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Generate it yourself (step 1) |
| `DJANGO_DEBUG` | Production: `0` | You choose (step 2) |
| `DJANGO_ALLOWED_HOSTS` | Yes | Backend's Vercel domain (step 3) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Yes | Frontend's full URL (step 4) |
| `DATABASE_URL` | Yes | Supabase → Connect → Transaction pooler (step 5) |
| `DIRECT_DATABASE_URL` | For migrations/backups | Supabase → Connect → Session pooler (step 6) |
| `DJANGO_USE_DIRECT_DB` | Only when running `migrate` | You type it on the command line (step 7) |
| `CRON_SECRET` | Yes | Generate it yourself (step 8) |
| `GITHUB_BACKUP_TOKEN`, `GITHUB_REPO`, `GITHUB_BACKUP_WORKFLOW`, `GITHUB_BACKUP_REF` | Optional ("Back up now" button) | GitHub (step 9) |
| `OWNER_PASSWORD` | Once, for `create_owner` | You choose (step 10) |
| `CRON_BATCH_SIZE`, `LOGIN_*`, `NUM_PROXIES`, `IMAGE_*`, `BACKUP_MIN_INTERVAL_SECONDS` | No | Defaults are fine (step 11) |

---

### 1. `DJANGO_SECRET_KEY`
Run on your computer (needs Python): `python -c "import secrets; print(secrets.token_urlsafe(50))"` and paste the output. (On Windows use double quotes outside, as shown.) If the app says `DJANGO_SECRET_KEY must be set`, the `.env` file is missing or not next to `manage.py`.
Use a **different** key for staging and production, keep it private, and do not change it later without need (it signs cookies/CSRF tokens; changing it can log everyone out).

### 2. `DJANGO_DEBUG`
Production and staging: `0`. Use `1` only on your own computer. With `0` the app refuses to start if the secret key is missing (intended).

### 3. `DJANGO_ALLOWED_HOSTS`
The backend's own web address, **without** `https://`. Vercel dashboard → backend project → **Settings → Domains** (or the *Domains* box on the project page), e.g. `isp-backend.vercel.app`.
If you add a custom domain later, list both, comma-separated: `isp-backend.vercel.app,api.yourisp.pk`. Local: `localhost,127.0.0.1`.

### 4. `DJANGO_CSRF_TRUSTED_ORIGINS`
The **frontend's** full origin **with** `https://`, no trailing slash: Vercel → *frontend* project → Domains, e.g. `https://isp-app.vercel.app`. Several: comma-separated. Local development: `http://localhost:3000`.
*Chicken-and-egg:* the frontend needs the backend URL and the backend needs the frontend URL. Deploy both once with placeholders, copy the two URLs, set the variables, then redeploy both.

### 5. `DATABASE_URL` (Supabase transaction pooler)
1. supabase.com → **New project** (pick the region closest to your users; choose a **database password** and save it in a password manager; avoid `@ : / ? #` in it, or URL-encode them, e.g. `@` → `%40`).
2. Wait until the project is ready, then click the **Connect** button at the top of the project page.
3. Choose **Transaction pooler** (port **6543**), copy the URI. It looks like `postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:6543/postgres`.
4. Replace `[YOUR-PASSWORD]` with your password and add `?sslmode=require` at the end.
The menu names can change; if you cannot find them, search "connection string" in the Supabase project settings or docs.

### 6. `DIRECT_DATABASE_URL` (Supabase session pooler)
Same **Connect** panel, but choose **Session pooler** (port **5432**, same `pooler.supabase.com` host and the same `postgres.<project-ref>` username). Fill in the password and add `?sslmode=require`.
Why not "Direct connection"? Supabase's direct connection is **IPv6-only** unless you buy the IPv4 add-on, so it often fails from home internet or CI. The session pooler works over IPv4 and is fine for migrations and `pg_dump`. This same value is also the GitHub secret `DIRECT_DATABASE_URL` used by the backup workflow.
Do **not** put this on Vercel: it is only for your computer and GitHub.

### 7. `DJANGO_USE_DIRECT_DB`
Never stored. Type it only when running migrations from your computer so they use the session pooler:
```powershell
cd backend
$env:DJANGO_USE_DIRECT_DB=1; python manage.py migrate; python manage.py createcachetable; Remove-Item Env:DJANGO_USE_DIRECT_DB
```
macOS/Linux: `DJANGO_USE_DIRECT_DB=1 python manage.py migrate`. (`DATABASE_URL` and `DIRECT_DATABASE_URL` come from your `.env`.)

### 8. `CRON_SECRET`
Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Add it in Vercel under exactly the name `CRON_SECRET`: Vercel then sends it automatically as `Authorization: Bearer …` on every cron call, and the backend refuses the cron endpoint if it is missing or wrong.

### 9. GitHub values (only for the "Back up now" button)
- **`GITHUB_REPO`:** `owner/repository` from your repo URL (`github.com/acme/isp-platform` → `acme/isp-platform`).
- **`GITHUB_BACKUP_WORKFLOW`:** the workflow file name: `backup.yml` (default).
- **`GITHUB_BACKUP_REF`:** the branch that holds the workflow, usually `main`.
- **`GITHUB_BACKUP_TOKEN`:** GitHub → your profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. Name it `isp-backup-trigger`; **Repository access → Only select repositories →** your repo; **Repository permissions → Actions: Read and write**; set an expiry (put a reminder in your calendar); **Generate** and copy it at once (GitHub shows it only once). It starts with `github_pat_`.
If you skip these, the button simply answers "not configured"; the daily automatic backup is unaffected.

### 10. `OWNER_PASSWORD` (one time)
Choose a long password (10+ characters, not common). Create the first owner from your computer, then forget the variable:
```powershell
cd backend
$env:OWNER_PASSWORD='your-long-password'; $env:DJANGO_USE_DIRECT_DB=1
python manage.py create_owner --username owner
Remove-Item Env:OWNER_PASSWORD; Remove-Item Env:DJANGO_USE_DIRECT_DB
```
macOS/Linux: `OWNER_PASSWORD='your-long-password' DJANGO_USE_DIRECT_DB=1 python manage.py create_owner --username owner`.
Do not store it in Vercel.

### 11. Variables with defaults (leave them alone unless you have a reason)
`CRON_BATCH_SIZE=500` · `LOGIN_MAX_FAILURES=5` · `LOGIN_LOCKOUT_MINUTES=15` · `LOGIN_THROTTLE_RATE=20/min` · `NUM_PROXIES=1` (correct for Vercel) · `IMAGE_MAX_B64_BYTES=4000000` (keep below ~4.5 MB, Vercel's request limit) · `IMAGE_MAX_PIXELS=25000000` · `IMAGE_MAX_DIMENSION=1600` · `IMAGE_MAX_PER_ENTITY=10` · `BACKUP_MIN_INTERVAL_SECONDS=300`.

### Related values you will need elsewhere
- **Frontend (Vercel, frontend project):** `BACKEND_URL` = `https://<your-backend-domain>` (no trailing slash).
- **GitHub repo secrets for the backup workflow** (Repo → Settings → Secrets and variables → Actions): `DIRECT_DATABASE_URL` (step 6); `BACKUP_AGE_PUBLIC_KEY` / `BACKUP_AGE_PRIVATE_KEY` (run `age-keygen` once; the line starting `age1…` is public, the `AGE-SECRET-KEY-…` line is private, keep a copy of it offline); `GDRIVE_RCLONE_TOKEN` (run `rclone config`, create a remote of type *drive*, scope *drive*, then `rclone config show` and copy the `token = {...}` JSON); `GDRIVE_BACKUP_FOLDER_ID` (open the Drive folder: the ID is the last part of the URL after `/folders/`).

### Check it worked
1. Open `https://<backend>/api/v1/docs/` (API documentation page) after deploying.
2. Run `python scripts/smoke_e2e.py` against the **frontend** URL (staging only; it creates real records).
3. In Vercel → *Settings → Cron Jobs*, confirm the expiry job is listed.
