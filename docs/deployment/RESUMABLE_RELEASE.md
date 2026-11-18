# Resumable RastiSi releases

The existing manual workflow and Production environment reviewer are required.
The workflow verifies the artifact hash, runs CI against the exact commit, and
checks installed tool compatibility before transfer. Activation rechecks the
currently serving commit, preflight, migration plan and health. It uses the
same deployment lock, safety fence, supported migration classes and rollback
policy as earlier releases.

## Transfer and recovery

The restricted SSH gate accepts fixed verbs with commit and digest arguments.
One staging slot is shared by all callers. Each request is at most 2 MiB; the
partial checkpoint advances only after validation of the chunk hash and durable
write. The server checks the complete length and SHA256 before atomically
promoting the spool file. Stage also checks identity and contents within the
archive and publishes a complete release under the deployment lock. Stage and
activation have independent run IDs. A known-complete artifact can reuse a
matching release that is still retained on disk.

Use the reviewed SSH configuration with pinned host keys and the same artifact
from the original build on a replacement runner.

```sh
python deploy/release/remote_client.py stage --sha "$SHA" --sha256 "$DIGEST" --artifact "$ARTIFACT"
python deploy/release/remote_client.py follow --run "$RUN_ID"
python deploy/release/remote_client.py status
```

Re-run the failed deploy job in the same GitHub workflow to reuse its build and
activation ID. Preserve the run ID printed before an activation request. If a
runner stops before printing the ID, the ID for that workflow is
`19700101T000000Z-` followed by the first 32 hex digits of the SHA256 of
`GITHUB_REPOSITORY/GITHUB_RUN_ID` (without a newline). A different dispatch is
a new activation request and requires a fresh review of the serving commit.

The next distinct upload reclaims a finished slot or a partial slot untouched
for 24 hours. A corrupt checkpoint or an orphaned launch without a PID requires
operator action. Verify that neither the gate supervisor nor its Stage child
is running and that no release operation holds the deployment lock. Temporarily
quiesce deployment requests and move the entire `staging/resumable` directory
aside as the deploy account. Keep `state/runs` and `state/INCIDENT` for audit.
Restore it only with deployments quiesced and after identifying the cause.
A Stage rejected as busy may be retried with the same artifact after the
operation holding the deployment lock ends. Other terminal outcomes require
review before discarding the slot. Never bypass a migration incident with an
automatic re-run.

For a connectivity trial, upload a synthetic artifact on an isolated deployment
layout and interrupt the runner after several checkpoints. Resume with the same
build and inspect the reported byte offset. Interrupt the connection after the
Stage and Activate launch responses, then use their run IDs to obtain the
result. In GitHub, manual dispatch with `dry_run=true` can upload and Stage and
run preflight without activating. Run the rehearsal on an isolated target
first. A Production dry run writes staging and state and needs separate approval.

A local transport test sent 67 MiB (70,254,592 bytes) to the real gate over
subprocess pipes in 2.193 seconds, with three deliberately truncated requests
and a checkpoint query after each interruption. The saved bytes had the
expected SHA256. These timings exclude SSH, WAN latency, extraction and
virtualenv setup. Measure all four on the isolated SSH rehearsal before setting
an operational SLA. The chunk retry timeout is 90 seconds and the deploy-job
budget is 90 minutes.

## Backup and approval

Read only the `backup_mode` setting from the root-owned deployment config
during the approved rollout. In `laptop` mode, retain the existing backup
and source/target-bound signed receipt process. Its freshness is verified on the
server before real activation, including a code-only release. A missing restic
tool is expected in this mode. If the config selects `restic`, use its
configured verification and restore procedure and retain the migration backup
gate. An unknown setting needs investigation.

Before a real workflow dispatch, restore the backup in an isolated PostgreSQL
instance, verify the expected database checks and validate the media manifest.
Keep the evidence with the backup. Supply a short opaque evidence reference
as `restore_drill_ref`, with no sensitive details. Before approving the
Production environment, the owner verifies that it names the drill for the
exact backup and commits. GitHub Actions cannot inspect a laptop's backup or
prove a restore. The owner's evidence check and the server's freshness and
signature check are separate. Coordinate backup timing with the build, test
and approval window. If expiry occurs, obtain and verify a fresh backup
before retrying, and use a new approved dispatch if the activation run already
ended.

## Versioned installed tools

After explicit approval, update tools before enabling the new workflow. Review
PR #7 and confirm the staged bundle contains its binary static response fix
and the exact GitHub `migration_guard.py` with the four reviewed SHA256
entries. In a clean worktree at the approved commit, collect the files named
in the tooling copy section of `deploy/release/install.sh`, including
`smoke.d`, `run_current_job.sh` and `TOOLING_VERSION`. Compute a SHA256
manifest from that checkout and verify all files against it after transporting
the bundle. Review the manifest independently of the transfer. Keep the
backup mode, service unit, SSH restrictions, sudo policy and deploy config
in place.

On an isolated replica, place the verified files in a versioned root-owned
directory beside `/usr/local/lib/rastisi`. Make directories mode 0755,
tools mode 0644 and the job wrapper mode 0755. Verify no group/world-writable
parents and check the exact configured application service and deployment
account. Run the release tooling suite, the edge probe TLS/binary tests, the
migration classification tests and a complete Stage/Activate/rollback
rehearsal against synthetic data before scheduling a Production change.

During an approved tools maintenance window with deployment requests
quiesced and no live runs, retain the installed directory as a rollback
bundle. Point the existing entry path at the new versioned directory. A legacy
physical directory needs a one-time move aside before placing the symlink.
Later upgrades can atomically rename a prepared symlink over the existing
one. Verify the gate's `version` against the workflow, run `status`, and
review the read-only configuration check. Restoring the previous tooling
bundle or pointer requires the same quiescence. Preserve the existing binary
probe fix when selecting a rollback bundle.

For a failed activation, first inspect `follow`, `status` and any incident
record. If automatic rollback succeeded, verify the previous release is
healthy. If the state machine fenced the deployment, verify the database and
migration state before resolving the incident. A manually authorized code
rollback uses `remote_client.py rollback --to previous` only after verifying
the previous release and schema compatibility. See
[Release pipeline](RELEASE_PIPELINE.md) for the operator incident procedure.
