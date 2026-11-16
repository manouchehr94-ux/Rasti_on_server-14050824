"""Ownership, legacy compatibility and real storage tests for tenant media."""
import tempfile
from pathlib import Path
from uuid import uuid4

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.module_loading import import_string

from apps.core.media_paths import StoreMediaPath, blog_cover_upload_path
from apps.core.models import ShopSettings, ExportJob, ImportJob
from apps.core.storage import PrivateFileSystemStorage
from apps.catalog.models import Product, ProductImage, Attribute, AttributeValue, Vendor, Category
from apps.catalog.services.product_image_service import add_product_image, delete_product_image, original_storage_name
from apps.catalog.tests.test_product_image_service import _make_image_file
from apps.stores.models import Store


class StoreMediaPathTests(SimpleTestCase):
    def setUp(self):
        self.store = Store(pk=11, public_id=uuid4(), name="Shop", slug="shop")

    def test_every_store_owned_file_field_has_an_explicit_policy(self):
        count = 0
        for model in apps.get_models():
            if model._meta.app_label not in {"catalog", "content", "core", "blog", "billing"}:
                continue
            for field in model._meta.fields:
                if not isinstance(field, models.FileField):
                    continue
                with self.subTest(model=model._meta.label, field=field.name):
                    if model is ProductImage:
                        instance = ProductImage(product=Product(store=self.store))
                    elif model is AttributeValue:
                        instance = AttributeValue(attribute=Attribute(store=self.store))
                    else:
                        instance = model(store=self.store)
                    name = field.generate_filename(instance, "same-client-name.jpeg")
                    self.assertTrue(name.startswith(f"stores/{self.store.public_id.hex}/"), name)
                    self.assertLessEqual(len(name), field.max_length)
                    self.assertNotIn("same-client-name", name)
                    count += 1
        self.assertEqual(count, 23)  # 19 public image fields + 4 private file fields

    def test_same_filename_for_different_stores_never_shares_a_directory(self):
        policy = StoreMediaPath("shop/branding")
        first = policy(ShopSettings(store=self.store), "logo.png")
        other = Store(pk=12, public_id=uuid4(), slug="other")
        second = policy(ShopSettings(store=other), "logo.png")
        self.assertNotEqual(first.split("/")[1], second.split("/")[1])

    def test_store_rename_and_domain_changes_do_not_change_namespace(self):
        policy = StoreMediaPath("shop/branding")
        before = policy(ShopSettings(store=self.store), "x.png").split("/")[:2]
        self.store.name, self.store.slug = "New name", "new-slug"
        after = policy(ShopSettings(store=self.store), "x.png").split("/")[:2]
        self.assertEqual(before, after)

    def test_missing_owner_is_rejected(self):
        with self.assertRaises(ValidationError):
            StoreMediaPath("shop/branding")(ShopSettings(), "x.png")

    def test_missing_parent_is_rejected(self):
        for instance in (ProductImage(), AttributeValue()):
            with self.assertRaises(ValidationError):
                StoreMediaPath("products/gallery")(instance, "x.png")

    def test_upload_policy_migrations_never_execute_database_operations(self):
        from importlib import import_module
        from django.db.migrations.operations.special import SeparateDatabaseAndState

        for module in (
            "apps.core.migrations.0025_store_media_namespaces",
            "apps.catalog.migrations.0044_store_media_namespaces",
            "apps.content.migrations.0026_store_media_namespaces",
            "apps.blog.migrations.0003_store_media_namespaces",
        ):
            operations = import_module(module).Migration.operations
            self.assertEqual(len(operations), 1)
            self.assertIsInstance(operations[0], SeparateDatabaseAndState)
            self.assertEqual(operations[0].database_operations, [])

    def test_untrusted_filename_cannot_supply_a_directory(self):
        for filename in ("../../secret.jpeg", r"C:\private\secret.PNG", "../../.env", "a." + "x" * 300):
            with self.subTest(filename=filename):
                name = StoreMediaPath("shop/branding")(ShopSettings(store=self.store), filename)
                self.assertEqual(len(name.split("/")), 5)
                self.assertNotIn("..", name)
                self.assertLessEqual(len(name), 100)

    def test_directory_policy_rejects_path_traversal(self):
        for directory in ("../other", "/absolute", "a//b", r"a\b"):
            with self.assertRaises(ValueError):
                StoreMediaPath(directory)

    def test_callable_survives_migration_serialization(self):
        path = StoreMediaPath("products/gallery")
        dotted, args, kwargs = path.deconstruct()
        self.assertEqual(import_string(dotted)(*args, **kwargs), path)

    def test_legacy_original_lookup_unchanged(self):
        self.assertEqual(original_storage_name("products/gallery/old.jpg"), "products/originals/old.jpg")

    def test_platform_editorial_cover_has_its_own_namespace(self):
        self.assertTrue(blog_cover_upload_path(apps.get_model("blog.BlogPost")(), "x.jpg").startswith("platform/blog/covers/"))

    def test_private_storage_has_no_public_url_even_with_media_url(self):
        with override_settings(MEDIA_URL="/media/"):
            with self.assertRaises(ValueError):
                PrivateFileSystemStorage().url("stores/anything/document.xlsx")


