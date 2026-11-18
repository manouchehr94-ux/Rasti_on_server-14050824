"""Regression checks for public-media permissions with a restrictive app umask.

Store-specific directories can inherit the application group `rastisi`, not
Nginx's `www-data`. Public paths must remain traversable without assuming a
particular supplementary group, even if the app umask is 0077.
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.db import models
from django.test import SimpleTestCase, override_settings

from apps.core.media_paths import StoreMediaPath
from apps.core.storage import PrivateFileSystemStorage


@unittest.skipUnless(os.name == "posix", "Unix file mode semantics required")
class PublicMediaStoragePermissionsTests(SimpleTestCase):
    def test_public_gallery_and_thumbnail_allow_traversal_without_matching_group(self):
        with tempfile.TemporaryDirectory() as root:
            with override_settings(MEDIA_ROOT=root, MEDIA_URL="/media/"):
                storage = storages["default"]
                self.assertEqual(storage.directory_permissions_mode, 0o751)
                self.assertEqual(storage.file_permissions_mode, 0o644)
                store_id = uuid4().hex
                old_umask = os.umask(0o077)
                try:
                    public_keys = (
                        f"stores/{store_id}/products/gallery/item.webp",
                        f"stores/{store_id}/products/thumbnails/thumb.webp",
                        f"stores/{store_id}/categories/images/category.png",
                        f"stores/{store_id}/brands/logos/brand.png",
                        f"stores/{store_id}/shop/branding/logo.webp",
                        f"stores/{store_id}/content/media/hero.webp",
                        "platform/blog/covers/platform.png",
                        "products/gallery/historical.jpg",
                    )
                    for key in public_keys:
                        name = storage.save(key, ContentFile(b"isolated public image bytes"))
                        file_path = Path(root, name)
                        self.assertEqual(file_path.read_bytes(), b"isolated public image bytes")
                        file_mode = stat.S_IMODE(file_path.stat().st_mode)
                        self.assertEqual(file_mode, 0o644)
                        # A different-group web worker can read this public file.
                        self.assertEqual(file_mode & 0o004, 0o004)
                        self.assertEqual(storage.url(name), "/media/" + name)
                        path = file_path.parent
                        while path != Path(root):
                            self.assertEqual(
                                stat.S_IMODE(path.stat().st_mode) & 0o777,
                                0o751,
                                f"Public media directory must be traversable by an unrelated Nginx group: {path}",
                            )
                            path = path.parent
                finally:
                    os.umask(old_umask)

    def test_all_store_owned_public_media_fields_share_default_storage(self):
        # Django's default storage settings govern all public model FileFields,
        # not only product images. Private imports/exports retain their own storage.
        public_fields = []
        private_fields = []
        for model in apps.get_models():
            for field in model._meta.fields:
                if not isinstance(field, models.FileField):
                    continue
                if isinstance(field.storage, PrivateFileSystemStorage):
                    private_fields.append(field)
                    continue
                if isinstance(field.upload_to, StoreMediaPath):
                    public_fields.append(field)
                    self.assertIs(
                        field.storage,
                        default_storage,
                        f"{model._meta.label}.{field.name} must use default public storage",
                    )
        self.assertGreaterEqual(len(public_fields), 15)
        self.assertGreaterEqual(len(private_fields), 4)

    def test_private_storage_not_changed_by_public_storage_config(self):
        with tempfile.TemporaryDirectory() as private:
            with override_settings(PRIVATE_MEDIA_ROOT=private):
                storage = PrivateFileSystemStorage()
                old_umask = os.umask(0o077)
                try:
                    name = storage.save("stores/test/private/test.csv", ContentFile(b"private"))
                finally:
                    os.umask(old_umask)
                parent_mode = stat.S_IMODE(Path(private, name).parent.stat().st_mode)
                self.assertEqual(parent_mode & 0o070, 0)
                self.assertEqual(parent_mode & 0o007, 0)
                with self.assertRaises(ValueError):
                    storage.url(name)
