#!/usr/bin/env bash
# Pin one release for startup; the Python worker takes the lock per batch.
set -euo pipefail
base="${RASTISI_RELEASE_BASE:?set the verified RASTISI release base}"
exec 9<"$base/state/deploy.lock"
flock -s -w 60 9 || exit 75
release="$(readlink -e "$base/current")"
case "$release" in "$base"/releases/*) ;; *) echo 'invalid RASTISI current release' >&2; exit 96 ;; esac
export RASTISI_APP_DIR="$release/app" RASTISI_VENV="$release/venv"
flock -u 9
exec 9<&-
exec /bin/bash "$release/app/deploy/cron/run_job.sh" inbox_delivery process_inbox_deliveries --watch --release-base "$base"
