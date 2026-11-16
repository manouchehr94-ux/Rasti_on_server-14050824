"""Synthetic-only official badge contract: parsing, encrypted persistence and tenant UI."""
from html.parser import HTMLParser
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.content.models import FooterSettings
from apps.core.models import ShopSettings
from apps.portal.context_processors import platform_enamad_verification
from apps.portal.forms import PlatformConfigurationForm
from apps.portal.models import PlatformConfiguration, PlatformAuditLogEntry
from apps.stores.models import Store, StoreDomain, StoreIntegrationConnection, StoreMembership
from apps.stores.services import integration_service
from apps.stores.services.enamad_verification_service import EnamadBadgeError, parse_enamad_badge_snippet

BADGE_ID = '1122863'
BADGE_CODE = 'SyntheticBadgeCode123456'
META = '<meta name="enamad" content="synthetic-technical-value">'


def snippet(badge_id=BADGE_ID, code=BADGE_CODE):
    return (f"<a referrerpolicy='origin' target='_blank' href='https://trustseal.enamad.ir/?id={badge_id}&Code={code}'>"
            f"<img referrerpolicy='origin' src='https://trustseal.enamad.ir/logo.aspx?id={badge_id}&Code={code}' alt='' style='cursor:pointer' code='{code}'></a>")


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def assert_badge(case, html):
    tags = Tags(html).tags
    anchors = [a for t, a in tags if t == 'a' and 'trustseal.enamad.ir' in a.get('href', '')]
    images = [a for t, a in tags if t == 'img' and 'trustseal.enamad.ir' in a.get('src', '')]
    case.assertEqual(len(anchors), 1)
    case.assertEqual(len(images), 1)
    a, image = anchors[0], images[0]
    case.assertEqual(a['referrerpolicy'], 'origin')
    case.assertEqual(image['referrerpolicy'], 'origin')
    case.assertEqual(image['code'], BADGE_CODE)
    case.assertEqual(image['style'], 'cursor:pointer')
    case.assertEqual(a['target'], '_blank')
    case.assertNotIn('noreferrer', a.get('rel', ''))
    case.assertEqual(parse_qs(urlsplit(a['href']).query), {'id': [BADGE_ID], 'Code': [BADGE_CODE]})
    case.assertEqual(parse_qs(urlsplit(image['src']).query), {'id': [BADGE_ID], 'Code': [BADGE_CODE]})


class SnippetParserTests(SimpleTestCase):
    def test_official_shape_quotes_entities_and_whitespace(self):
        for raw in (snippet(), snippet().replace("'", '"'), '\n ' + snippet().replace('&Code=', '&amp;Code=').replace('<img ', '\n<img ').replace('></a>', ' />\n</a>') + '\n'):
            with self.subTest(raw=raw):
                badge = parse_enamad_badge_snippet(raw)
                self.assertEqual((badge.enamad_id, badge.auth_code), (BADGE_ID, BADGE_CODE))

    def test_attribute_order_and_inert_presentation_are_allowed(self):
        raw = snippet().replace("<a referrerpolicy=", "<a class='seal' title='نماد' referrerpolicy=").replace("<img referrerpolicy=", "<img width='125' height='136' border='0' referrerpolicy=")
        self.assertEqual(parse_enamad_badge_snippet(raw).auth_code, BADGE_CODE)

    def test_no_network_lookup(self):
        with mock.patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            self.assertIsNotNone(parse_enamad_badge_snippet(snippet()))

    def test_empty_and_oversized_input(self):
        self.assertIsNone(parse_enamad_badge_snippet('  '))
        for raw in (snippet() * 30, ' ' * 4096 + snippet()):
            with self.subTest(size=len(raw)), self.assertRaises(EnamadBadgeError):
                parse_enamad_badge_snippet(raw)

    def test_structural_and_xss_attacks_are_rejected(self):
        payloads = [snippet() * 2, '<div>' + snippet() + '</div>', '<script>alert(1)</script>' + snippet(), snippet() + '<script>x</script>',
                    snippet().replace('<img ', '<img onerror="alert(1)" '), snippet().replace('<a ', '<a onclick="alert(1)" '),
                    snippet().replace('<img ', '<img srcset="https://evil.example/a" '), snippet().replace('cursor:pointer', 'background:url(https://evil.example/)'),
                    snippet().replace('cursor:pointer', 'expression(alert(1))'), snippet().replace('</a>', ''), snippet().replace('</a>', '</img></a>'),
                    '<!--hidden-->' + snippet(), '<!DOCTYPE html>' + snippet(), '<meta name="enamad" content="test">', snippet() + '<',
                    snippet().replace("alt=''", "alt='' alt='again'"), snippet().replace("alt=''", "alt='&lt;script&gt;'"),
                    snippet().replace("target='_blank'", "target='_blank' rel='noreferrer'"),
                    snippet().replace('<img ', '<img style="cursor:pointer" ')]
        for raw in payloads:
            with self.subTest(payload=raw), self.assertRaises(EnamadBadgeError):
                parse_enamad_badge_snippet(raw)

    def test_url_allowlist_and_duplicate_parameters(self):
        for old, new in [('https://trustseal.enamad.ir', 'http://trustseal.enamad.ir'),
                         ('https://trustseal.enamad.ir', 'https://trustseal.enamad.ir.evil.example'),
                         ('https://trustseal.enamad.ir', 'https://trustseal.enamad.ir@evil.example'),
                         ('https://trustseal.enamad.ir', 'https://evil.example@trustseal.enamad.ir'),
                         ('https://trustseal.enamad.ir', 'https://trustseal.enamad.ir:8443'),
                         ('logo.aspx', 'other.aspx'), ('?id=', '?id=999&id='), ('&Code=', '&other=x&Code='),
                         ('&Code=', '&code='), (BADGE_ID, '1%0A'), (BADGE_CODE, 'bad%22code')]:
            with self.subTest(new=new), self.assertRaises(EnamadBadgeError):
                parse_enamad_badge_snippet(snippet().replace(old, new))

    def test_conflicting_pairs_and_image_code(self):
        for raw in (snippet().replace('/logo.aspx?id=' + BADGE_ID, '/logo.aspx?id=999'),
                    snippet().replace("code='" + BADGE_CODE, "code='OTHER"), snippet().replace('&Code=' + BADGE_CODE, '&Code=OTHER', 1)):
            with self.subTest(payload=raw), self.assertRaises(EnamadBadgeError):
                parse_enamad_badge_snippet(raw)


