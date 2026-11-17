"""Mandatory real-browser UI regression suite; isolated synthetic SQLite data only.

Install tools/qa/requirements-management-browser.txt and Chromium, then run:
python tools/qa/management_modal_browser.py --output /tmp/management-modal-evidence
No Production URLs, credentials, databases or uploaded media are used.
"""
from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "89c5f58e75f9b68f0bc277836d80e47aa0deaabe"
HOST = "modal-qa.rastisi.localhost"
BADGE_CODE = "TestModalBadgeAa123"
PROVIDERS = {"enamad": "enamad", "torob": "torob", "google_analytics": "analytics", "google_tag_manager": "analytics", "ad_pixel": "analytics"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(output):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    results = []
    def passed(name):
        results.append(name)
        print("PASS", name, flush=True)

    with tempfile.TemporaryDirectory(prefix="rastisi-management-browser-") as temp_name:
        temp = Path(temp_name)
        settings_path = temp / "modal_qa_settings.py"
        settings_path.write_text(
            "from shop_core.settings import *\n"
            f"DATABASES={{'default':{{'ENGINE':'django.db.backends.sqlite3','NAME':{str(temp / 'qa.sqlite3')!r}}}}}\n"
            f"MEDIA_ROOT={str(temp / 'media')!r}\n"
            f"ALLOWED_HOSTS=['127.0.0.1', {HOST!r}]\n"
            "EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'\n", encoding="utf-8")
        # Whitelisted test environment: never inherit deployment DB/Redis/secret settings.
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "LANG", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH"}}
        env.update(DJANGO_DEBUG="1", DJANGO_SETTINGS_MODULE="modal_qa_settings", PYTHONPATH=os.pathsep.join((str(temp), str(ROOT))))
        os.environ.clear()
        os.environ.update(env)
        sys.path[:0] = [str(temp), str(ROOT)]
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.utils import timezone
        from apps.core.models import ShopSettings
        from apps.stores.models import Store, StoreDomain, StoreMembership, StoreIntegrationConnection
        call_command("migrate", verbosity=0, interactive=False)
        store = Store.objects.get(slug="akhlaghi")
        store.admin_subdomain = "modal-qa"
        store.save(update_fields=["admin_subdomain"])
        shop = ShopSettings.provision_for(store)
        shop.name = "Rasti Mode Demo"
        shop.save()
        StoreDomain.objects.create(store=store, hostname="modal-shop.localhost", domain_type=StoreDomain.DomainType.CUSTOM_DOMAIN,
                                   verification_status=StoreDomain.VerificationStatus.VERIFIED, verified_at=timezone.now())
        owner = get_user_model().objects.create_user(username="modal-browser-owner", is_staff=True)
        StoreMembership.objects.create(store=store, user=owner, role=StoreMembership.Role.OWNER,
                                       status=StoreMembership.MembershipStatus.ACTIVE, accepted_at=timezone.now())
        from apps.stores.services.integration_service import connect
        connect(store=store, provider_code="enamad", actor=owner, values={"enamad_id":"1122863", "enamad_auth_code":BADGE_CODE, "enamad_show_badge":"1", "verification_meta_tag":'<meta name="enamad" content="synthetic-browser-meta">'})
        def stored(code, active=False):
            # Playwright sync runs an event loop; inspect Django in a normal
            # worker thread rather than disabling Django's async-safety guard.
            def read():
                from django.db import connections
                try:
                    row = StoreIntegrationConnection.objects.filter(store_id=store.pk, provider_code=code).first()
                    return (row.is_active if active else row.get_credentials()) if row else None
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as worker:
                return worker.submit(read).result()
        client = Client()
        client.force_login(owner)
        session = client.cookies["sessionid"].value
        # Reconstruct BEFORE presentation from the exact validated baseline; backend is unchanged.
        old = temp / "baseline"
        paths = {"templates/dashboard/base_admin.html": "apps/dashboard/templates/dashboard/base_admin.html"}
        for name in ("settings_integrations.html", "settings_integration_card.html", "settings_integration_drawer.html"):
            paths["templates/dashboard/partials/" + name] = "apps/dashboard/templates/dashboard/partials/" + name
        for name in ("css/admin_ui.css", "js/admin_ui.js"):
            paths["static/" + name] = "apps/dashboard/static/" + name
        for dest, source in paths.items():
            path = old / dest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output(["git", "show", BASELINE + ":" + source], cwd=ROOT))
        (temp / "modal_before_settings.py").write_text(
            "from modal_qa_settings import *\n"
            f"TEMPLATES[0]['DIRS']=[{str(old / 'templates')!r}]+list(TEMPLATES[0]['DIRS'])\n"
            f"STATICFILES_DIRS=[{str(old / 'static')!r}]+list(STATICFILES_DIRS)\n", encoding="utf-8")
        processes = []
        try:
            ports = [free_port(), free_port()]
            for port, module in zip(ports, ("modal_qa_settings", "modal_before_settings")):
                child_env = {**env, "DJANGO_SETTINGS_MODULE": module}
                log = (temp / (module + ".log")).open("w")
                processes.append((subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                                                   cwd=ROOT, env=child_env, stdout=log, stderr=log), log))
                for attempt in range(100):
                    try:
                        urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                        break
                    except Exception:
                        if processes[-1][0].poll() is not None: raise RuntimeError("local QA server exited")
                        time.sleep(.1)
                else: raise RuntimeError("local QA server did not become ready")
            base = f"http://{HOST}:{ports[0]}/admin-portal/settings/?section=integrations"
            before_base = f"http://{HOST}:{ports[1]}/admin-portal/settings/?section=integrations"
            with sync_playwright() as pw:
                executable = None if Path(pw.chromium.executable_path).exists() else shutil.which("chromium") or shutil.which("google-chrome")
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                contexts = []
                errors = []
                def context(width=1440, theme="light"):
                    c = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                    contexts.append(c)
                    c.add_cookies([{"name": "sessionid", "value": session, "domain": HOST, "path": "/"}])
                    c.add_init_script("if (location.hostname === " + json.dumps(HOST) + ") localStorage.setItem('adminTheme', " + json.dumps("dark" if theme == "dark" else "") + ");")
                    def offline(route):
                        from urllib.parse import urlsplit
                        if urlsplit(route.request.url).hostname in (HOST, "127.0.0.1"):
                            route.continue_()
                        else:
                            route.fulfill(status=503, content_type="text/plain", body="synthetic external request blocked")
                    c.on("page", lambda p: p.on("pageerror", lambda error: errors.append(str(error))))
                    c.route("**/*", offline)
                    return c
                page = context().new_page()
                def opened(code):
                    d = page.locator("#drawer-" + code)
                    d.wait_for(state="visible")
                    assert d.get_attribute("data-management-modal") is not None
                    assert d.evaluate("d=>d.matches(':modal')")
                    assert page.evaluate("getComputedStyle(document.documentElement).overflowY") == "hidden"
                    assert page.evaluate("getComputedStyle(document.body).overflowY") == "hidden"
                    return d
                def clean_close(d):
                    page.keyboard.press("Escape")
                    d.wait_for(state="hidden")
                    page.wait_for_function("!document.documentElement.classList.contains('au-management-open')")
                    assert page.evaluate("getComputedStyle(document.body).overflowY") != "hidden"
                # Every provider, overview link, attention item and individual tab.
                page.goto(base + '&manage=enamad', wait_until='networkidle')
                opened('enamad'); clean_close(page.locator('#drawer-enamad'))
                passed('provider-only deep link follows its authorized service tab')
                page.goto(base, wait_until="networkidle")
                attention = page.locator('[data-testid="overview-attention"] a[href*="manage="]').evaluate_all("xs=>xs.map(x=>x.getAttribute('href'))")
                assert attention
                for href in attention:
                    page.goto(base, wait_until="networkidle")
                    page.locator('[data-testid="overview-attention"] a').evaluate_all("(xs,href)=>xs.find(x=>x.getAttribute('href')===href).click()", href)
                    page.wait_for_load_state("networkidle")
                    d = page.locator("dialog[data-management-modal][open]")
                    d.wait_for(state="visible")
                    clean_close(d)
                for code, view in PROVIDERS.items():
                    page.goto(base, wait_until="networkidle")
                    page.locator(f'[data-testid="integration-{code}"] [data-testid="int-manage"]').click()
                    d = opened(code); clean_close(d)
                    page.goto(base + "&view=" + view, wait_until="networkidle")
                    trigger = page.locator(f'[data-drawer-open="drawer-{code}"]')
                    trigger.click(); d = opened(code)
                    assert page.evaluate("document.activeElement.closest('dialog').id") == "drawer-" + code
                    for _ in range(25):
                        page.keyboard.press("Tab")
                        assert d.evaluate("d=>d.contains(document.activeElement)")
                    for _ in range(25):
                        page.keyboard.press("Shift+Tab")
                        assert d.evaluate("d=>d.contains(document.activeElement)")
                    clean_close(d)
                    assert trigger.evaluate("e=>e===document.activeElement")
                passed("all five providers / overview / attention / tabs / focus trap and restore")
                # Four close entry points must all guard dirty forms and discard only on confirmation.
                page.goto(base + "&view=analytics", wait_until="networkidle")
                trigger = page.locator('[data-drawer-open="drawer-google_analytics"]')
                for method in ("escape", "x", "footer", "backdrop"):
                    trigger.click(); d = opened("google_analytics")
                    field = d.locator('[name="measurement_id"]'); initial = field.input_value()
                    field.fill("G-DIRTY123")
                    def close_action():
                        if method == "escape": page.keyboard.press("Escape")
                        elif method == "x": d.locator('[data-drawer-close]').first.click()
                        elif method == "footer": d.locator('[data-drawer-close]').last.click()
                        else: page.mouse.click(2, 2)
                    page.once("dialog", lambda alert: alert.dismiss()); close_action()
                    assert d.is_visible() and field.input_value() == "G-DIRTY123"
                    page.once("dialog", lambda alert: alert.accept()); close_action()
                    d.wait_for(state="hidden")
                    trigger.click(); assert field.input_value() == initial
                    clean_close(d)
                passed("Escape/X/footer/backdrop dirty cancellation and confirmed discard/reset")
                # Long-form geometry, themes, RTL/LTR, scrolling and touch targets.
                metrics = []
                for width in (1440, 1366, 768, 390):
                    for theme in ("light", "dark"):
                        c = context(width, theme); p = c.new_page()
                        p.goto(base + "&view=enamad&manage=enamad", wait_until="networkidle")
                        d = p.locator('#drawer-enamad'); d.wait_for(state="visible")
                        m = d.evaluate("""d=>{let r=d.getBoundingClientRect(),b=d.querySelector('.au-modal__body');return {x:r.x,y:r.y,w:r.width,h:r.height,vw:innerWidth,vh:innerHeight,overflow:d.scrollWidth-d.clientWidth,scroll:b.scrollHeight>b.clientHeight,dir:getComputedStyle(d).direction};}""")
                        assert abs(m['x'] + m['w']/2 - width/2) < 3 and abs(m['y'] + m['h']/2 - 450) < 3, m
                        assert m['y'] >= 10 and m['h'] <= 880 and m['overflow'] <= 1 and m['scroll'] and m['dir'] == 'rtl', m
                        if width > 768: assert .8 <= m['w']/width <= .91, m
                        else: assert m['w'] <= width-20, m
                        for button in (d.locator('[data-drawer-close]').first, d.locator('[data-testid="int-save"]')):
                            r = button.bounding_box(); assert r['width'] >= 44 and r['height'] >= 44, r
                        contrast = d.locator('[data-drawer-close]').first.evaluate(r"""e=>{let rgb=s=>s.match(/[\d.]+/g).slice(0,3).map(Number).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}),lum=s=>{let c=rgb(s);return .2126*c[0]+.7152*c[1]+.0722*c[2]},a=lum(getComputedStyle(e).color),b=lum(getComputedStyle(e.closest('dialog')).backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)}""")
                        assert contrast >= 4.5, contrast
                        assert d.locator('[name="enamad_badge_snippet"]').evaluate("e=>getComputedStyle(e).direction") == 'ltr'
                        body = d.locator('.au-modal__body')
                        body.evaluate("e=>e.scrollTop=0")
                        bounds = body.bounding_box(); background_scroll = p.evaluate('scrollY')
                        p.mouse.move(bounds['x'] + bounds['width']/2, bounds['y'] + bounds['height']/2)
                        p.mouse.wheel(0, 400); p.wait_for_timeout(100)
                        assert body.evaluate('e=>e.scrollTop') > 0
                        assert p.evaluate('scrollY') == background_scroll
                        body.evaluate("e=>e.scrollTop=0")
                        p.screenshot(path=str(output / f"after-{width}-{theme}.png"))
                        d.locator('[name="enamad_badge_snippet"]').scroll_into_view_if_needed()
                        if width in (1440, 390) and theme == 'light': p.screenshot(path=str(output / f"after-{width}-form.png"))
                        footer = d.locator('.au-modal__foot').bounding_box(); assert footer['y'] + footer['height'] <= 900
                        metrics.append({"width": width, "theme": theme, **m})
                passed("eight responsive/theme cases / centered bounds / internal scroll / RTL-LTR / 44px targets")
                # Real valid/invalid POST, CSRF middleware, preserved values and unrelated provider isolation.
                page.goto(base + "&view=analytics&manage=google_analytics", wait_until="networkidle")
                d = opened("google_analytics"); d.locator('[name="measurement_id"]').fill("G-ABC1234DEF")
                with page.expect_navigation(wait_until="networkidle"): d.locator('[data-testid="int-save"]').click()
                assert stored('google_analytics')['measurement_id'] == 'G-ABC1234DEF'
                assert stored('google_tag_manager') is None
                page.locator('[data-drawer-open="drawer-google_analytics"]').click(); d = opened('google_analytics')
                d.locator('[name="measurement_id"]').fill('invalid-qa-value')
                with page.expect_navigation(wait_until="networkidle"): d.locator('[data-testid="int-save"]').click()
                d = opened('google_analytics'); field = d.locator('[name="measurement_id"]')
                assert field.input_value() == 'invalid-qa-value' and field.get_attribute('aria-invalid') == 'true'
                assert field.evaluate("e=>e===document.activeElement")
                assert stored('google_analytics')['measurement_id'] == 'G-ABC1234DEF'
                clean_close(d) # rejected server value is the baseline, not a new unsaved edit
                page.evaluate('RastiManagementModal.init(); RastiManagementModal.init()')
                assert not d.is_visible()
                passed("real valid/invalid POST / auto-reopen / visible error focus / saved-value and other-provider isolation")
                # Keep native details, eNamad raw-input redaction and existing structured persistence.
                page.goto(base + '&view=enamad&manage=enamad', wait_until='networkidle'); d=opened('enamad')
                details=d.locator('[data-testid="enamad-advanced"]'); details.locator('summary').click(); assert details.get_attribute('open') is not None
                details.locator('summary').click(); assert details.get_attribute('open') is None
                badge_code=BADGE_CODE
                raw=(f"<a referrerpolicy='origin' target='_blank' href='https://trustseal.enamad.ir/?id=1122863&Code={badge_code}'><img referrerpolicy='origin' src='https://trustseal.enamad.ir/logo.aspx?id=1122863&Code={badge_code}' alt='' style='cursor:pointer' code='{badge_code}'></a>")
                d.locator('[name="enamad_badge_snippet"]').fill(raw)
                with page.expect_navigation(wait_until='networkidle'): d.locator('[data-testid="int-save"]').click()
                before_values=stored('enamad')
                assert before_values['enamad_auth_code']==badge_code and 'enamad_badge_snippet' not in before_values
                page.locator('[data-drawer-open="drawer-enamad"]').click();d=opened('enamad');d.locator('[name="enamad_badge_snippet"]').fill(raw+'<script>window.BAD_MODAL=1</script>')
                with page.expect_navigation(wait_until='networkidle'):d.locator('[data-testid="int-save"]').click()
                d=opened('enamad');assert d.locator('[name="enamad_badge_snippet"]').input_value()=='' and page.evaluate('window.BAD_MODAL') is None
                assert stored('enamad')==before_values
                d.locator('[data-testid="int-disable"]').click()
                confirmation = page.locator('#au-confirm-dialog')
                confirmation.wait_for(state='visible')
                assert d.is_visible() and stored('enamad', active=True)
                confirmation.locator('[data-au-close]').last.click()
                confirmation.wait_for(state='hidden')
                assert d.is_visible() and stored('enamad', active=True)
                clean_close(d)
                passed("native advanced accordion / eNamad XSS redaction and saved values / legacy confirmation cancellation")
                # Generic opt-in empty file input does not create false dirty state; rapid reopening stays clean.
                page.evaluate("""()=>{let box=document.createElement('div');box.innerHTML='<button id="generic-open" style="position:fixed;left:20px;top:20px;z-index:1000" data-management-open="generic-modal">open</button><dialog id="generic-modal" data-management-modal class="au-modal au-modal--management" aria-labelledby="generic-title"><header class="au-modal__head"><h2 id="generic-title">QA</h2></header><div class="au-modal__body"><form data-management-form><input type="file" name="upload"><input name="title" value="initial"><label><input name="check" type="checkbox">QA</label></form></div><footer class="au-modal__foot"><button data-management-close>close</button></footer></dialog>';document.body.append(box);}""")
                page.locator('#generic-open').click();g=page.locator('#generic-modal');clean_close(g)
                page.evaluate("""()=>{let d=document.querySelector('#generic-modal'),b=document.querySelector('#generic-open');for(let i=0;i<12;i++){RastiManagementModal.open(d,b);RastiManagementModal.close(d);}RastiManagementModal.open(d,b);}""")
                page.wait_for_timeout(100)
                assert g.is_visible() and page.locator('#generic-open').get_attribute('aria-expanded')=='true'
                g.locator('[name="title"]').fill('modified')
                page.once('dialog',lambda alert:alert.accept());g.locator('[data-management-close]').click();g.wait_for(state='hidden')
                page.locator('#generic-open').click();assert g.locator('[name="title"]').input_value()=='initial';clean_close(g)
                page.evaluate("()=>{let b=document.querySelector('#generic-open');b.removeAttribute('data-management-open');b.setAttribute('data-au-open','#generic-modal');let c=document.querySelector('#generic-modal [data-management-close]');c.removeAttribute('data-management-close');c.setAttribute('data-au-close','');}")
                page.locator('#generic-open').click();g.locator('[name="title"]').fill('legacy-control-edit')
                page.once('dialog',lambda alert:alert.dismiss());g.locator('[data-au-close]').click();assert g.is_visible()
                page.once('dialog',lambda alert:alert.accept());g.locator('[data-au-close]').click();g.wait_for(state='hidden')
                passed("generic opt-in / empty files / rapid reopen / idempotent initialization / clean restored state")
                for width in (1440,390):
                    c=context(width);p=c.new_page();p.goto(before_base+'&view=enamad&manage=enamad',wait_until='networkidle');p.locator('#drawer-enamad').wait_for(state='visible');p.screenshot(path=str(output/f'before-{width}-light.png'))
                assert not errors, errors
                report={"status":"PASS","checks":results,"responsive":metrics,"baseline":BASELINE,
                        "checkout_commit":subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
                        "tracked_changes":subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=ROOT).decode().splitlines(),
                        "source_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                            'apps/dashboard/static/css/admin_ui.css','apps/dashboard/static/js/admin_ui.js',
                            'apps/dashboard/static/js/management_modal.js','apps/dashboard/templates/dashboard/base_admin.html',
                            'apps/dashboard/templates/dashboard/partials/settings_integrations.html',
                            'apps/dashboard/templates/dashboard/partials/settings_integration_drawer.html',
                            'apps/dashboard/templates/dashboard/partials/settings_integration_card.html')},
                        "browser":browser.version}
                (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
                browser.close()
        finally:
            for process, log in processes:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                log.close()
    print('MANAGEMENT_MODAL_BROWSER_PASS checks='+str(len(results)),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
