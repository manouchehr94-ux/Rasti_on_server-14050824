"""Reconcile operator-only aliases for store-owned public media."""

import fcntl
import json
import os
import re
import stat
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.stores.models import Store, StoreDomain


LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
UUID_HEX = re.compile(r"[a-f0-9]{32}")


def desired_aliases():
    stores = Store.objects.filter(
        status__in=(Store.Status.PROVISIONING, Store.Status.ACTIVE),
        deletion_requested_at__isnull=True,
    )
    claims = defaultdict(set)
    platform_suffix = "." + settings.RASTISI_PLATFORM_PRIMARY_HOST
    rows = stores.order_by().values_list("public_id", "admin_subdomain", "platform_code")
    for store_id, admin_label, code in rows:
        for label in (admin_label, code):
            if LABEL.fullmatch(label):
                claims[label].add(store_id.hex)
    public_names = StoreDomain.objects.filter(
        store__in=stores,
        domain_type=StoreDomain.DomainType.PLATFORM_SUBDOMAIN,
        verification_status=StoreDomain.VerificationStatus.VERIFIED,
        retired_at__isnull=True,
        hostname__endswith=platform_suffix,
    ).order_by().values_list("store__public_id", "hostname")
    for store_id, hostname in public_names:
        label = hostname.removesuffix(platform_suffix)
        if LABEL.fullmatch(label):
            claims[label].add(store_id.hex)
    return claims


class Command(BaseCommand):
    help = "Preview or reconcile administrator-only public media aliases."

    def add_arguments(self, parser):
        parser.add_argument("--alias-root", type=Path, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--remove-managed", action="store_true")

    def handle(self, *, alias_root, apply, remove_managed, **options):
        media_root = Path(settings.MEDIA_ROOT)
        stores_root = media_root / "stores"
        if (
            not alias_root.is_absolute()
            or alias_root.resolve() != alias_root
            or not alias_root.is_dir()
            or not media_root.is_absolute()
            or stores_root.resolve() != stores_root
            or alias_root.is_relative_to(media_root)
            or media_root.is_relative_to(alias_root)
        ):
            raise CommandError("Use canonical, separate media and private alias directories.")
        if getattr(settings, "PRIVATE_MEDIA_ROOT", None):
            private_root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
            if alias_root.is_relative_to(private_root) or private_root.is_relative_to(alias_root):
                raise CommandError("Alias directory overlaps private media.")
        directory = os.open(alias_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if stat.S_IMODE(os.fstat(directory).st_mode) & 0o027:
                raise CommandError("Alias directory must restrict writing to its owner and deny all public access.")
            fcntl.flock(directory, fcntl.LOCK_EX)
            self.reconcile(directory, stores_root, apply, remove_managed)
        finally:
            os.close(directory)

    def reconcile(self, directory, stores_root, apply, remove_managed):
        state_name = ".managed-aliases.json"
        try:
            fd = os.open(state_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            managed = {}
        else:
            with os.fdopen(fd, encoding="utf-8") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise CommandError("Alias ledger must be a regular file.")
                managed = json.load(source)
        if not isinstance(managed, dict) or any(
            not LABEL.fullmatch(label) or not isinstance(store_id, str) or not UUID_HEX.fullmatch(store_id)
            for label, store_id in managed.items()
        ):
            raise CommandError("Invalid alias ledger.")

        original = managed.copy()
        claims = {} if remove_managed else desired_aliases()
        issues = []
        # A hostname claimed by a different store is never automatically reassigned.
        for label, old_id in sorted(managed.copy().items()):
            if label in claims:
                if claims[label] != {old_id}:
                    issues.append(f"ownership conflict {label}")
                continue
            target = stores_root / old_id
            current = self.read_entry(directory, label)
            if current == str(target):
                self.stdout.write(f"remove {label}")
                if apply:
                    os.unlink(label, dir_fd=directory)
                    del managed[label]
            elif current is None:
                del managed[label]
            else:
                issues.append(f"modified alias {label}")

        for label, owners in sorted(claims.items()):
            if len(owners) != 1:
                issues.append(f"multiple stores claim {label}")
                continue
            store_id = next(iter(owners))
            target = stores_root / store_id
            current = self.read_entry(directory, label)
            if label in managed and managed[label] != store_id:
                continue
            if current is not None and current != str(target):
                issues.append(f"existing entry blocks {label}")
                continue
            try:
                mode = target.lstat().st_mode
            except FileNotFoundError:
                # Reserve the canonical name even before the first upload.
                mode = None
            if mode is not None and not stat.S_ISDIR(mode):
                issues.append(f"unsafe public media directory for {label}")
                if managed.get(label) == store_id and current == str(target):
                    self.stdout.write(f"remove unsafe alias {label}")
                    if apply:
                        os.unlink(label, dir_fd=directory)
                continue
            if current is None:
                self.stdout.write(f"create {label}")
                if apply:
                    managed[label] = store_id
                    self.save_ledger(directory, state_name, managed)
                    os.symlink(str(target), label, dir_fd=directory)
                    original = managed.copy()

        if apply and managed != original:
            self.save_ledger(directory, state_name, managed)
        if issues:
            raise CommandError("; ".join(sorted(set(issues))))

    @staticmethod
    def save_ledger(directory, state_name, managed):
        temporary = f".alias-ledger-{os.urandom(12).hex()}"
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as state:
                json.dump(managed, state, sort_keys=True)
                state.flush()
                os.fsync(state.fileno())
            os.replace(temporary, state_name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    @staticmethod
    def read_entry(directory, label):
        try:
            if stat.S_ISLNK(os.stat(label, dir_fd=directory, follow_symlinks=False).st_mode):
                return os.readlink(label, dir_fd=directory)
            return False
        except FileNotFoundError:
            return None
