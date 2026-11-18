"""Regression checks for public-media permissions with a restrictive app umask.

A deployment commonly gives the Nginx worker www-data group access to the
public MEDIA_ROOT. A parent directory created as 0700 makes an existing
image unreadable over HTTPS even if the file itself is present.
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import SimpleTestCase, override_settings

from apps.core.storage import PrivateFileSystemStorage


@unittest.skipUnless(os.name == "posix", "Unix file mode semantics required")
class PublicMediaStoragePermissionsTests(SimpleTestCase):
    def test_public_gallery_and_thumbnail_are_group_readable_under_umask_077(self):
        with tempfile.TemporaryDirectory() as root:
            with override_settings(MEDIA_ROOT=root, MEDIA_URL="/media/"):
                storage = storages["default"]
                self.assertEqual(storage.directory_permissions_mode, 0o750)
                self.assertEqual(storage.file_permissions_mode, 0o640)
                store_id = uuid4().hex
                old_umask = os.umask(0o077)
                try:
                    for kind in ("gallery", "thumbnails"):
                        name = storage.save(
                            f"stores/{store_id}/products/{kind}/image.webp",
                            ContentFile(b"isolated public image bytes"),
                        )
                        file_path = Path(root, name)
                        self.assertEqual(file_path.read_bytes(), b"isolated public image bytes")
                        self.assertEqual(stat.S_IMODE(file_path.stat().st_mode), 0o640)
                        self.assertEqual(storage.url(name), "/media/" + name)
                        path = file_path.parent
                        while path != Path(root):
                            self.assertEqual(
                                stat.S_IMODE(path.stat().st_mode) & 0o777,
                                0o750,
                                f"Public media directory must be traversable by the www-data group: {path}",
                            )
                            path = path.parent
                finally:
                    os.umask(old_umask)

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
