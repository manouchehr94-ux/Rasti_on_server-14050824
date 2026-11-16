"""Display metadata must never determine a physical storage key."""
import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings

from apps.content.models import MediaAsset
from apps.core.media_paths import upload_display_name
from apps.stores.models import Store


class MediaOriginalFilenameTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.store = Store.objects.get(slug="akhlaghi")

    def test_duplicate_client_names_keep_labels_but_get_distinct_store_scoped_keys(self):
        name = "بنر اصلی.png"
        first = MediaAsset.objects.create(store=self.store, image=ContentFile(b"first", name=name))
        second = MediaAsset.objects.create(store=self.store, image=ContentFile(b"second", name=name))
        first.refresh_from_db()
        self.assertEqual(first.original_filename, name)
        self.assertEqual(first.display_name, name)
        self.assertEqual(second.display_name, name)
        self.assertNotEqual(first.image.name, second.image.name)
        self.assertTrue(first.image.name.startswith(f"stores/{self.store.public_id.hex}/media-assets/"))
        self.assertNotIn(name, first.image.name)
        self.assertEqual(first.image.read(), b"first")
        self.assertEqual(second.image.read(), b"second")

    def test_fieldfile_save_and_image_only_update_persist_replacement_metadata(self):
        asset = MediaAsset.objects.create(store=self.store, image=ContentFile(b"old", name="old.png"))
        old_path = asset.image.path
        asset.image.save("new.png", ContentFile(b"new"), save=False)
        asset.save(update_fields=["image"])
        asset.refresh_from_db()
        self.assertEqual(asset.display_name, "new.png")
        self.assertEqual(asset.image.read(), b"new")
        self.assertEqual(Path(old_path).read_bytes(), b"old", "No cleanup of existing stored media")
        asset.image.save("third.png", ContentFile(b"third"))
        asset.refresh_from_db()
        self.assertEqual(asset.display_name, "third.png")

    def test_legacy_name_is_a_read_only_fallback(self):
        name = "homepage/hero/legacy-banner.png"
        path = Path(self.directory.name, name)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"existing")
        asset = MediaAsset.objects.create(store=self.store, image=name)
        self.assertEqual(asset.original_filename, "")
        self.assertEqual(asset.display_name, "legacy-banner.png")
        asset.save()
        asset.refresh_from_db()
        self.assertEqual(asset.original_filename, "")
        self.assertEqual(asset.image.name, name)
        self.assertEqual(path.read_bytes(), b"existing")

    def test_old_code_insert_omitting_new_column_gets_database_default(self):
        asset = MediaAsset.objects.create(store=self.store, image="legacy/existing.png")
        # An N-1 ORM INSERT never mentions the new column. Use the same physical
        # columns as that old ORM, including timestamps and ownership.
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO content_mediaasset (created_at, updated_at, store_id, image) VALUES (%s, %s, %s, %s)',
                [asset.created_at, asset.updated_at, self.store.pk, "legacy/old-writer.png"],
            )
        old_writer = MediaAsset.objects.get(image="legacy/old-writer.png")
        self.assertEqual(old_writer.original_filename, "")
        self.assertEqual(old_writer.display_name, "old-writer.png")

    def test_display_name_normalizes_paths_and_controls_without_losing_persian(self):
        self.assertEqual(upload_display_name(r"C:\fakepath\بنر اصلی.png"), "بنر اصلی.png")
        self.assertEqual(upload_display_name("../../secret\n\x00.png"), "secret.png")
        self.assertEqual(upload_display_name("نام\u200cفایل.png"), "نام\u200cفایل.png")
        self.assertEqual(len(upload_display_name("x" * 300)), 255)
        self.assertEqual(upload_display_name(".."), "")
