# Automated RastiSi acceptance

Run from Windows with the existing integrated QA installation and an exact Git commit.

    .\Invoke-RastiSi-Acceptance.ps1 -Commit <40-character SHA>

The launcher reuses the QA Python and installed Edge or Chrome. It installs only the
pinned Playwright driver into the individual run directory when needed. It clones the
target separately and starts Django's own live test server on a temporary loopback port.
Its SQLite database, media and sessions are synthetic and disposable. Port 8771 and
the original checkout, database and virtual environment are independent of this run.

Use -QaHome for a different existing virtual environment, -Chromium for a specific
browser, and -Ref main (or another Git branch) for future revisions. The checkout must
match the requested commit. Use the project Firefox/Safari or real device acceptance
where browser-specific behavior matters.

The launcher prints the report path under LocalAppData. Open index.html for PASS/FAIL,
screenshots, synthetic operation evidence and full group logs. summary.json records
the exact Git SHA; junit.xml is available for future automation. Exit code 1 indicates
a failed required group, including a skipped browser suite. Browser contexts use
isolated cookies. External URLs are blocked by the integrated browser fixtures.

## Automated scope

- Platform-owner edits, category tree and attribute values, version creation,
  activation/deactivation, audit entries, and preservation of installed store data.
- Real browser registration with local ALTCHA and OTP, trial store, custom industry,
  plus the existing Playwright onboarding and final-review correction tests.
- Browser checkout with synthetic payment in cash on delivery, confirmation, customer
  cancellation request, return submission, database outcomes and notification inbox.
- Cross-store order/import access, platform-only forms, encrypted Push payload tests,
  and Service Worker simulation for expired, denied and authorized sessions.
- Browser XLSX 3.3 download, five RTL sheets, preview, commit and idempotent reimport.
- Existing diagnostics checks for safe phases, request correlation, masking and marker
  enforcement. Native Windows does not execute Linux-specific Gunicorn checks. They
  remain unchanged in the Linux Release rehearsal and appear as NOT_RUN in Windows.

## Short human-only acceptance

1. Receive one real alert per device on Android and iPhone with the panel closed,
   using the private HTTPS procedure in INTEGRATED-ACCEPTANCE.md. Use a relevant
   Iranian network if that is the release requirement. Open one alert after session
   expiry and confirm login is required. Simulated Push does not count as delivery.
2. Review one representative screenshot per changed workflow only if visual
   acceptance is required. Check Excel-specific visual/dropdown behavior in desktop
   Excel only when the workbook design or validation rules change.
