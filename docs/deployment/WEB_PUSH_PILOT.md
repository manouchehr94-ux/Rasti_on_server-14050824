# RASTISI Web Push pilot

Release through the existing approved workflow. The new policy table is additive.
The global and per-store policy default to off. Environment flag RASTISI_NOTIF_WEBPUSH_ENABLED
remains an infrastructure kill switch. Provision reviewed VAPID credentials in the existing
RASTISI environment separately from enabling policy. With the flag and credentials ready,
the platform owner can stop sending from Platform Settings or Store Detail without a restart.
Queued disabled-scope deliveries are skipped when processed. An in-flight delivery or a push
already accepted by the provider cannot be recalled. Seller browser consent stays required.

After the standard release is healthy and with separate permission, review the unit under
deploy/cron. Set its user, launcher path, and an EnvironmentFile with the verified RASTISI
RASTISI_RELEASE_BASE, RASTISI_ENV_FILE, RASTISI_LOG_DIR and alert settings. The worker uses
the same fail-closed cron wrapper, PostgreSQL advisory lock, claim, retry/backoff, and log sink.
Do not point to a second site or share its environment. Install only this reviewed service
and remove only the existing inbox_delivery cron line if installed. Other cron jobs remain.

The watcher sleeps five seconds between cycles, runs a single batch of at most ten and
uses no external provider on an idle pass. The hot idle path is one queue check and two
advisory-lock operations per cycle, plus transaction control. A batch with slow provider
responses can take longer. The shared release lock is held only during a batch; when the
current release changes, the worker exits and the supervisor starts the current code.
Start with policy off. Check worker PSS, CPU, DB connections, queue age and errors while idle
and while sending one synthetic order. Host capacity and end-to-end delay need measurement.

Enable one test store and then the global policy. Confirm the second store cannot register,
and compare enqueue, provider acceptance and physical reception timestamps. Disable the
store and verify subsequent sends do not run. If the worker itself must be withdrawn, stop
only rastisi-inbox-delivery and restore the previous inbox_delivery cron entry if appropriate.
Keep the additive table when reverting application code. No steps are automatically run on
the host by this change.
