"""Run the XLSX acceptance exercise against a fresh, private local workspace."""
import argparse
import os
from pathlib import Path
import secrets
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")

    repo = Path(__file__).resolve().parents[2]
    state = repo / ".xlsx-local"
    local_paths = [state, *(state / name for name in (
        "catalog.sqlite3", "catalog.sqlite3-journal", "catalog.sqlite3-shm", "catalog.sqlite3-wal",
        "media", "private-media",
    ))]
    if any(path.is_symlink() or path.resolve() != path for path in local_paths):
        parser.error("Local state paths must not redirect to other files or directories.")
    # Keep the caller's PowerShell environment intact and avoid reusing app
    # credentials or configuration inherited from a different project.
    for key in list(os.environ):
        if key.startswith(("DJANGO_", "RASTISI_", "PAYMENT_", "PAYMENTS_", "REDIS_", "AWS_", "SMS_", "ZIBAL_", "CELERY_")):
            os.environ.pop(key)
    os.environ.pop("DATABASE_URL", None)
    os.environ.update(
        DJANGO_SETTINGS_MODULE="tools.import_v33_local.settings",
        DJANGO_DEBUG="true",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(48),
        RASTISI_XLSX_LOCAL_STATE=str(state),
    )
    state.mkdir(exist_ok=True)
    sys.path.insert(0, str(repo))
    os.chdir(repo)

    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import transaction
    from django.utils import timezone

    from apps.catalog.models import Brand, Category, Vendor
    from apps.stores.models import Store, StoreMembership

    call_command("migrate", interactive=False)
    with transaction.atomic():
        store, _ = Store.objects.get_or_create(
            slug="xlsx-qa", defaults={
                "name": "فروشگاه مصنوعی اکسل", "admin_subdomain": "xlsx-qa", "status": Store.Status.ACTIVE,
            },
        )
        if store.status == Store.Status.PROVISIONING:
            store.status = Store.Status.ACTIVE
            store.save(update_fields=["status"])
        owner, _ = get_user_model().objects.get_or_create(
            username="xlsx-owner", defaults={"email": "xlsx-owner@example.invalid", "is_staff": True},
        )
        password = secrets.token_urlsafe(20)
        owner.set_password(password)
        owner.save(update_fields=["password"])
        StoreMembership.objects.get_or_create(
            store=store, user=owner,
            defaults={"role": StoreMembership.Role.OWNER, "status": StoreMembership.MembershipStatus.ACTIVE,
                      "accepted_at": timezone.now()},
        )
        Vendor.objects.get_or_create(store=store, slug="qa-vendor", defaults={"name": "فروشنده مصنوعی"})
        root, _ = Category.objects.get_or_create(store=store, slug="qa-root", defaults={"name": "پوشاک آزمایشی"})
        Category.objects.get_or_create(
            store=store, slug="qa-child", defaults={"name": "پیراهن آزمایشی", "parent": root},
        )
        Brand.objects.get_or_create(store=store, slug="qa-brand", defaults={"name": "برند مصنوعی"})

    print("Open in Edge or Chrome:", f"http://xlsx-qa.rastisi.localhost:{args.port}/admin-portal/imports/upload/", flush=True)
    print("Password sign-in:", owner.email, flush=True)
    print("Local password for this session:", password, flush=True)
    call_command("runserver", f"127.0.0.1:{args.port}", use_reloader=False)


if __name__ == "__main__":
    main()
