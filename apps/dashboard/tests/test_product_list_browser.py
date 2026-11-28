"""Opt-in browser regression for the merchant product list's RTL layout."""
import os
from pathlib import Path
import unittest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Brand, Category, Product, ProductVariant, Vendor
from apps.stores.models import Store, StoreMembership

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


HOST = f"product-list-browser.{settings.RASTISI_ADMIN_DOMAIN_SUFFIX}"


@unittest.skipUnless(os.getenv("RASTISI_PRODUCT_LIST_BROWSER") == "1" and sync_playwright, "opt-in Chromium test")
@override_settings(ALLOWED_HOSTS=[HOST, "localhost", "127.0.0.1"], STATIC_URL="/static/")
class ProductListBrowserTests(StaticLiveServerTestCase):
    def test_rows_actions_and_bulk_selection_fit_the_dashboard(self):
        store = Store.objects.get(slug="akhlaghi")
        store.admin_subdomain = HOST.split(".")[0]
        store.save(update_fields=["admin_subdomain"])
        user = get_user_model().objects.create_user(username="product-list-browser", is_staff=True)
        StoreMembership.objects.create(store=store, user=user, role=StoreMembership.Role.OWNER,
                                       status=StoreMembership.MembershipStatus.ACTIVE, accepted_at=timezone.now())
        vendor = Vendor.objects.create(store=store, name="فروشندهٔ آزمایشی", slug="browser-layout-vendor")
        root = Category.objects.create(store=store, name="گروه اصلی آزمایشی " * 4, slug="browser-layout-root")
        leaf = Category.objects.create(store=store, parent=root, name="زیرگروه نهایی آزمایشی " * 5,
                                       slug="browser-layout-leaf")
        brand = Brand.objects.create(store=store, name="نام‌برندبدون‌فاصله" * 5, slug="browser-layout-brand")
        product = Product.objects.create(store=store, vendor=vendor, category=leaf, brand=brand,
                                         sku="PANEL-" + "9" * 34, slug="browser-layout-product",
                                         name="محصول‌چندتنوعی‌با‌نام‌طولانی‌" * 6,
                                         product_type=Product.ProductType.VARIABLE, price=987654321012)
        ProductVariant.objects.create(product=product, attribute="اندازه", value="متوسط",
                                      sku="V-" + "9" * 60, price=1000)
        client = Client(HTTP_HOST=HOST)
        client.force_login(user)
        base = f"http://{HOST}:{self.server_thread.port}"
        url = base + reverse("dashboard:product-list")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.getenv("RASTISI_CHROMIUM"),
                args=["--no-sandbox", "--no-proxy-server", f"--host-resolver-rules=MAP {HOST} 127.0.0.1"],
            )
            try:
                context = browser.new_context()
                context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value,
                                      "domain": HOST, "path": "/"}])
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                page = context.new_page()
                for width, mode in ((1920, "table"), (1440, "table"), (1280, "table"),
                                    (1024, "grid"), (768, "grid"), (390, "grid"), (320, "grid")):
                    with self.subTest(width=width):
                        page.set_viewport_size({"width": width, "height": 950})
                        with page.expect_response(lambda res: "merchant_product_list.css" in res.url) as asset:
                            response = page.goto(url)
                        self.assertEqual(asset.value.status, 200)
                        self.assertEqual(response.status, 200)
                        page.evaluate("document.fonts.ready")
                        row = page.locator(".product-table-responsive tbody tr").filter(has_text=product.sku)
                        expect(row).to_have_css("display", "table-row" if mode == "table" else "grid")
                        self.assertEqual(page.locator("html").get_attribute("dir"), "rtl")
                        expect(row.get_by_role("link", name="ویرایش")).to_be_visible()
                        expect(row.get_by_role("link", name="تنوع‌ها")).to_have_attribute(
                            "href", reverse("dashboard:product-options", args=[product.pk]))
                        page.get_by_label("انتخاب همهٔ کالاهای صفحه").check()
                        expect(row.locator('input[name="product_ids"]')).to_be_checked()
                        more = row.locator("summary")
                        more.press("Enter")
                        expect(more.locator("..")).to_have_attribute("open", "")
                        expect(row.get_by_role("button", name="تصاویر")).to_be_visible()
                        expect(row.get_by_role("link", name="پیش‌نمایش")).to_be_visible()
                        expect(row.get_by_role("button", name="حذف")).to_be_visible()
                        layout = row.evaluate("""row => {
                            const card = row.closest('.products-list-card').getBoundingClientRect();
                            const cells = [...row.querySelectorAll('td')];
                            return {
                                documentFits: document.documentElement.scrollWidth <= window.innerWidth,
                                tableFits: row.closest('table').scrollWidth <= card.width,
                                contentFits: cells.every(cell => cell.scrollWidth <= cell.clientWidth + 1),
                                actionsFit: [...row.querySelectorAll('.products-actions .btn')].every(el => {
                                    const rect = el.getBoundingClientRect();
                                    return rect.left >= card.left && rect.right <= card.right;
                                }),
                            };
                        }""")
                        self.assertTrue(all(layout.values()), layout)
                        if output := os.getenv("RASTISI_PRODUCT_LAYOUT_SCREENSHOTS"):
                            page.evaluate("window.scrollTo(0, 0)")
                            page.screenshot(path=Path(output) / f"products-{width}.png", full_page=True)
                with page.expect_response(lambda res: res.url.endswith(reverse("dashboard:product-images", args=[product.pk]))):
                    row.get_by_role("button", name="تصاویر").click()
                expect(page.locator("#admin-modal-content")).to_be_visible()

                for width in (1024, 768, 390):
                    page.set_viewport_size({"width": width, "height": 950})
                    for route in ("product-variants", "product-options"):
                        with self.subTest(width=width, route=route):
                            response = page.goto(base + reverse(f"dashboard:{route}", args=[product.pk]))
                            self.assertEqual(response.status, 200)
                            page.evaluate("document.fonts.ready")
                            self.assertTrue(page.evaluate(
                                "document.documentElement.scrollWidth <= window.innerWidth"))
            finally:
                browser.close()