@override_settings(ALLOWED_HOSTS=['*'])
class MerchantSnippetTests(TestCase):
    def setUp(self):
        self.store = Store.objects.get(slug='akhlaghi')
        self.owner = get_user_model().objects.create_user(username='badge-owner-test', password='test-password', is_staff=True)
        StoreMembership.objects.create(store=self.store, user=self.owner, role=StoreMembership.Role.OWNER,
                                       status=StoreMembership.MembershipStatus.ACTIVE, accepted_at=timezone.now())
        self.admin_host = f"{self.store.admin_subdomain}.{settings.RASTISI_ADMIN_DOMAIN_SUFFIX}"
        self.client = Client(HTTP_HOST=self.admin_host)
        self.client.force_login(self.owner)
        self.url = reverse('dashboard:settings-integration-connect', args=['enamad'])
        self.other = Store.objects.create(name='Other', slug='enamad-other', admin_subdomain='enamad-other')
        self.addCleanup(cache.clear)

    def saved(self, store=None):
        return StoreIntegrationConnection.objects.get(store=store or self.store, provider_code='enamad').get_credentials()

    def test_one_paste_http_saves_only_structured_encrypted_values(self):
        with mock.patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            response = self.client.post(self.url, {'enamad_badge_snippet': snippet()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.saved(), {'enamad_id': BADGE_ID, 'enamad_auth_code': BADGE_CODE})
        row = StoreIntegrationConnection.objects.get(store=self.store, provider_code='enamad')
        self.assertNotIn(BADGE_CODE, row.encrypted_credentials)
        self.assertNotIn('enamad_badge_snippet', self.client.session.get('integration_form_error', {}))
        page = self.client.get('/admin-portal/settings/?section=integrations&view=enamad')
        self.assertContains(page, 'کد کامل نماد اینماد')
        self.assertContains(page, 'data-testid="enamad-advanced"')
        self.assertContains(page, 'data-testid="enamad-identified-id"')
        self.assertNotIn(snippet(), page.content.decode())

    def test_badge_meta_and_visibility_are_preserved_independently(self):
        integration_service.connect(store=self.store, provider_code='enamad', actor=self.owner,
                                    values={'verification_meta_tag': META, 'enamad_id': '999', 'enamad_auth_code': 'OLDTEST', 'enamad_show_badge': '0'})
        self.client.post(self.url, {'enamad_badge_snippet': snippet(), 'verification_meta_tag': ''})
        self.assertEqual(self.saved(), {'verification_meta_tag': META, 'enamad_id': BADGE_ID, 'enamad_auth_code': BADGE_CODE, 'enamad_show_badge': '0'})
        self.client.post(self.url, {'verification_meta_tag': '<meta name="enamad" content="replacement-test">', 'enamad_badge_snippet': '', 'enamad_id': '', 'enamad_auth_code': ''})
        self.assertEqual(self.saved()['enamad_id'], BADGE_ID)
        self.assertEqual(self.saved()['enamad_show_badge'], '0')
        self.client.post(self.url, {'enamad_clear_badge': '1'})
        self.assertNotIn('enamad_id', self.saved())
        self.assertIn('verification_meta_tag', self.saved())

    def test_bad_snippet_is_atomic_and_not_echoed_or_saved_in_session(self):
        self.client.post(self.url, {'enamad_badge_snippet': snippet()})
        before = self.saved()
        bad = snippet() + '<script>UNTRUSTED-PAYLOAD</script>'
        self.client.post(self.url, {'enamad_badge_snippet': bad, 'verification_meta_tag': META})
        self.assertEqual(self.saved(), before)
        session_error = self.client.session['integration_form_error']
        self.assertEqual(session_error['field'], 'enamad_badge_snippet')
        self.assertNotIn('UNTRUSTED-PAYLOAD', str(session_error))
        page = self.client.get('/admin-portal/settings/?section=integrations&view=enamad')
        self.assertContains(page, 'aria-invalid="true"')
        self.assertContains(page, 'int-enamad-snippet-error')
        self.assertNotContains(page, 'UNTRUSTED-PAYLOAD')

    def test_manual_advanced_route_and_explicit_unchecked_visibility(self):
        self.client.post(self.url, {'enamad_id': BADGE_ID, 'enamad_auth_code': BADGE_CODE})
        self.client.post(self.url, {'enamad_visibility_submitted': '1'})
        self.assertEqual(self.saved()['enamad_show_badge'], '0')
        self.assertEqual(self.saved()['enamad_id'], BADGE_ID)

    def test_store_id_forgery_does_not_modify_another_store(self):
        integration_service.connect(store=self.other, provider_code='enamad', values={'enamad_id': '999', 'enamad_auth_code': 'OTHERTEST'}, actor=None)
        self.client.post(self.url, {'enamad_badge_snippet': snippet(), 'store_id': self.other.pk})
        self.assertEqual(self.saved()['enamad_id'], BADGE_ID)
        self.assertEqual(self.saved(self.other)['enamad_auth_code'], 'OTHERTEST')

    def test_audit_failure_rolls_back_structured_changes(self):
        integration_service.connect(store=self.store, provider_code='enamad', actor=self.owner,
                                    values={'verification_meta_tag': META})
        before = self.saved()
        with mock.patch('apps.stores.services.integration_service.record_audit_event', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                integration_service.connect(store=self.store, provider_code='enamad', actor=self.owner,
                                            values={'enamad_badge_snippet': snippet()})
        self.assertEqual(self.saved(), before)

    def test_permissions_and_csrf_remain_required(self):
        anonymous = Client(HTTP_HOST=self.admin_host)
        self.assertIn(anonymous.post(self.url, {'enamad_badge_snippet': snippet()}).status_code, (302, 403))
        csrf = Client(enforce_csrf_checks=True, HTTP_HOST=self.admin_host)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, {'enamad_badge_snippet': snippet()}).status_code, 403)
        self.assertFalse(StoreIntegrationConnection.objects.filter(store=self.store, provider_code='enamad').exists())

    def test_homepage_placement_survives_disabled_footer_but_respects_badge_switch_and_domain(self):
        self.client.post(self.url, {'enamad_badge_snippet': snippet()})
        StoreDomain.objects.create(store=self.store, hostname='enamad-qa.example', domain_type=StoreDomain.DomainType.CUSTOM_DOMAIN,
                                   verification_status=StoreDomain.VerificationStatus.VERIFIED, verified_at=timezone.now())
        FooterSettings.objects.filter(store=self.store).update(is_enabled=False)
        public = Client()
        page = public.get('/', HTTP_HOST='enamad-qa.example')
        self.assertEqual(page.status_code, 200)
        assert_badge(self, page.content.decode())
        self.client.post(self.url, {'enamad_show_badge': '0'})
        self.assertNotContains(public.get('/', HTTP_HOST='enamad-qa.example'), 'trustseal.enamad.ir')
        self.client.post(self.url, {'enamad_show_badge': '1'})
        StoreDomain.objects.filter(hostname='enamad-qa.example').update(verification_status=StoreDomain.VerificationStatus.UNVERIFIED, verified_at=None)
        blocked = public.get('/', HTTP_HOST='enamad-qa.example')
        self.assertEqual(blocked.status_code, 404)
        self.assertNotContains(blocked, 'trustseal.enamad.ir', status_code=404)


