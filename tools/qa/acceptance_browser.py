"""Real-browser acceptance against an ephemeral Django live server."""
from io import StringIO
import json
import os
import secrets
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import Client, modify_settings, override_settings
from playwright.sync_api import sync_playwright

from apps.catalog.models import Category, IndustryTemplate, Product, StoreIndustryInstallation
from apps.catalog.services.industry_template_service import install_industry_template
from apps.notifications.models import InboxItem, NotificationEvent
from apps.orders.models import Order, PaymentGateway, ReturnRequest, Transaction
from apps.portal.models import PlatformAuditLogEntry
from apps.portal.tests.test_onboarding_journey import make_rich_template
from apps.stores.models import Store

PLATFORM = "platformadmins.rastisi.localhost"
MERCHANT = "qa-bike.rastisi.localhost"
OTHER = "qa-other.rastisi.localhost"


@modify_settings(MIDDLEWARE={"append": "apps.core.availability_evidence.AvailabilityEvidenceMiddleware"})
@override_settings(DEBUG=True, ALLOWED_HOSTS=[".rastisi.localhost", "mybikestore.test", "127.0.0.1"])
class IntegratedBrowserAcceptance(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.driver = sync_playwright().start()
        cls.addClassCleanup(cls.driver.stop)
        options = {"args": [
            "--no-sandbox", "--no-proxy-server",
            "--host-resolver-rules=MAP *.rastisi.localhost 127.0.0.1, MAP mybikestore.test 127.0.0.1",
        ]}
        if os.environ.get("SFB_CHROMIUM_PATH"):
            options["executable_path"] = os.environ["SFB_CHROMIUM_PATH"]
        cls.browser = cls.driver.chromium.launch(**options)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.output = Path(os.environ["RASTISI_ACCEPTANCE_OUTPUT"])
        call_command("seed_qa_notifications", password=secrets.token_urlsafe(32), stdout=StringIO())
        self.shop = Store.objects.get(slug="qa-bike")
        self.other = Store.objects.get(slug="qa-other")

    def session(self, host, username=None):
        # Browser carries a real session. Signup itself is covered separately.
        client = Client(HTTP_HOST=host)
        if username:
            client.force_login(get_user_model().objects.get(username=username))
        context = self.browser.new_context(viewport={"width": 1440, "height": 1000}, locale="fa-IR")
        port = self.live_server_url.rsplit(":", 1)[-1]
        origin = f"http://{host}:{port}"
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
        context.set_default_timeout(12000)
        self.addCleanup(context.close)
        if username:
            context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": origin}])
        page = context.new_page()
        self.addCleanup(lambda: page.screenshot(path=self.output / f"{self._testMethodName}-{username}-last.png"))
        return page, origin

    def evidence(self, page, name):
        page.screenshot(path=self.output / f"{name}.png")

    def record(self, name, **values):
        (self.output / f"{name}.json").write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")

    @override_settings(RASTISI_NOTIF_WEBPUSH_ENABLED=True)
    def test_platform_owner_controls_web_push_pilot(self):
        from django.urls import reverse

        from apps.notifications.services import webpush_policy
        from apps.notifications.services.scope import key_for

        page, origin = self.session(PLATFORM, "09120000001")
        configuration = reverse("portal_platform_admin:configuration", urlconf="shop_core.urls_platform_admin")
        detail = reverse("portal_platform_admin:store-detail", args=[self.shop.public_id], urlconf="shop_core.urls_platform_admin")
        self.assertFalse(webpush_policy.allowed(self.shop))
        for route in (configuration, detail):
            page.goto(origin + route)
            page.locator('button[name="webpush_policy"][value="on"]').click()
            page.locator('button[name="webpush_policy"][value="off"]').wait_for()
        self.assertTrue(webpush_policy.allowed(self.shop))
        self.assertFalse(webpush_policy.allowed(self.other))
        self.evidence(page, "web-push-pilot")
        page.locator('button[name="webpush_policy"][value="off"]').click()
        page.locator('button[name="webpush_policy"][value="on"]').wait_for()
        self.assertFalse(webpush_policy.allowed(self.shop))
        self.assertEqual(PlatformAuditLogEntry.objects.filter(action_code="webpush.policy_updated",
                                                               object_id=key_for(self.shop)).count(), 2)
        self.record("web-push-pilot", store_gate=True, other_store_denied=True, disable_audited=True)

    def test_platform_version_and_tenant_permissions(self):
        original = make_rich_template()
        install_industry_template(self.shop, original)
        before = list(Category.objects.filter(store=self.shop).order_by("pk").values())
        page, origin = self.session(PLATFORM, "09120000001")
        page.goto(f"{origin}/industries/{original.pk}/new-version/")
        page.locator('[data-fields="header"]').get_by_label("نام صنف").fill("پوشاک پذیرش")
        page.locator('[data-add="categories"]').click()
        added = page.locator('[data-list="categories"] > fieldset').last
        added.get_by_label("نام دسته").fill("زیرگروه پذیرش")
        added.get_by_label("دستهٔ والد").select_option("root")
        for attribute_code in ("size", "material", "brand"):
            page.locator('[data-add="mappings"]').click()
            mapping = page.locator('[data-list="mappings"] > fieldset').last
            mapping.locator('select[data-reference="category"]').select_option(label="زیرگروه پذیرش")
            mapping.locator('select[data-reference="attribute"]').select_option(attribute_code)
        size = page.locator('[data-list="attributes"] > fieldset').last  # canonical codes are sorted; size is last
        size.get_by_role("button", name="افزودن مقدار", exact=True).click()
        size.get_by_label("برچسب مقدار").last.fill("XL")
        self.evidence(page, "industry-form")
        page.get_by_role("button", name="اعتبارسنجی و ذخیره نسخه").click()
        page.wait_for_url(lambda url: url.rstrip("/").rsplit("/", 1)[-1].isdigit())
        new = IndustryTemplate.objects.get(slug=original.slug, version=2)
        self.assertFalse(new.is_active)
        self.assertEqual(new.categories.get(name="زیرگروه پذیرش").parent.code, "root")
        self.assertTrue(new.attributes.get(code="size").values.filter(label="XL").exists())
        page.on("dialog", lambda dialog: dialog.accept())
        toggle = page.locator('form:has(input[value="toggle_active"]) button')
        for active in (True, False):
            toggle.click()
            with page.expect_navigation():
                page.get_by_role("alertdialog").get_by_role("button", name="تأیید", exact=True).click()
            new.refresh_from_db()
            self.assertEqual(new.is_active, active)
        self.assertEqual(StoreIndustryInstallation.objects.get(store=self.shop).industry_template_id, original.pk)
        self.assertEqual(list(Category.objects.filter(store=self.shop).order_by("pk").values()), before)
        self.assertTrue(PlatformAuditLogEntry.objects.filter(action_code="platform_admin.industry_version_created", object_id=str(new.pk)).exists())
        self.evidence(page, "industry-version")
        self.record("industry", version=new.version, final_active=new.is_active,
                    existing_category_count=len(before), changes_audited=True)
        denied, _ = self.session(PLATFORM, "09121110003")
        denied.goto(origin + "/industries/new/")
        self.assertEqual(denied.locator("#industry-author").count(), 0)

    @override_settings(RASTISI_CAPTCHA_ENABLED=True)
    def test_registration_otp_store_and_other_industry(self):
        call_command("ensure_industry_registry", apply=True, verbosity=0)
        page, origin = self.session("rastisi.localhost")
        page.goto(origin + "/register/")
        page.locator("#id_full_name").fill("مالک آزمایشی پذیرش")
        page.locator("#id_phone").fill("09124440091")
        page.locator("#id_accept_terms").check()
        widget = page.locator("altcha-widget")
        widget.locator('input[type="checkbox"]').click()
        page.locator('altcha-widget[data-state="verified"]').wait_for()
        page.locator('form:has(#id_accept_terms) button[type="submit"]').click()
        page.locator("#id_code").fill("123456")
        page.locator('form:has(#id_code) button[type="submit"]').click()
        page.locator("#id_email").fill("acceptance-owner@example.test")
        page.locator("#id_password").fill("Synthetic-Owner-4827!")
        page.locator("#id_password_confirm").fill("Synthetic-Owner-4827!")
        page.locator('form:has(#id_email) button[type="submit"]').click()
        page.wait_for_url("**/app/**")
        from apps.stores.models import StoreMembership

        membership = StoreMembership.objects.get(user__email="acceptance-owner@example.test")
        store = membership.store
        self.assertEqual(membership.role, "owner")
        page.goto(origin + f"/app/stores/{store.public_id}/onboarding/industry/")
        other = IndustryTemplate.objects.filter(slug="other", is_active=True).latest("version")
        page.locator(f'[data-ob-card][data-id="{other.pk}"]').click()
        page.fill("#ob-custom-industry-name", "کارگاه سفال پذیرش")
        page.locator("[data-ob-confirm-check]").check()
        self.evidence(page, "signup-industry")
        page.locator("[data-ob-install-submit]").click()
        page.wait_for_url("**/onboarding/template/**")
        self.assertEqual(StoreIndustryInstallation.objects.get(store=store).custom_industry_name, "کارگاه سفال پذیرش")
        self.assertEqual(StoreMembership.objects.filter(user=membership.user).count(), 1)
        self.record("signup", altcha=True, otp="local-console", owner_memberships=1, industry="other")

    def test_order_actions_and_scoped_notifications(self):
        from django.urls import reverse
        from django.utils import timezone

        from apps.cart.models import Cart, CartItem
        from apps.customers.models import Customer
        from apps.stores.models import StoreDomain

        StoreDomain.objects.filter(store=self.shop, hostname="mybikestore.test").update(
            verification_status=StoreDomain.VerificationStatus.VERIFIED, verified_at=timezone.now(),
        )
        PaymentGateway.objects.filter(store=self.shop, slug="qa-cod").update(slug="cod")
        buyer = Customer.objects.get(phone="09125550001")
        product = Product.objects.get(store=self.shop, slug="qa-bike-1")
        cart = Cart.objects.create(customer=buyer)
        CartItem.objects.create(cart=cart, product=product, quantity=1, unit_price=product.price)
        customer, public = self.session("mybikestore.test", "09125550001")
        customer.goto(public + reverse("orders:checkout-step1"))
        for key, value in {
            "receiver_name": "مشتری آزمایشی", "phone": buyer.phone, "province": "تهران", "city": "تهران",
            "postal_code": "1111111111", "full_address": "نشانی کاملاً مصنوعی پذیرش",
        }.items():
            customer.locator(f'#address-fields [name="{key}"]').fill(value)
        self.evidence(customer, "checkout")
        with customer.expect_response(lambda reply: "/checkout/pay/" in reply.url and reply.request.method == "POST") as posted:
            customer.locator('button[hx-post="/checkout/pay/"]').click()
        self.assertIn("/checkout/order/", posted.value.headers.get("hx-redirect", ""))
        order = Order.objects.get(store=self.shop)
        customer.wait_for_url("**/checkout/order/**/result/")
        customer.goto(public + reverse("customers:account-order-detail", args=[order.code]))
        customer.locator('form[action$="/request-cancel/"] button').click()
        customer.wait_for_load_state("domcontentloaded")
        order.refresh_from_db()
        self.assertIsNotNone(order.cancellation_requested_at)
        self.assertEqual(order.status, Order.Status.PENDING)
        merchant, origin = self.session(MERCHANT, "09121110003")
        merchant.on("dialog", lambda dialog: dialog.accept())
        target = origin + f"/admin-portal/orders/{order.code}/"
        merchant.goto(target)
        merchant.locator('form[action$="/cod-payment/"] button').click()
        merchant.wait_for_load_state("domcontentloaded")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(Transaction.objects.filter(order=order, status=Transaction.Status.OK).count(), 1)
        merchant.goto(target + "returns/new/")
        merchant.locator('input[name^="quantity_"]').first.fill("1")
        merchant.locator('form:has(input[name^="quantity_"]) button[type="submit"]').click()
        merchant.wait_for_load_state("domcontentloaded")
        self.assertTrue(ReturnRequest.objects.filter(order=order, store=self.shop).exists())
        keys = {"order.created", "order.cancel_requested", "payment.succeeded", "return.requested"}
        self.assertEqual(set(NotificationEvent.objects.filter(store=self.shop, type_key__in=keys).values_list("type_key", flat=True)), keys)
        self.assertFalse(InboxItem.objects.filter(user__username="09121110004", event__type_key__in=keys).exists())
        merchant.goto(origin + "/admin-portal/inbox/")
        self.evidence(merchant, "merchant-notifications")
        item = InboxItem.objects.get(user__username="09121110003", event__store=self.shop, event__type_key="payment.succeeded")
        merchant.goto(origin + f"/admin-portal/inbox/{item.public_id}/go/?push=1")
        merchant.wait_for_url(target)
        denied, alternate = self.session(OTHER, "09121110004")
        self.assertEqual(denied.goto(alternate + f"/admin-portal/orders/{order.code}/").status, 404)
        self.record("commerce", payment="synthetic COD confirmation", events=sorted(keys),
                    cross_tenant_order_status=404, physical_push=False)

    def test_browser_request_emits_sanitized_route_evidence(self):
        page, origin = self.session(PLATFORM, "09120000001")
        request_id = "f" * 32
        marker = "SYNTHETIC-PRIVATE-QUERY"
        page.set_extra_http_headers({"X-Request-ID": request_id, "Authorization": "SYNTHETIC-PRIVATE-HEADER"})
        with self.assertLogs("rastisi.availability", level="WARNING") as captured:
            page.goto(origin + "/industries/?q=" + marker)
        rows = [json.loads(record.getMessage()) for record in captured.records]
        events = [row["event"] for row in rows if row.get("route") == "industries" and row.get("request_id") == request_id]
        self.assertIn("view_entered", events)
        log = "\n".join(captured.output)
        self.assertNotIn(marker, log)
        self.assertNotIn("SYNTHETIC-PRIVATE-HEADER", log)
        self.record("diagnostic-browser", route="industries", matched_request=True, phase_events=events,
                    redaction_checked=True)

    def test_service_worker_payload_expired_and_denied_simulation(self):
        page, origin = self.session(MERCHANT, "09121110003")
        page.goto(origin + "/admin-portal/inbox/")
        script = (Path(__file__).resolve().parents[2] /
                  "apps/notifications/static/notifications/sw.js").read_text(encoding="utf-8")
        outcomes = page.evaluate("""async source => {
            const opaque = "11111111-2222-3333-4444-555555555555";
            const result = [];
            for (const status of [401, 404, 200]) {
                const handlers = {};
                const notices = [];
                const mock = {addEventListener: (key, handler) => { handlers[key] = handler; },
                    registration: {showNotification: (title, options) => notices.push({title, options})}};
                const response = async () => ({status, ok: status === 200,
                    headers: {get: () => "application/json"},
                    json: async () => ({item: {id: opaque, title: "رخداد آزمایشی"}})});
                new Function("self", "fetch", source)(mock, response);
                let task;
                handlers.push({data: {json: () => ({item: opaque})}, waitUntil: value => { task = value; }});
                await task;
                result.push(notices);
            }
            return result;
        }""", script)
        self.assertEqual(outcomes[0][0]["title"], "در پنل راستی‌سی اعلان تازه‌ای دارید")
        self.assertEqual(outcomes[1], [])
        self.assertEqual(outcomes[2][0]["title"], "رخداد آزمایشی")
        self.assertTrue(outcomes[2][0]["options"]["data"]["url"].endswith("/go/?push=1"))
        self.record("push-simulation", expired="generic", denied="no notice", authorized="exact item",
                    physical_delivery="NOT_RUN")
