"""Loopback-only configuration for the XLSX acceptance exercise."""
import os
from pathlib import Path

from shop_core.settings import *  # noqa: F403

STATE = Path(os.environ["RASTISI_XLSX_LOCAL_STATE"])
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": STATE / "catalog.sqlite3"}}
MEDIA_ROOT = STATE / "media"
PRIVATE_MEDIA_ROOT = STATE / "private-media"
ALLOWED_HOSTS = ["127.0.0.1", "rastisi.localhost", "xlsx-qa.rastisi.localhost", "xlsx-shop.rastisi.localhost"]
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
