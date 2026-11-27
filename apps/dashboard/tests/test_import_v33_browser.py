"""Optional Chromium test against the real dashboard and a disposable database."""
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.catalog.models import Brand, Category, Product, Vendor
from apps.core.models import ImportJob
from apps.dashboard.services.import_v33 import SHEET
from apps.stores.models import Store, StoreMembership

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


HOST = f"import-v33-browser.{settings.RASTISI_ADMIN_DOMAIN_SUFFIX}"


@unittest.skipUnless(os.getenv("RASTISI_V33_BROWSER") == "1" and sync_playwright, "opt-in Chromium test")
@override_settings(ALLOWED_HOSTS=[HOST, "localhost", "127.0.0.1"])
class UnifiedImportBrowser(StaticLiveServerTestCase):
    def test_download_preview_and_confirm(self):
        store = Store.objects.get(slug="akhlaghi")
        store.admin_subdomain = "import-v33-browser"
        store.save(update_fields=["admin_subdomain"])
        Vendor.objects.create(store=store, name="نمونه", slug="v33-browser")
        Category.objects.create(store=store, name="دسته مرورگر", slug="v33-browser")
        user = get_user_model().objects.create_user(username="v33-browser", is_staff=True)
        StoreMembership.objects.create(store=store, user=user, role=StoreMembership.Role.OWNER,
                                       status=StoreMembership.MembershipStatus.ACTIVE, accepted_at=timezone.now())
        client = Client(HTTP_HOST=HOST)
        client.force_login(user)
        base = f"http://{HOST}:{self.server_thread.port}"
        with tempfile.TemporaryDirectory() as storage, override_settings(PRIVATE_MEDIA_ROOT=storage), sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ["RASTISI_CHROMIUM"],
                args=["--no-sandbox", "--no-proxy-server", f"--host-resolver-rules=MAP {HOST} 127.0.0.1"],
            )
            context = browser.new_context(accept_downloads=True)
            context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value,
                                  "domain": HOST, "path": "/"}])
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
            page = context.new_page()
            output = os.environ.get("RASTISI_ACCEPTANCE_OUTPUT")
            if output:
                output = Path(output)
            page.goto(base + reverse("dashboard:import-upload"))
            href = reverse("dashboard:import-template", args=["products"])
            with page.expect_download() as downloaded:
                page.locator(f'a[href="{href}"]').first.click()
            wb = load_workbook(BytesIO(downloaded.value.path().read_bytes()))
            self.assertEqual(len(wb.sheetnames), 5)
            self.assertTrue(all(sheet.sheet_view.rightToLeft for sheet in wb))
            sheet = wb[SHEET]
            for col, value in {1: "BROWSER33", 2: "کالای مرورگر", 3: "دسته مرورگر", 5: "برند جدید مرورگر",
                               12: 120001, 13: 3}.items():
                sheet.cell(2, col, value)
            wb["فهرست‌ها"]["D20"] = "برند جدید مرورگر"
            binary = BytesIO()
            wb.save(binary)
            wb.close()
            if output:
                (output / "excel-v33-synthetic.xlsx").write_bytes(binary.getvalue())
            upload = page.locator('input[name="file"]')
            upload.set_input_files({"name": "browser.xlsx", "mimeType": "application/octet-stream", "buffer": binary.getvalue()})
            page.locator("form").filter(has=upload).locator('button[type="submit"]').click()
            job = ImportJob.objects.latest("pk")
            page.wait_for_url("**" + reverse("dashboard:import-detail", args=[job.pk]))
            if output:
                page.screenshot(path=output / "excel-preview.png")
            self.assertFalse(Brand.objects.filter(store=store, name="برند جدید مرورگر").exists())
            self.assertFalse(Product.objects.filter(store=store, sku="BROWSER33").exists())
            execute = reverse("dashboard:import-execute", args=[job.pk])
            page.get_by_test_id("final-import-open").click()
            with page.expect_response(lambda response: response.url.endswith(execute)) as confirmed:
                page.locator(f'form[action="{execute}"] button[type="submit"]').click()
            self.assertEqual(confirmed.value.status, 302)
            product = Product.objects.get(store=store, sku="BROWSER33")
            self.assertEqual((product.price, product.stock, product.brand.name), (120001, 3, "برند جدید مرورگر"))
            if output:
                page.screenshot(path=output / "excel-confirmed.png")
            page.goto(base + reverse("dashboard:import-upload"))
            upload = page.locator('input[name="file"]')
            upload.set_input_files({"name": "browser.xlsx", "mimeType": "application/octet-stream", "buffer": binary.getvalue()})
            page.locator("form").filter(has=upload).locator('button[type="submit"]').click()
            replay = ImportJob.objects.filter(store=store).latest("pk")
            page.wait_for_url("**" + reverse("dashboard:import-detail", args=[replay.pk]))
            row = replay.row_results.get(row_number=2)
            self.assertEqual(row.normalized_data_summary["action"], "skip")
            self.assertEqual(Product.objects.filter(store=store, sku="BROWSER33").count(), 1)
            if output:
                page.screenshot(path=output / "excel-reimport.png")
                (output / "excel.json").write_text(json.dumps({
                    "sheets": 5, "preview_writes": 0, "price_toman": 120001, "stock": 3,
                    "reimport": "skip", "same_sku_products": 1,
                }, indent=2), encoding="utf-8")
            browser.close()
