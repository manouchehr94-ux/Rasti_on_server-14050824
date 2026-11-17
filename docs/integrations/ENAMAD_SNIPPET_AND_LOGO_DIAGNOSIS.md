# eNamad snippet import and Production logo diagnosis

Issues: #3 and #4. Baseline: main db0d9abc11c408b5377893b6f1943ff71394a58e.
Production inspection was read-only public HTTPS GETs on 2026-11-16. No server,
database, credentials or media were changed. Real issued identifiers are omitted
from this report, issues, source, test fixtures and CI output.

## Confirmed live evidence

The owner privately supplied the final issued snippet and confirmed its registered
hostname is `rastisi.ir`. Comparing the live HTML privately establishes that the
anchor and image identifiers BOTH exactly match the supplied snippet. The anchor
uses HTTPS trustseal.enamad.ir `/`; the image uses HTTPS trustseal.enamad.ir
`/logo.aspx`; both carry the same `id` and `Code` values. Manual identifier entry
is therefore not the observed discrepancy.

Both live elements declare `referrerpolicy="origin"`. The live image is missing
the official `code` and `style="cursor:pointer"` attributes. The live anchor has
`rel="noopener noreferrer"`; noreferrer suppresses the profile-navigation
referrer even with an origin policy. The image request is independent of that
anchor navigation. Crucially, an arbitrary image DOM `code` attribute is NOT sent
as an HTTP request header: its absence alone does not prove the image failure's
network cause.

The homepage's canonical URL uses rastisi.ir. The www homepage also returns HTTP
200 without redirecting to the non-www host. eNamad authorization of that alias
has not been established. No CSP response header or CSP meta tag was found on
the inspected homepage. Its global Referrer-Policy is same-origin, while the
image's explicit origin policy governs the cross-origin image request.

## Network evidence and limits

Controlled requests used the live image URL with `Referer: https://rastisi.ir/`,
and repeated without a Referer. The live profile URL and the official
https://enamad.ir/logohelp guide were also requested. All eNamad requests returned:

- HTTP 503, Content-Type `text/plain`, 91 bytes, no redirects;
- Server header `envoy`;
- body: `upstream connect error or disconnect/reset before headers. reset reason: connection timeout`;
- response-body SHA256: `2f85832e31ca57711e6107af6b142363351ae516adb71c69afc643fac26be7f6`.

This is a transport/proxy timeout before usable origin headers, not evidence of
an eNamad account rejection, invalid Code, suspended business or denied domain.
The client was configured with the origin Referer; a failed upstream connection
cannot prove what the origin received. A headless Chromium probe additionally
encountered a certificate-authority trust error in the inspection environment
before image inspection. This is not evidence that Production's TLS is broken.
The shared desktop/browser tool was unavailable; the project browser fallback
was attempted. Owner-browser extension blocks and the owner's actual image
response remain unverified.

The owner explicitly states that current official badge/business status has NOT
been independently confirmed. Do not label the account valid or invalid, and do
not claim that this markup correction alone makes the external logo load.

### Required external follow-up

The operator must inspect the `logo.aspx` request on their own browser/network
(or run the same read-only request from their server), reporting status,
Content-Type, blocked reason, redirects and Referer without publishing ID/Code.
Check the eNamad panel's exact badge/business status, approved activity and
registered hostname `rastisi.ir`; confirm whether `www.rastisi.ir` is authorized.
If the origin denies an otherwise approved, matching-domain request, send that
response and the issued snippet privately to eNamad support. If the panel reports
pending, suspended or domain/activity mismatch, resolve that specific panel
condition with eNamad. Changes can take time to propagate. No uploaded substitute,
image proxy, browser referrer bypass, fake approval or hidden-error workaround is
implemented here. External cause remains open until that evidence is supplied.

## Implemented customer flow and safeguards

The merchant integration drawer and platform configuration form now lead with
one multiline field, «کد کامل نماد اینماد». The advanced manual ID/Code route
remains available. The parser accepts the official single a/img shape, quoting,
entity escaping, whitespace and safe presentation variations. It checks exact
HTTPS origin and paths, exactly one id/Code pair in each URL, matching pairs and
any image code attribute, using the existing identifier validator. It rejects
scripts, event handlers, extra elements, ambiguous parameters, unsafe styles,
lookalike hosts, userinfo, incorrect paths and oversized/malformed input.
It never fetches a submitted URL.

Pasted HTML is transient: only the existing structured values are saved. Store
credentials remain encrypted and tenant-scoped; pasted HTML is not persisted in
credentials, model fields, audit events or failed-form sessions, and is not echoed
into templates. Errors are inline Persian messages. Invalid input leaves saved
values intact. Transactional updates serialize store stages and roll back failed
writes. No migration is required.

Blank snippet/blank stage inputs preserve saved badge and meta values. Explicit
clear controls remove only the selected stage. A real-form checkbox marker
separates an explicitly unchecked visibility choice from an omitted field; saved
visibility is otherwise preserved. One existing checkbox regression was updated
to submit this real marker, retaining every previous assertion.

A shared static renderer generates the trusted HTTPS URLs, both origin referrer
policies, escaped image code, cursor style, official empty alt, and an accessible
anchor label. It retains noopener without the conflicting noreferrer. No pasted
HTML is marked safe or executed. URL identifiers are unchanged and no unsolicited
parameters are added.

The official merchant badge is rendered once by the canonical storefront shell,
independently of optional decorative footer badges or a disabled footer. Its own
explicit visibility switch, active connection and verified custom domain of the
same store still control display. Platform badges remain confined to platform
marketing hosts, not merchant/admin hosts. Technical verification meta remains
home-page-only and separate from final badge state. Saved/configured/markup-ready
states never assert official approval or successful external image loading.

## QA provenance

All committed fixtures use synthetic identifiers. Tests cover strict parsing,
malicious snippets, no network calls, encrypted persistence, stage separation,
blank preservation/explicit clearing, visibility, CSRF/permissions, cross-store
writes, verified-domain homepage rendering even with a disabled footer, both
platform public layouts and platform-host isolation. A headless synthetic HTTP
503 image test confirms the official verification link remains present, with no
fake image, onerror handler or injected script. It is a QA simulation, not a
Production workaround or proof of eNamad's real status.

The footer renderer changes also invalidate the Vision Studio gallery preview
fingerprints. Real desktop/mobile footer captures are regenerated with the
existing `capture_global_region_previews --region footer` command against an
isolated local synthetic store; the unchanged gallery tests verify freshness
and declared visual twins. No Production media or issued badge is used.

Final SHA, exact test counts and full CI result are recorded in the PR after
verification. No merging or deployment is part of this task.