class StoreMediaStorageTests(TestCase):
    def create_product(self, store, **kwargs):
        vendor = Vendor.objects.create(store=store, name="Vendor", slug="media-vendor")
        category = Category.objects.create(store=store, name="Category", slug="media-category")
        return Product.objects.create(store=store, vendor=vendor, category=category, **kwargs)

    def test_product_original_thumbnail_and_deletion_are_store_scoped(self):
        with tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            store = Store.objects.get(slug="akhlaghi")
            product = self.create_product(store, name="Image", slug="media-test", sku="MEDIA-TEST", price=1)
            uploaded = _make_image_file()
            raw = uploaded.read()
            uploaded.seek(0)
            image = add_product_image(product, uploaded)
            names = (image.image.name, image.thumbnail.name, original_storage_name(image.image.name))
            for name in names:
                self.assertTrue(name.startswith(f"stores/{store.public_id.hex}/products/"))
                self.assertTrue(Path(root, name).is_file())
            self.assertEqual(Path(root, names[2]).read_bytes(), raw)
            unrelated = Path(root, "stores", uuid4().hex, "products", "originals", Path(names[2]).name)
            unrelated.parent.mkdir(parents=True)
            unrelated.write_bytes(b"other shop")
            delete_product_image(image)
            self.assertTrue(all(not Path(root, name).exists() for name in names))
            self.assertEqual(unrelated.read_bytes(), b"other shop")

    def test_private_exports_and_imports_use_private_root(self):
        # Instantiate storage inside the override: no write to the project's data directories.
        with tempfile.TemporaryDirectory() as private, tempfile.TemporaryDirectory() as public:
            with override_settings(PRIVATE_MEDIA_ROOT=private, MEDIA_ROOT=public):
                store = Store.objects.get(slug="akhlaghi")
                storage = PrivateFileSystemStorage()
                for model, field_name in ((ExportJob, "file"), (ImportJob, "source_file"), (ImportJob, "error_report_file")):
                    field = model._meta.get_field(field_name)
                    name = field.generate_filename(model(store=store), "report.xlsx")
                    stored = storage.save(name, ContentFile(b"private"))
                    self.assertTrue(Path(private, stored).exists())
                    self.assertFalse(Path(public, stored).exists())

    def test_existing_database_names_and_files_are_not_rewritten_on_save(self):
        with tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            store = Store.objects.get(slug="akhlaghi")
            product = self.create_product(store, name="Old", slug="legacy-image", sku="LEGACY-MEDIA", price=1)
            name = "products/gallery/legacy.jpg"
            path = Path(root, name)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"legacy bytes")
            row = ProductImage.objects.create(product=product, image=name)
            row.alt = "Updated caption"
            row.save()
            row.refresh_from_db()
            self.assertEqual(row.image.name, name)
            self.assertEqual(path.read_bytes(), b"legacy bytes")
