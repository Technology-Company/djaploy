"""
Django settings for the helloworld test project.

This is a minimal Django project used to test djaploy deployments locally.
"""

from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR
GIT_DIR = PROJECT_DIR.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-local-testing-only-do-not-use-in-production"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["*"]

# Application definition
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "djaploy",
]

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
DATABASES = {
    "default": {
        "ENGINE": "django.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
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

# Import local settings if they exist
try:
    from helloworld.local import *  # noqa: F401, F403
except ImportError:
    pass
