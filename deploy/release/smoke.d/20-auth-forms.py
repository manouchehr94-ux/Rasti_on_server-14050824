#!/usr/bin/env python3
"""Release smoke plugin: the public authentication and contact forms are well formed (read-only, GET only).

Adapts to the version being verified:
  * a page that carries the math CAPTCHA must have a numeric question, an answer input and an opaque token, must NOT contain the sum,
    and must not be cacheable (a cached page would hand several visitors the same question);
  * the CAPTCHA is REQUIRED when the release being verified contains it (apps/portal/services/captcha_service.py exists in RELEASE_DIR,
    or SMOKE_REQUIRE_CAPTCHA is set): serving the Turnstile widget then means the wrong code or settings are live;
  * otherwise a page that still carries the Cloudflare Turnstile widget is accepted (older releases, including the one a rollback returns to).
Prints one JSON line.
"""

import json
import os
import re
import sys
import urllib.request

BASE = os.environ.get("BASE_URL", "").rstrip("/")
HOST = os.environ.get("SMOKE_HOST", "")
FORMS = (("/login/", ("login_password", "login_otp")), ("/register/", ("register",)), ("/reset-password/", ("password_reset",)), ("/contact/", ("contact",)))
PERSIAN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
QUESTION = re.compile(r"<bdi[^>]*>\s*([0-9۰-۹٠-٩]+) \+ ([0-9۰-۹٠-٩]+) = ؟\s*</bdi>")


# --- BEGIN ALTCHA RELEASE SMOKE SUPPORT (2026-11-14) ---
from html.parser import HTMLParser
from urllib.parse import urlsplit


class _AltchaPageParser(HTMLParser):
    """Inspect markup, not arbitrary text matches in scripts or comments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.widgets = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "altcha-widget":
            self.widgets.append(values)
        elif tag.lower() == "script":
            self.scripts.append(values)


def _release_expects_altcha(release_dir):
    """Determine the required CAPTCHA type from the release under test."""
    if not release_dir:
        return False
    for design in ("public", "public_v2"):
        template = os.path.join(release_dir, "apps", "portal", "templates", "portal", design, "_captcha.html")
        try:
            with open(template, "r", encoding="utf-8") as stream:
                if "<altcha-widget" in stream.read().lower():
                    return True
        except OSError:
            continue
    return False


def _safe_altcha_challenge(url):
    """A challenge must resolve to this application's origin, never a foreign host."""
    if not url or any(ch.isspace() for ch in url):
        return False
    try:
        target = urlsplit(url)
        if not target.path.startswith("/") or target.path.startswith("//"):
            return False
        if target.fragment or target.username or target.password:
            return False
        if target.scheme and target.scheme not in ("http", "https"):
            return False
        if target.netloc:
            allowed = {urlsplit(BASE).hostname, (HOST.split(":", 1)[0] if HOST else None)}
            return target.hostname in allowed
        return not target.scheme
    except ValueError:
        return False


