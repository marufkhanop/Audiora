"""
Django settings for the Audiora audiobook streaming platform.

Configuration values come from a .env file at the project root (parsed with a
tiny built-in loader, so no extra dependency is required).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")
    return env


ENV = _load_env(BASE_DIR / ".env")

# Expose the .env values to the process environment so that anything reading
# them with os.environ.get(...) (Cloudinary, third-party SDKs, etc.) works
# without requiring python-dotenv.
for _key, _value in ENV.items():
    os.environ.setdefault(_key, _value)

# Cloudinary cloud media storage. All uploaded media (covers, audio) is stored
# on Cloudinary instead of local disk. Credentials come from the environment —
# either the individual CLOUD_NAME / API_KEY / API_SECRET variables or the full
# CLOUDINARY_URL string (cloudinary://<api_key>:<api_secret>@<cloud_name>).
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("API_SECRET", "")

# Only enable Cloudinary when credentials are actually available, so a fresh
# checkout without them still boots and falls back to local disk storage.
CLOUDINARY_CONFIGURED = bool(
    CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET
) or bool(os.environ.get("CLOUDINARY_URL"))

SECRET_KEY = ENV.get(
    "SECRET_KEY", "django-insecure-local-only-change-me-in-production"
)
DEBUG = ENV.get("DEBUG", "true").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [
    h.strip() for h in ENV.get("ALLOWED_HOSTS", "*").split(",") if h.strip()
]

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "core",
    "accounts",
    "audiobooks",
    "player",
]

# Cloudinary apps go right after django.contrib.staticfiles. They are only
# enabled when credentials are available (the cloudinary_storage app itself
# raises ImproperlyConfigured when it can't find any).
if CLOUDINARY_CONFIGURED:
    INSTALLED_APPS.insert(6, "cloudinary_storage")
    INSTALLED_APPS.insert(7, "cloudinary")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.SplitSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database (SQLite — development / Termux)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Authentication

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static & media files

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Hybrid media storage:
# Local Server Storage is the permanent default for all uploads. Files are
# written under MEDIA_ROOT on this server. Named Cloudinary accounts are added
# from the admin dashboard and bound per audiobook (see audiobooks.models).
# Cloudinary itself stays configured so those optional backends work.
if CLOUDINARY_CONFIGURED:
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": CLOUDINARY_CLOUD_NAME,
        "API_KEY": CLOUDINARY_API_KEY,
        "API_SECRET": CLOUDINARY_API_SECRET,
        "SECURE": True,  # serve cloud uploads over HTTPS
    }

DEFAULT_FILE_STORAGE_BACKEND = "django.core.files.storage.FileSystemStorage"

STORAGES = {
    "default": {
        "BACKEND": DEFAULT_FILE_STORAGE_BACKEND,
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Keep the admin login separate from the listener session. The public site
# keeps Django's normal SESSION_COOKIE_NAME; admin requests use this dedicated
# cookie (see core.middleware.SplitSessionMiddleware).
ADMIN_SESSION_COOKIE_NAME = ENV.get(
    "ADMIN_SESSION_COOKIE_NAME", "audiora_admin_sessionid"
)
ADMIN_SESSION_PATH_PREFIX = "/admin/"

# Listener accounts (email + Google sign-in)
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"

GOOGLE_CLIENT_ID = ENV.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = ENV.get("GOOGLE_CLIENT_SECRET", "")
# Leave blank to derive it from the request (…/accounts/google/callback/).
GOOGLE_REDIRECT_URI = ENV.get("GOOGLE_REDIRECT_URI", "")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Android FUSE storage (e.g. /mnt/sdcard) does not implement flock(), which
# Django's FileSystemStorage uses while saving uploads. Disable the advisory
# locking when the filesystem hosting MEDIA_ROOT cannot perform it.
# fcntl is Unix-only — on Windows Django's locks module is already a no-op,
# so the whole block is skipped there.
try:
    import fcntl as _fcntl
    import os as _os

    def _flock_supported() -> bool:
        probe = os.path.join(MEDIA_ROOT, ".flock_probe")
        try:
            os.makedirs(MEDIA_ROOT, exist_ok=True)
            fd = _os.open(probe, _os.O_CREAT | _os.O_WRONLY, 0o600)
        except OSError:
            return False
        try:
            _fcntl.flock(fd, _fcntl.LOCK_SH)
            return True
        except OSError:
            return False
        finally:
            _os.close(fd)
            _os.unlink(probe)

    if not _flock_supported():
        from django.core.files import locks as _locks

        _locks.lock = lambda fd, flags: None
        _locks.unlock = lambda fd: None
except ImportError:  # pragma: no cover — Windows has no fcntl module
    pass
