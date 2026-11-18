# Administrator media aliases

The reconciler reads active and provisioning stores without pending deletion.
It uses platform-assigned admin labels, generated trial codes, and verified,
non-retired storefront labels under the configured primary platform host.
A cross-store collision is reported and the entry is left alone. A store may
have several labels. Unicode slugs and external domains are ignored.

Aliases appear on the next healthy reconciliation after a store is created.
The timer waits one minute after the previous invocation, with five seconds
of scheduling accuracy and a 60-second task timeout. An alias for an empty
store points to its expected UUID path and remains unresolved until the first
public upload creates that directory with its usual owner and mode.

The job creates only absolute symlinks to exact UUID paths under
`MEDIA_ROOT/stores`. A pre-existing target must be a real directory; symlink
targets and regular files are rejected. The job never creates media directories
or opens media content.
Suspension, closure, deletion requests, deletion and hostname changes make
obsolete managed links eligible for removal. Removal requires an exact match
to the ledger. Existing manually managed links, including the four Production
aliases, are preserved and may need administrator maintenance. The job records ownership before creating a link,
so an interrupted creation can be retried on the next run.
A name claimed by another store is held for manual resolution while the old
managed link still exists.

## Isolated trial and rollout

With explicit rollout approval, adapt the example units in
`deploy/media-aliases/` to the reviewed release layout. First trial them against
a synthetic database and media root. Provision a dedicated Unix account and a
separate root-owned environment file with the deployment settings needed to
start Django. Set database credentials to a dedicated read-only role granted
SELECT on the store and store-domain tables. The role needs no access to data in
other tables. Check that the account can traverse the canonical media path and
read the release, and has write access only to the alias directory. Do not grant
write access to the web account, deploy SSH account, or WinSCP operators. Keep
the parent root-controlled, the alias directory private, and grant traversal
and listing only to approved administrators using ownership or ACLs. The job
account is trusted to manage symlinks in that directory. Point WinSCP at it only
for trusted administrators who are already allowed to access the public media.
Never add it to an Nginx location or web-server document root. The filesystem
symlinks are dynamic, so the Unix accounts that can replace UUID directories
are also trusted.

Use the same Python and environment as the timer and run this dry-run first.

```sh
python manage.py sync_media_aliases --alias-root /srv/rastisi/media-by-name
```

Review actions and conflicts privately in the service journal, then run the
service once and verify links before enabling the timer. Check that the
existing aliases still resolve and that both the alias directory and its
ledger are inaccessible to web workers. Repeat the dry-run after the first
upload and after a label change in the trial environment.

For rollback, stop and disable the timer, then run the same command under the
dedicated account with `--remove-managed` to preview and with
`--remove-managed --apply` to remove exact managed symlinks. Remove the example
units after verifying the result. No media file is moved or deleted.
