"""Isolated settings for the explicit Windows/private LAN phone drill."""
import os

from django.core.exceptions import ImproperlyConfigured

from shop_core.settings import *  # noqa: F403

if (
    not DEBUG  # noqa: F405
    or os.environ.get("RASTISI_QA_MODE") != "1"
    or os.environ.get("DATABASE_URL")
    or RASTISI_EMAIL_PROVIDER != "fake"  # noqa: F405
):
    raise ImproperlyConfigured("Mobile LAN settings require local synthetic QA.")

RASTISI_ADMIN_DOMAIN_SUFFIX = "rastisi-qa.test"
RASTISI_PLATFORM_PRIMARY_HOST = "rastisi-qa.test"
RASTISI_PLATFORM_HOSTS = frozenset({"rastisi-qa.test"})
RASTISI_PLATFORM_ADMIN_HOSTS = frozenset({"platformadmins.rastisi-qa.test"})
ALLOWED_HOSTS = [".rastisi-qa.test", "mybikestore.test", "otherstore.test"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
