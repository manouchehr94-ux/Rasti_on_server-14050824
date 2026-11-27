# Integrated local acceptance

Use Git and Python 3.12 on Windows. With the Draft PR branch checked out, run:

    .\tools\qa\Test-RastiSi-Integrated.ps1 -Commit <full PR SHA>

Append -EnableWebPush only for synthetic notifications through the official browser push providers. Local media and SQLite live under a new LocalAppData directory. The fixture prints synthetic shop URLs and credentials. The server listens on loopback only. Stop it with Ctrl+C.

## Incident evidence

The 504 (02:35:32.563 Tehran) and app-side 200 share a Request ID; the observed Gunicorn start appears 128.469 seconds after the edge record ended. A Gunicorn status is not confirmation that Nginx delivered that response. There were no identified open request IDs on either timed-out PID in the supplied window. A queued request, a time-source/log-ingestion discrepancy, heartbeat lag, pressure, a database lock and an external dependency remain hypotheses. The 2-thread gthread worker can process a request longer than the configured worker heartbeat timeout while it continues to notify the master. Do not change Worker count, timeouts or proxy deadlines on these observations.

- Run the synthetic evidence check (includes slow SQL, 5xx, masking and hook chaining): python -m unittest discover -s deploy/release/tests -p test_stage2_availability.py
- Verify only the Django URL **name** appears, never the raw path/route parameters, headers, SQL text or exception message. For an interrupted request, compare Gunicorn start, Django view entry, first database entry, summary and finish by Request ID.
- The optional fixed Nginx proxy header `proxy_set_header X-RastiSi-Edge-Dispatched $msec;` belongs in each affected RASTISI HTTPS proxy location alongside existing proxy headers. It must overwrite client input and must not be applied globally. No change to existing Nginx is required for the Django and Gunicorn phases. Use of this header would need separate operations approval; values are an estimate of proxy-to-worker delay and negative values indicate clock or evidence problems.
- From Windows, if the owner authorizes a fresh snapshot, the following streams the reviewed read-only collector over the existing SSH connection. It reports cgroup counters, memory and CPU/IO pressure, current PostgreSQL locks and current Redis unit state. A current snapshot cannot reconstruct previous locks, Redis delays or host pressure.

      Get-Content -Raw .\tools\qa\rastisi_host_evidence.py | ssh -o StrictHostKeyChecking=yes root@87.107.9.4 "python3 -B -"

## Platform industries

1. Sign in as the fixture's platform owner. In صنف‌ها و قالب‌ها, start from a known complete template and create a new version with a different name/order plus an attribute value. Use the Persian graphical form; raw JSON is available under Advanced. It creates a disabled version; publishing requires the existing production-readiness validator.
2. Try an unknown parent and a duplicate code. Nothing from the invalid submission should persist. Activate a ready version, verify it appears for new sellers, then deactivate it and verify an existing store still has its original categories and attributes.
3. Register using صنف «سایر», enter a synthetic name, and confirm the owner can mark that proposal reviewed. Repeat the final-review correction from PR #23; confirm one store. A merchant administrator may not use the platform mutation URLs.

## Mobile orders

1. In each fixture store use the manager's notification preferences to register a device. Enable only a subset of new order, paid, cancellation request and return events. To create events use synthetic customer checkout/COD confirmation and the existing return flow. A cancellation request is a customer POST from order detail; it does not cancel the order or refund payment.
2. With the panel closed, expect an OS alert for an enabled event, and repeat with two devices. Open it and verify the corresponding order; a user with return-only access still lands on the authorized return page. Test duplicate submission, disabled event, unsubscription, logout, expired provider subscription (HTTP 404/410) and a different store. To validate by browser with the laptop fixture, use the provided loopback origins and desktop Edge/Chrome.
3. Mobile acceptance needs an approved **trusted HTTPS** test origin reachable by both phones and the worker. Android requires a compatible browser and notification permission. On iOS/iPadOS use version 16.4 or newer, add the merchant app to Home Screen, open it there and request permission from a tap. An expired or offline session can show a generic alert without order data; opening details still requires authorization. The loopback launcher does not expose the laptop or create tunnels.
4. Test a device inside Iran on each relevant carrier or ISP, without assuming any provider is reachable. Record OS/browser/version, registration, receipt (including when the panel is closed), delivery delay and link opening. The server must reach the official browser provider; the phone must reach both that provider and the merchant HTTPS origin. Provider retries occur in the existing inbox worker; even a provider acceptance does not prove reception on a device. In-app inbox is the fallback for expired sessions, unavailable providers or expired pushes.

## Rollback and scope

The feature commits can be reverted independently. The mobile change has two additive migrations; deploy-time schema review is still required. Revert app behavior and keep the new columns to preserve data on rollback. Disable the existing Web Push feature flag to stop delivery during triage. Industry template versions and audit history are retained. No Production or RASTISERVICE action is required for local acceptance.

## Android and iPhone on a private LAN

The launcher accepts -LanHttpsQa -EnableWebPush for an independent synthetic SQLite database.
Django still binds to 127.0.0.1:8771. Set up the following only when you choose to run the phone
drill. It needs a Windows laptop and phones on an isolated Wi-Fi/LAN, no public tunnel, no public DNS
records, no inbound router port forwarding, and outbound access to the official browser Push providers.

1. On a DNS resolver used only by the test devices, point rastisi-qa.test, platformadmins.rastisi-qa.test,
   qa-a.rastisi-qa.test, qa-b.rastisi-qa.test, qa-bike.rastisi-qa.test, qa-other.rastisi-qa.test,
   mybikestore.test and otherstore.test to the laptop's private IPv4 address. Use the same resolver
   on the laptop; ensure the Android Private DNS and browser secure-DNS mode do not bypass it.
2. Start the PowerShell launcher with both switches. In a separate terminal, set RASTISI_QA_LAN_IP
   to the laptop's private address, validate the supplied Caddyfile, then run Caddy with that file.
   If needed, allow TCP 8443 in Windows Firewall only on the private test network and test subnet.
   Caddy binds only to the supplied QA address and forwards to the loopback Django server.
3. Install only Caddy's QA root certificate (not its private key) on the two test phones and turn
   on full trust for it on iOS. Verify the URLs open with trusted HTTPS before requesting notifications.
   Open https://qa-bike.rastisi-qa.test:8443/admin-portal/ as 09121110003 and use the synthetic
   password shown by the launcher. On iOS/iPadOS 16.4+ add the site to Home Screen first, then enable
   Push from the installed app with a tap.
4. Register one Android and one iPhone. With the panel closed send a synthetic event. Expire the
   test session without revoking the device and send a second event: only the generic notification
   is shown. Tap it, sign in and confirm the same order. With another account or store, the same
   link must not reveal an order. Test read/disabled items and revoke one device, then send again.
5. Stop Caddy and the QA launcher. Remove the private DNS overrides, temporary firewall rule and QA
   root trust. No Production settings or Production records are involved.

Examples after opening a second PowerShell in the QA checkout:

    $env:RASTISI_QA_LAN_IP = 'YOUR_PRIVATE_LAPTOP_IPV4'
    $env:RASTISI_QA_CADDY_DATA = Join-Path $env:LOCALAPPDATA 'RastiSi-Mobile-QA-Caddy'
    caddy validate --config .\tools\qa\Caddyfile.mobile-qa --adapter caddyfile
    caddy run --config .\tools\qa\Caddyfile.mobile-qa --adapter caddyfile
