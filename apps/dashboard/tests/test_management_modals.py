"""Server contract for opt-in management dialogs; behavior is tested in Chromium."""
from bs4 import BeautifulSoup
from django.urls import reverse

from apps.dashboard.tests.test_integrations_redesign import _Base
from apps.dashboard.tests.integration_pages import VIEWS, view_html
from apps.stores.integrations.registry import all_providers


class ManagementModalContractTests(_Base):
    def test_every_provider_keeps_its_scoped_real_post_and_accessible_modal(self):
        found = set()
        for view in VIEWS:
            soup = BeautifulSoup(view_html(self.client, view), 'html.parser')
            for dialog in soup.select('dialog[data-management-modal]'):
                code = dialog['id'].removeprefix('drawer-')
                found.add(code)
                self.assertIn('au-modal--management', dialog['class'])
                self.assertIsNotNone(soup.find(id=dialog['aria-labelledby']))
                self.assertIsNotNone(soup.find(id=dialog['aria-describedby']))
                form = dialog.select_one('form[data-management-form]')
                self.assertEqual(form['method'], 'post')
                self.assertEqual(form['action'], reverse('dashboard:settings-integration-connect', args=[code]))
                self.assertIsNotNone(form.select_one('[name="csrfmiddlewaretoken"]'))
                save = dialog.select_one('[data-testid="int-save"]')
                self.assertEqual(save['form'], form['id'])
                self.assertEqual(dialog.select_one('[data-drawer-close]')['type'], 'button')
        self.assertEqual(found, {p.code for p in all_providers()})

    def test_enamad_advanced_entry_remains_native_accordion_and_redacted(self):
        soup = BeautifulSoup(view_html(self.client, 'enamad'), 'html.parser')
        advanced = soup.select_one('details[data-testid="enamad-advanced"]')
        self.assertIsNotNone(advanced.find('summary'))
        self.assertIsNotNone(advanced.select_one('[name="enamad_id"]'))
        self.assertEqual(advanced.select_one('[name="enamad_auth_code"]')['type'], 'text')
        self.assertEqual(soup.select_one('[name="enamad_badge_snippet"]').get_text(), '')
        self.assertIsNotNone(soup.select_one('[name="enamad_clear_badge"]'))
        self.assertIsNotNone(soup.select_one('[name="enamad_clear_meta"]'))
        self.assertIsNotNone(soup.select_one('[name="enamad_show_badge"]'))
