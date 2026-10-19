"""Django settings. Everything environment-specific comes from env vars."""
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Local development: read backend/.env automatically (works the same on Windows, macOS and Linux).
# Real environment variables always win, so Vercel/production settings are never overridden by a stray file.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # python-dotenv is only a convenience; production gets its values from the platform
    pass

TESTING = "test" in sys.argv


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key-not-for-production-" + "x" * 30
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.organization",
    "apps.accounts",
    "apps.audit",
    "apps.packages",
    "apps.customers",
    "apps.billing",
    "apps.finance",
    "apps.notifications",
    "apps.operations",
    "apps.hr",
    "apps.inventory",
    "apps.expenses",
    "apps.leads",
    "apps.media",
    "apps.portal",
    "apps.reports",
    "apps.imports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "accounts.Employee"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- database
# Supabase: use the *transaction pooler* URL (port 6543) as DATABASE_URL for the
# running app and the *direct* URL (port 5432) as DIRECT_DATABASE_URL for
# migrations (set DJANGO_USE_DIRECT_DB=1 when running `migrate`).
_db_url = os.environ.get("DATABASE_URL", "")
if env_bool("DJANGO_USE_DIRECT_DB") and os.environ.get("DIRECT_DATABASE_URL"):
    _db_url = os.environ["DIRECT_DATABASE_URL"]

if _db_url:
    DATABASES = {"default": dj_database_url.parse(_db_url, conn_max_age=0)}
    if DATABASES["default"]["ENGINE"].endswith("postgresql"):
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True  # pgbouncer txn mode
        DATABASES["default"].setdefault("OPTIONS", {})["prepare_threshold"] = None
elif DEBUG or TESTING:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    raise ImproperlyConfigured("DATABASE_URL must be set.")

# Serverless has no shared memory between instances: rate-limit counters live in
# the database (run `manage.py createcachetable` once). No Redis needed.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

# ---------------------------------------------------------------- auth/session
SESSION_ENGINE = "django.contrib.sessions.backends.db"  # server-side => revocable
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
if not DEBUG and not TESTING:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_SSL_REDIRECT = True  # safe: X-Forwarded-Proto is set by Vercel's edge

if TESTING:  # PBKDF2 is deliberately slow; production keeps Django's secure default
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", 5))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", 15))

# ---------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {"login": os.environ.get("LOGIN_THROTTLE_RATE", "20/min")},
    "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", 1)),
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "ISP Management API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------- i18n/time
LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Karachi"  # display zone; all timestamps are stored in UTC
USE_TZ = True

# Images are stored as Base64 text in the database (project requirement). Vercel rejects request bodies over ~4.5 MB,
# so the Base64 payload is capped below that; browsers compress/resize before upload and the server re-processes.
IMAGE_MAX_B64_BYTES = int(os.environ.get("IMAGE_MAX_B64_BYTES", 4_000_000))
IMAGE_MAX_PIXELS = int(os.environ.get("IMAGE_MAX_PIXELS", 25_000_000))
IMAGE_MAX_DIMENSION = int(os.environ.get("IMAGE_MAX_DIMENSION", 1600))
IMAGE_MAX_PER_ENTITY = int(os.environ.get("IMAGE_MAX_PER_ENTITY", 10))
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Manual backups: the API asks GitHub Actions to run the backup workflow (Vercel cannot run pg_dump).
# The token needs only 'Actions: read/write' on the one repository. Never expose it to the browser.
GITHUB_BACKUP_TOKEN = os.environ.get("GITHUB_BACKUP_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")                    # e.g. "your-org/isp-platform"
GITHUB_BACKUP_WORKFLOW = os.environ.get("GITHUB_BACKUP_WORKFLOW", "backup.yml")
GITHUB_BACKUP_REF = os.environ.get("GITHUB_BACKUP_REF", "main")
BACKUP_MIN_INTERVAL_SECONDS = int(os.environ.get("BACKUP_MIN_INTERVAL_SECONDS", 300))

# Scheduled jobs (Vercel Cron sends `Authorization: Bearer $CRON_SECRET`)
CRON_SECRET = os.environ.get("CRON_SECRET", "")
CRON_BATCH_SIZE = int(os.environ.get("CRON_BATCH_SIZE", 500))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
