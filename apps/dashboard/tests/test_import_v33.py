from io import BytesIO
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from apps.catalog.models import Attribute, AttributeValue, Brand, Category, Product
from apps.core.models import ImportJob
from apps.dashboard.services import import_v33
from apps.dashboard.tests.test_import_views import ImportViewTestCase
from apps.dashboard.tests.xlsx_helpers import XLSX_MIME
from apps.orders.models import TaxClass
from apps.stores.models import Store


class UnifiedImportFlow(ImportViewTestCase):
    def setUp(self):
        super().setUp()
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.enterContext(override_settings(PRIVATE_MEDIA_ROOT=storage.name, MEDIA_ROOT=storage.name))

    def download(self):
        response = self.client.get(reverse("dashboard:import-template", args=["products"]))
        self.assertEqual(response.status_code, 200)
        return load_workbook(BytesIO(response.content))

    def upload(self, rows, definitions=()):
        wb = self.download()
        sheet = wb[import_v33.SHEET]
        for index, row in enumerate(rows, 2):
            for key, value in row.items():
                sheet.cell(index, import_v33.KEYS.index(key) + 1, value)
        lists = wb[import_v33.LIST_SHEET]
        for index, row in enumerate(definitions, 20):
            for key, value in row.items():
                lists.cell(index, import_v33.KEYS.index(key) + 1, value)
        data = BytesIO()
        wb.save(data)
        return self.upload_bytes(data.getvalue())

    def upload_bytes(self, data):
        response = self.client.post(reverse("dashboard:import-upload"), {
            "file": SimpleUploadedFile("data.xlsx", data, content_type=XLSX_MIME), "import_type": "products", "mode": "upsert",
        })
        self.assertEqual(response.status_code, 302)
        return ImportJob.objects.filter(store=self.store).latest("pk")

    def confirm(self, job):
        response = self.client.post(reverse("dashboard:import-execute", args=[job.pk]))
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()

    def synthetic_rows(self):
        return [
            dict(group="TEST33", name="کالای آزمایشی", category="دسته ساخته‌شده", child="برگ آزمایشی",
                 brand="برند آزمایشی", axis1="رنگ", value1="قرمز", axis2="اندازه", value2="کوچک",
                 price=250000, stock=7, barcode="001234", specs="جنس: پنبه", status="فعال"),
            dict(group="TEST33", axis1="رنگ", value1="آبی", axis2="اندازه", value2="بزرگ",
                 price=295001, stock=2),
        ]

    def synthetic_definitions(self):
        return [
            dict(category="دسته ساخته‌شده", child="برگ آزمایشی", brand="برند آزمایشی", axis1="رنگ", value1="قرمز"),
            dict(value1="آبی"),
            dict(axis1="اندازه", value1="کوچک"),
            dict(value1="بزرگ"),
        ]

    def test_template_keeps_layout_and_only_store_refs(self):
        foreign = Store.objects.create(name="مستأجر دوم", slug="v33-second")
        Brand.objects.create(store=foreign, name="FORBIDDEN-TENANT", slug="forbidden")
        TaxClass.objects.create(store=foreign, name="FORBIDDEN-TAX", code="forbidden")
        Attribute.objects.create(store=foreign, label="FORBIDDEN-AXIS", code="forbidden", is_variant_axis=True)
        axis = Attribute.objects.create(store=self.store, label="طرح مصنوعی", code="v33-style", is_variant_axis=True,
                                        data_type=Attribute.DataType.SELECT)
        AttributeValue.objects.create(attribute=axis, label="راه‌راه")
        TaxClass.objects.create(store=self.store, name="مالیات آزمایشی", code="v33-tax")
        second = Category.objects.create(store=self.store, name="ریشه دوم", slug="v33-root-two")
        Category.objects.create(store=self.store, name="برگ دوم", slug="v33-leaf-two", parent=second)
        restricted = Attribute.objects.create(store=self.store, label="محور دوم", code="v33-private-axis",
                                               category=second, is_variant_axis=True, data_type=Attribute.DataType.SELECT)
        AttributeValue.objects.create(attribute=restricted, label="مقدار دوم")
        wb = self.download()
        approved = import_v33._template()
        self.assertEqual(wb.sheetnames, approved.sheetnames)
        self.assertEqual([c.value for c in wb[import_v33.SHEET][1]], [c.value for c in approved[import_v33.SHEET][1]])
        self.assertTrue(all(s.sheet_view.rightToLeft for s in wb))
        self.assertIsNone(wb[import_v33.SHEET]["A2"].value)
        values = " ".join(str(value) for row in wb["فهرست‌ها"].values for value in row if value is not None)
        for label in ("برند", "راه‌راه", "مالیات آزمایشی", "زیردسته"):
            self.assertIn(label, values)
        self.assertNotIn("FORBIDDEN", values)
        rules = {str(rule.sqref): rule.formula1 for rule in wb[import_v33.SHEET].data_validations.dataValidation}
        for column, source in (("D", "$C$1"), ("G", "$F$1"), ("I", "$H$1"), ("K", "$J$1")):
            name = rules[f"{column}2:{column}400"]
            self.assertLess(len(name), 255)
            self.assertIn(source, wb.defined_names[name].attr_text)
        if destination := os.getenv("RASTISI_V33_SYNTHETIC_SAMPLE"):
            wb.save(Path(destination))

    def test_preview_confirm_exact_variants_and_replay(self):
        rows = self.synthetic_rows()
        job = self.upload(rows, self.synthetic_definitions())
        self.assertEqual((job.valid_rows, job.invalid_rows), (6, 0), list(job.row_results.values("errors")))
        self.assertFalse(Product.objects.filter(store=self.store, sku="TEST33").exists())
        self.assertFalse(Brand.objects.filter(store=self.store, name="برند آزمایشی").exists())
        self.assertFalse(Category.objects.filter(store=self.store, name="برگ آزمایشی").exists())
        self.assertFalse(Attribute.objects.filter(store=self.store, label="رنگ").exists())
        self.assertContains(self.client.get(reverse("dashboard:import-detail", args=[job.pk])), "برند آزمایشی")
        self.confirm(job)
        self.assertEqual(job.status, ImportJob.Status.COMPLETED, list(job.row_results.values("errors")))
        product = Product.objects.get(store=self.store, sku="TEST33")
        self.assertEqual(product.category.parent.name, "دسته ساخته‌شده")
        self.assertEqual(product.specifications.get().value, "پنبه")
        variants = list(product.variants.order_by("price"))
        self.assertEqual([(int(v.price), v.stock, v.option_values.count()) for v in variants],
                         [(250000, 7, 2), (295001, 2, 2)])
        self.assertEqual(variants[0].barcode, "001234")
        self.assertTrue(all(v.sku for v in variants))
        v = variants[0]
        v.price = 310000
        v.save(update_fields=["price"])
        with job.source_file.open("rb") as source:
            replay = self.upload_bytes(source.read())
        self.confirm(replay)
        self.assertEqual((replay.status, replay.skipped_rows), (ImportJob.Status.COMPLETED, replay.total_rows))
        v.refresh_from_db()
        self.assertEqual(v.price, 310000)
        self.assertEqual(product.variants.count(), 2)
        updated = [dict(group=product.sku, operation="به‌روزرسانی", product_id=product.pk, variant_id=v.pk, price=320005)]
        job = self.upload(updated)
        self.confirm(job)
        self.assertEqual(job.updated_rows, 1, list(job.row_results.values("errors")))
        v.refresh_from_db()
        self.assertEqual(v.price, 320005)

    def test_invalid_row_and_last_write_failure_leave_no_partial_records(self):
        rows = self.synthetic_rows()
        invalid = self.upload([rows[0], dict(rows[1], stock=-1)], self.synthetic_definitions())
        self.assertEqual(invalid.row_results.get(row_number=3).errors[0][:2], "M:")
        self.confirm(invalid)
        self.assertFalse(Product.objects.filter(store=self.store, sku="TEST33").exists())
        job = self.upload(rows, self.synthetic_definitions())
        original = import_v33._save_variant

        def save(store, product, line):
            if line.number == 3:
                raise import_v33.InvalidRow("price", "آزمایش شکست در ردیف دوم")
            return original(store, product, line)

        with patch.object(import_v33, "_save_variant", side_effect=save):
            self.confirm(job)
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertEqual(job.row_results.get(row_number=3).errors[0][:2], "L:")
        self.assertFalse(Product.objects.filter(store=self.store, sku="TEST33").exists())
        self.assertFalse(Brand.objects.filter(store=self.store, name="برند آزمایشی").exists())
        self.assertFalse(Category.objects.filter(store=self.store, name="برگ آزمایشی").exists())
        self.assertFalse(Attribute.objects.filter(store=self.store, label="رنگ").exists())

    def test_simple_and_duplicate_combination(self):
        simple = self.upload([dict(group="S33", name="ساده", category=self.category.name, price=99, stock=4)])
        self.confirm(simple)
        self.assertEqual(simple.status, ImportJob.Status.COMPLETED, list(simple.row_results.values("errors")))
        product = Product.objects.get(store=self.store, sku="S33")
        self.assertEqual((product.price, product.stock, product.variants.count()), (99, 4, 0))
        rows = self.synthetic_rows()
        job = self.upload([rows[0], rows[0]], self.synthetic_definitions())
        self.assertEqual(job.invalid_rows, 1)
        self.assertIn("تکرار", job.row_results.get(row_number=3).errors[0])

    def test_undeclared_and_mismatched_selections_block_product(self):
        row = self.synthetic_rows()[0]
        for field, value, letter in (
            ("brand", "تعریف نشده", "E"),
            ("child", "برگ اشتباه", "D"),
            ("value1", "کوچک", "G"),
        ):
            with self.subTest(field=field):
                job = self.upload([dict(row, **{field: value})], self.synthetic_definitions())
                self.assertTrue(job.row_results.get(row_number=2).errors[0].startswith(letter + ":"))
                self.confirm(job)
        self.assertFalse(Product.objects.filter(sku="TEST33").exists())
        self.assertFalse(Brand.objects.filter(store=self.store, name="برند آزمایشی").exists())

    def test_bad_definition_blocks_even_valid_products_and_reports_sheet_row(self):
        other = Store.objects.create(name="مستأجر دیگر", slug="v33-private")
        foreign = Brand.objects.create(store=other, name="برند خصوصی", slug="foreign-v33")
        original = Category.objects.get(pk=self.category.pk)
        job = self.upload(
            [dict(group="SAFE33", name="ساده", category=self.category.name, price=99, stock=4)],
            [dict(brand="برند پیشنهادی", category="ریشه دیگر", child="زیردسته"),
             dict(brand="برند پیشنهادی"), dict(brand=str(foreign.pk)), dict(axis1="=1+1")],
        )
        rows = {row.normalized_data_summary.get("sheet_row"): row for row in job.row_results.all()}
        for number, column in ((20, "D"), (21, "E"), (22, "E"), (23, "F")):
            self.assertTrue(rows[number].errors[0].startswith(column + ":"), rows[number].errors)
        self.assertContains(self.client.get(reverse("dashboard:import-detail", args=[job.pk])), "فهرست‌ها")
        self.confirm(job)
        self.assertFalse(Product.objects.filter(sku="SAFE33").exists())
        self.assertFalse(Brand.objects.filter(name="برند پیشنهادی").exists())
        self.assertFalse(Category.objects.filter(name="ریشه دیگر").exists())
        self.assertEqual(Category.objects.get(pk=original.pk).name, original.name)
        self.assertEqual(Brand.objects.get(pk=foreign.pk).store_id, other.pk)

    def test_duplicate_value_under_same_attribute_is_invalid(self):
        job = self.upload(self.synthetic_rows(), [*self.synthetic_definitions(), dict(value1="بزرگ")])
        self.assertIn("تکراری", job.row_results.get(row_number=import_v33.LIST_ROW_OFFSET + 24).errors[0])
        self.confirm(job)
        self.assertFalse(Attribute.objects.filter(store=self.store, label="اندازه").exists())

    def test_definition_save_failure_rolls_back_earlier_definitions(self):
        job = self.upload(self.synthetic_rows(), self.synthetic_definitions())
        original = import_v33.save_definition

        def save(store, kind, name, parent_id):
            if kind == "value1":
                raise ValueError("synthetic definition failure")
            return original(store, kind, name, parent_id)

        with patch.object(import_v33, "save_definition", side_effect=save):
            self.confirm(job)
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        result = job.row_results.get(row_number=import_v33.LIST_ROW_OFFSET + 20)
        self.assertEqual(result.normalized_data_summary["sheet_row"], 20)
        self.assertTrue(result.errors[0].startswith("G:"))
        self.assertFalse(Brand.objects.filter(store=self.store, name="برند آزمایشی").exists())
        self.assertFalse(Category.objects.filter(store=self.store, name="دسته ساخته‌شده").exists())
        self.assertFalse(Attribute.objects.filter(store=self.store, label="رنگ").exists())
        self.assertFalse(Product.objects.filter(store=self.store, sku="TEST33").exists())