@override_settings(ALLOWED_HOSTS=['*'], RASTISI_PLATFORM_HOSTS=frozenset({'rastisi.localhost'}))
class PlatformSnippetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.config, _ = PlatformConfiguration.objects.get_or_create(pk=1)

    def data(self, **changes):
        values = {name: getattr(self.config, name) for name in PlatformConfigurationForm.Meta.fields if name != 'logo'}
        values.update(changes)
        return values

    def test_platform_form_extracts_without_a_model_field_or_html_persistence(self):
        form = PlatformConfigurationForm(self.data(enamad_badge_snippet=snippet()), instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()
        self.assertEqual((config.enamad_id, config.enamad_auth_code), (BADGE_ID, BADGE_CODE))
        self.assertNotIn('enamad_badge_snippet', form.cleaned_data)
        self.assertNotIn(snippet(), str(config.__dict__))

    def test_platform_both_public_layouts_preserve_the_official_dom(self):
        self.config.enamad_id, self.config.enamad_auth_code = BADGE_ID, BADGE_CODE
        self.config.enamad_badge_enabled = True
        self.config.save()
        cache.clear()
        response = self.client.get('/', HTTP_HOST='rastisi.localhost')
        self.assertEqual(response.status_code, 200)
        assert_badge(self, response.content.decode())
        from django.test import RequestFactory
        context = platform_enamad_verification(RequestFactory().get('/', HTTP_HOST='rastisi.localhost'))
        for layout in ('portal/public/base_public.html', 'portal/public_v2/base.html'):
            with self.subTest(layout=layout), override_settings(ROOT_URLCONF='shop_core.urls_platform'):
                assert_badge(self, render_to_string(layout, context))

    def test_platform_bad_snippet_has_inline_error_and_does_not_echo_html(self):
        form = PlatformConfigurationForm(self.data(enamad_badge_snippet='<script>UNTRUSTED-PAYLOAD</script>'), instance=self.config)
        self.assertFalse(form.is_valid())
        self.assertIn('enamad_badge_snippet', form.errors)
        self.assertNotIn('UNTRUSTED-PAYLOAD', str(form['enamad_badge_snippet']))

    def test_platform_raw_size_limit_includes_whitespace_padding(self):
        form = PlatformConfigurationForm(self.data(enamad_badge_snippet=' ' * 4096 + snippet()), instance=self.config)
        self.assertFalse(form.is_valid())
        self.assertIn('enamad_badge_snippet', form.errors)

    def test_platform_blank_stages_preserve_saved_values_and_explicit_clear_is_separate(self):
        self.config.enamad_id, self.config.enamad_auth_code = BADGE_ID, BADGE_CODE
        self.config.enamad_verification_meta_tag = META
        self.config.enamad_badge_enabled = True
        self.config.save()
        form = PlatformConfigurationForm(self.data(enamad_id='', enamad_auth_code='', enamad_verification_meta_tag=''), instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()
        self.assertEqual((config.enamad_id, config.enamad_verification_meta_tag, config.enamad_badge_enabled), (BADGE_ID, META, True))
        form = PlatformConfigurationForm(self.data(enamad_clear_badge='on'), instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()
        self.assertEqual((config.enamad_id, config.enamad_auth_code, config.enamad_verification_meta_tag), ('', '', META))

    def test_platform_http_import_and_audit_never_persist_raw_snippet(self):
        staff = get_user_model().objects.create_user(username='enamad-platform-test', is_staff=True, is_superuser=True)
        admin = Client(HTTP_HOST='platformadmins.rastisi.localhost')
        admin.force_login(staff)
        response = admin.post('/configuration/', self.data(enamad_badge_snippet=snippet()))
        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.enamad_auth_code, BADGE_CODE)
        audit = PlatformAuditLogEntry.objects.filter(action_code='platform_configuration.updated').latest('pk')
        self.assertNotIn('enamad_badge_snippet', audit.after_summary)
        self.assertNotIn('<a ', audit.after_summary)

    def test_platform_badge_does_not_leak_to_admin_or_merchant(self):
        from django.test import RequestFactory
        self.config.enamad_id, self.config.enamad_auth_code = BADGE_ID, BADGE_CODE
        self.config.enamad_badge_enabled = True
        self.config.save()
        cache.clear()
        for host in ('platformadmins.rastisi.localhost', 'other.example'):
            self.assertEqual(platform_enamad_verification(RequestFactory().get('/', HTTP_HOST=host)), {})

    def test_missing_platform_credentials_render_no_broken_empty_image(self):
        self.config.enamad_badge_enabled = True
        self.config.save()
        cache.clear()
        self.assertNotContains(self.client.get('/', HTTP_HOST='rastisi.localhost'), 'trustseal.enamad.ir')
