# Public media HTTP 403: all-store repair policy

## Scope

The Django `MEDIA_ROOT` contains public uploads, not exports, imports, receipts,
private transaction files, backups or credentials. RastiSi Production currently
uses `/srv/rastisi/shared/media`; private media is a separate sibling tree.
Do **not** change `PRIVATE_MEDIA_ROOT`.

On 2026-11-18, a real ThermCo product image and thumbnail were present on
disk and linked in the database, yet their public HTTPS URLs both returned
403. The store UUID directory was `0755 rastisi:rastisi`. Its
`products`, `gallery`, and `thumbnails` children were
`0750 rastisi:rastisi`; the Nginx worker `www-data` was not a member of
that group. The files themselves were `0644`. No upload/database rewrite
could fix this permission-chain failure.

## Future public uploads: application rule

`shop_core/settings.py` now sets the default **public** filesystem storage
to create directories with mode `0751` and files with mode `0644`.
The final `1` on a directory is **traverse-only**, not directory listing.
The file's final `4` permits public reads even if the inherited group is
`rastisi` rather than `www-data`. A restrictive application process
umask must not defeat this rule.

This affects default public storage for all stores, including product
gallery/thumbnails, category images, logos, banners, blog covers and other
published assets. The independent `PrivateFileSystemStorage` remains
private; do not globally change its mode, `umask`, or storage backend.

Django's storage permissions apply when directories/files are **created**:
they do not repair existing directories and historical uploads.

## Existing public images: gated operational repair

Before any Production mutation:

1. Verify Production release SHA and that no rollout/incident is in progress.
   Back up and independently verify the current media tree and relevant
   database snapshot; retain the existing signed laptop backup requirements
   for any later application activation.
2. Run a **read-only inventory**, from the real `DJANGO_MEDIA_ROOT`,
   checking the effective `www-data` read access to every known public image
   on disk. Include legacy/platform paths, not only UUID stores. Report
   image counts, unreadable counts per store, actual path components,
   ownership/group/modes, broken symlinks and unknown/unexpected entries.
   Check database image references separately for dangling paths or images
   without ordinary filename extensions.
3. Have the operator review an exact manifest of only the minimum changes:
   directories blocking traversal can gain `o+x` (not `o+r`), and
   **public image** files blocking reads may gain `o+r`. Never blindly
   `chmod -R`, `chown -R`, reparent or move the whole media directory.
   Do not touch private media, unknown/symlink targets, other tenants'
   non-image files, or anything outside the verified public root.
4. Any apply tool must compare current inode/type/owner/group/mode against
   the reviewed manifest, refuse changed inputs, avoid following symlinks,
   preserve original modes for rollback and stop on unexpected ownership.
   Root and ancestor directories need separate review if they are blocked.
5. Verify a representative set of gallery, thumbnail, logo, category and
   other image URLs with **real HTTPS**, and confirm `200 image/*` where
   applicable; check readability as the actual web worker. Confirm the
   private filesystem and privileged routes are still inaccessible.
   Retain the manifest, before/after checks, and rollback plan privately.
6. Make the same checks for a newly created synthetic store **after**
   deploying the approved application fix, under restrictive umask and
   different application/web-server groups. Recheck older store images.

The user-facing all-store PowerShell audit is **read-only** and must report
the actual number of blocked public photos before any repair is approved.
A public-image filename scan alone cannot guarantee the health of every
database reference, every private asset or every remote CDN URL.

## Release interlock

This defect was observed while the running Production SHA was
`8158a21f5d20252cc041dec7f924504def51cf1a`.
Do not attempt to treat the pending production rollout or root-owned tooling
replacement (PR #13) as an image-permission fix. In Production, image
permission repair and application deployment require **separate approvals**.
Merged code and green PR CI do not themselves authorize either change.