def _check_altcha_page(path, actions, markup, headers, release_dir):
    """Fail closed on absent, incomplete, off-origin or disabled ALTCHA."""
    successes, errors = [], []
    parsed = _AltchaPageParser()
    parsed.feed(markup)
    if "captcha-unavailable" in markup:
        errors.append("%s: ALTCHA service is unavailable" % path)
    if "cf-turnstile" in markup or "captcha-answer-" in markup:
        errors.append("%s: mixed or legacy CAPTCHA markup in ALTCHA release" % path)
    js = [s.get("src") or "" for s in parsed.scripts]
    if not any("altcha-forms.js" in src for src in js):
        errors.append("%s: ALTCHA JavaScript not referenced" % path)
    if release_dir:
        source_js = os.path.join(release_dir, "apps", "portal", "static", "portal", "js", "altcha-forms.js")
        if not os.path.isfile(source_js):
            errors.append("%s: ALTCHA JavaScript source missing from release" % path)
    if len(parsed.widgets) != len(actions):
        errors.append("%s: expected %d ALTCHA widget(s), found %d" % (path, len(actions), len(parsed.widgets)))
    for action in actions:
        widgets = [w for w in parsed.widgets if w.get("id") == "captcha-" + action]
        if len(widgets) != 1:
            errors.append("%s: missing/duplicate ALTCHA widget for %s" % (path, action))
            continue
        widget = widgets[0]
        if widget.get("name") != "altcha":
            errors.append("%s: ALTCHA response input missing for %s" % (path, action))
            continue
        if widget.get("type") != "checkbox" or widget.get("auto") != "off" or "disabled" in widget:
            errors.append("%s: unexpected or disabled ALTCHA configuration for %s" % (path, action))
            continue
        if not _safe_altcha_challenge(widget.get("challenge", "")):
            errors.append("%s: missing/invalid ALTCHA challenge for %s" % (path, action))
            continue
        successes.append("%s ALTCHA well formed (%s)" % (path, action))
    cache = str(headers.get("Cache-Control", "")).lower()
    if "public" in cache and "private" not in cache:
        errors.append("%s: authentication form publicly cacheable" % path)
    return successes, errors
# --- END ALTCHA RELEASE SMOKE SUPPORT ---


def get(path):
    req = urllib.request.Request(BASE + path)
    if HOST:
        req.add_header("Host", HOST)
    req.add_header("X-Forwarded-Proto", "https")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.read().decode("utf-8", "replace"), resp.headers
    except Exception:
        return "", {}


def main():
    passed, failures = [], []
    release_dir = os.environ.get("RELEASE_DIR", "")
    require = bool(os.environ.get("SMOKE_REQUIRE_CAPTCHA")) or bool(release_dir and os.path.exists(os.path.join(release_dir, "apps", "portal", "services", "captcha_service.py")))
    expect_altcha = _release_expects_altcha(release_dir)
    for path, actions in FORMS:
        html, headers = get(path)
        if not html:
            failures.append("%s did not load" % path)
            continue
        if "csrfmiddlewaretoken" not in html:
            failures.append("%s has no CSRF token" % path)
            continue
        if expect_altcha or "altcha-widget" in html.lower() or "rasti-altcha" in html or "captcha-unavailable" in html:
            good, bad = _check_altcha_page(path, actions, html, headers, release_dir if expect_altcha else "")
            passed.extend(good)
            failures.extend(bad)
            continue
        if "captcha-answer-" in html:
            cache = str(headers.get("Cache-Control", ""))
            if "no-store" not in cache and "private" not in cache:
                failures.append("%s carries a per-visitor CAPTCHA but is cacheable (Cache-Control=%r)" % (path, cache))
            for action in actions:
                marker = 'id="captcha-label-%s"' % action
                if marker not in html:
                    failures.append("%s: no CAPTCHA question for %s" % (path, action))
                    continue
                block = html[html.index(marker): html.index(marker) + 2500]
                q = QUESTION.search(block)
                token = re.search(r'name="captcha_token" value="([^"]{16,})"', block)
                if not (q and token and 'name="captcha_answer"' in block):
                    failures.append("%s: malformed CAPTCHA for %s" % (path, action))
                    continue
                total = int(q.group(1).translate(PERSIAN)) + int(q.group(2).translate(PERSIAN))
                if re.search(r'(value|data-[a-z-]+)="%d"|data-answer|data-sum' % total, block):
                    failures.append("%s: the CAPTCHA answer is exposed" % path)
                    continue
                passed.append("%s CAPTCHA well formed (%s)" % (path, action))
        elif require:
            failures.append("%s has no CAPTCHA but this release requires one%s" % (path, " (still serving the Turnstile widget)" if "cf-turnstile" in html else ""))
        elif "cf-turnstile" in html:
            passed.append("%s carries the legacy Turnstile widget" % path)
        else:
            passed.append("%s loads" % path)
    print(json.dumps({"ok": not failures, "passed": passed, "failures": failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
