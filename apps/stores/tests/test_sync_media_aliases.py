from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command, CommandError
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from apps.content.models import MediaAsset

from apps.stores.models import Store, StoreDomain


class MediaAliasTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.aliases = Path(temp.name) / "aliases"
        self.aliases.mkdir()
        self.media = Path(temp.name) / "media"
        settings = self.settings(
            MEDIA_ROOT=self.media, RASTISI_PLATFORM_PRIMARY_HOST="example.test",
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self.store = Store.objects.create(
            name="Example", slug="فروشگاه-من",
            admin_subdomain="friendly", platform_code="a6a69ggnv",
        )
        self.target = self.media / "stores" / self.store.public_id.hex

    def sync(self, **flags):
        output = StringIO()
        call_command("sync_media_aliases", alias_root=self.aliases, stdout=output, **flags)
        return output.getvalue()

    def test_first_public_media_preview_repeat_rename_suspend_and_remove(self):
        self.assertIn("create friendly", self.sync())
        self.assertEqual(list(self.aliases.iterdir()), [])
        self.sync(apply=True)
        self.assertEqual((self.aliases / "friendly").readlink(), self.target)
        self.assertFalse((self.aliases / "friendly").exists())
        asset = MediaAsset.objects.create(store=self.store, image=ContentFile(b"original", name="uploaded.png"))
        media = Path(asset.image.path)
        self.assertTrue(media.is_relative_to(self.target))
        self.assertTrue((self.aliases / "friendly").is_dir())
        self.assertEqual((self.aliases / "a6a69ggnv").readlink(), self.target)
        self.assertFalse((self.aliases / self.store.slug).exists())
        self.assertNotIn("create ", self.sync(apply=True))
        self.store.admin_subdomain = "renamed"
        self.store.save()
        self.sync(apply=True)
        self.assertFalse((self.aliases / "friendly").is_symlink())
        self.assertEqual((self.aliases / "renamed").readlink(), self.target)
        self.store.status = Store.Status.SUSPENDED
        self.store.save()
        self.sync(apply=True)
        self.assertFalse((self.aliases / "renamed").is_symlink())
        self.assertFalse((self.aliases / "a6a69ggnv").is_symlink())
        self.assertEqual(media.read_bytes(), b"original")

    def test_collision_and_different_tenant_storage(self):
        self.target.mkdir(parents=True)
        self.sync(apply=True)
        other = Store.objects.create(
            name="Other", slug="other", admin_subdomain="a6a69ggnv",
            platform_code="z6a69ggnv",
        )
        other_target = self.target.parent / other.public_id.hex
        other_target.mkdir()
        with self.assertRaisesRegex(CommandError, "multiple stores claim"):
            self.sync(apply=True)
        self.assertEqual((self.aliases / "a6a69ggnv").readlink(), self.target)
        self.assertEqual((self.aliases / "z6a69ggnv").readlink(), other_target)

    def test_tampering_traversal_and_non_directory_are_rejected(self):
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(self.aliases)
        with self.assertRaisesRegex(CommandError, "unsafe public media"):
            self.sync(apply=True)
        self.target.unlink()
        self.target.mkdir()
        (self.aliases / "friendly").symlink_to("../outside")
        (self.aliases / "a6a69ggnv").touch()
        Store.objects.filter(pk=self.store.pk).update(admin_subdomain="../outside")
        with self.assertRaisesRegex(CommandError, "existing entry"):
            self.sync(apply=True)
        self.assertEqual((self.aliases / "friendly").readlink(), Path("../outside"))

    def test_verified_platform_host_retirement_and_rollback(self):
        domain = StoreDomain.objects.create(
            store=self.store, hostname="shop.example.test",
            domain_type=StoreDomain.DomainType.PLATFORM_SUBDOMAIN,
            verification_status=StoreDomain.VerificationStatus.VERIFIED, verified_at=timezone.now(),
        )
        self.target.mkdir(parents=True)
        (self.aliases / "manual").symlink_to(self.target)
        self.sync(apply=True)
        self.assertEqual((self.aliases / "shop").readlink(), self.target)
        self.assertIn("remove shop", self.sync(remove_managed=True))
        self.sync(remove_managed=True, apply=True)
        self.assertTrue((self.aliases / "manual").is_symlink())
        self.assertFalse((self.aliases / "shop").is_symlink())
        self.sync(apply=True)
        domain.retired_at = timezone.now()
        domain.save()
        self.sync(apply=True)
        self.assertFalse((self.aliases / "shop").is_symlink())

    def test_deleted_store_retires_only_managed_aliases(self):
        self.target.mkdir(parents=True)
        (self.aliases / "operator").symlink_to(self.target)
        self.sync(apply=True)
        self.store.delete()
        self.sync(apply=True)
        self.assertFalse((self.aliases / "friendly").is_symlink())
        self.assertTrue((self.aliases / "operator").is_symlink())
        self.assertTrue(self.target.is_dir())
