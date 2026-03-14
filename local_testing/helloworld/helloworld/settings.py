"""
Django settings for the helloworld test project.

This is a minimal Django project used to test djaploy deployments locally.
"""

from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR
# GIT_DIR points to the helloworld project root (its own git repo)
GIT_DIR = PROJECT_DIR

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-local-testing-only-do-not-use-in-production"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["*"]

# Application definition
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
]

# djaploy is only needed on the developer machine for management commands
try:
    import djaploy  # noqa: F401
    INSTALLED_APPS.append("djaploy")
except ImportError:
    pass

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "helloworld.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "helloworld.wsgi.application"

# Database — SQLite for simplicity
# On zero-downtime deploys each release is under releases/<release-name>/,
# so the db must live in shared/ to persist across symlink swaps.
# BASE_DIR resolves to the release dir; go up twice to reach the app root.
_shared_dir = BASE_DIR.parent.parent / "shared"
_db_path = _shared_dir / "db.sqlite3" if _shared_dir.is_dir() else BASE_DIR / "db.sqlite3"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _db_path,
    }
}

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Djaploy configuration directory
DJAPLOY_CONFIG_DIR = PROJECT_DIR / "infra"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

