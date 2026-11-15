# RastiSi CI/CD integration — 2026-11-15

Base: new repository main `f02ba7942f18eb7e9dacd8bc03ee5ae753838165`.
Bootstrap baseline: `86742072855eb0d937a63be0c7662919d0dbef87`, registered
only after exact per-file matching. The physical `5f8ca08...` release is kept
for rollback; its cleaned tree is not claimed to equal the old Git commit.

## Candidate implementation

- Restored supplied tests, legal references, raw/QA fixtures and tooling to Git.
  The 345 original QA images now use two hashed source archives, restored before
  CI without substitutes or dropped tests. A fresh restore matched every original
  byte. Seven archive-safety tests and all 18 original media-pipeline tests passed.
  Public repository publication of these images was explicitly authorized by the
  owner after private visibility was unavailable.
  The pricing audit document is regenerated from its canonical Python data.
- Production uses an explicit file allowlist: no docs, tests, QA commands,
  development tooling, raw image sources, pipeline internals or browser settings.
  Processed demo assets, selected-media manifest and seed fingerprint remain.
- Flat-layout bootstrap checks hashes, Python, app identity, shared paths,
  cron/timers, protected services and HTTP health before making changes.
  Default is plan only. Apply registers legacy links, installs restricted tooling,
  sets group inheritance on the two deployment parents, and changes only the
  RastiSi unit paths. Shared ownership/modes and RastiService remain unchanged.
- A failed bootstrap health check restores the old current link and drop-in.
  Installed tooling/configuration remain for inspection before retrying.
- Windows preparation creates two separate laptop keys, uploads public keys only,
  checks the tooling transfer SHA256, and exports public GitHub environment vars.
  Host keys come from the already trusted administrator SSH connection.
- Deployment builds one hash-pinned offline wheelhouse; CI and the server use
  that exact dependency set. Runtime requires Python 3.12, Linux x86_64 and
  glibc >=2.34. Supplied server metadata (3.12.3, glibc 2.39) is compatible.
- Code-only deployment needs no laptop backup. Additive migrations require a
  recent, separately signed laptop receipt bound to the actual source/target,
  configured local DB and SHA256 verification. DB/media stream straight to the
  laptop; only receipt metadata persists on the server. Old skip acknowledgments
  cannot bypass this. Unsafe online migrations remain blocked.

## Concrete blockers corrected

- Pillow upgraded to 13.0.0 and cryptography to 50.0.2 in the tested wheelhouse;
  requirements constrain their corresponding major versions. Latest audit:
  **0 advisories, 0 accepted exceptions**. Historical assessments are retained
  explicitly as historical; no vulnerability was waived.
- Definitive subscription refund failures now generate deduplicated inbox-only
  notices for the permitted store/platform recipients. Ambiguous outcomes remain
  silent; no payment/provider/state policy or amount calculation was changed.
  Notification exceptions cannot roll back a financial failure. Five focused
  transaction/recipient/delivery tests passed.
- CSS colour moved into its semantic root token; the stale privacy SEO test now
  checks the existing approval switch without publishing unapproved legal text.
- Demo group images use existing curated child media when no group artwork is
  set. Settings-page SMS data is lazy, retaining the original query budget.
  Five focused demo/query regressions and the separate SMS-tab check passed.
- Coupon-concurrency assertions now cover actual email/SMS consent suppression
  while retaining exactly-once issuance checks.
- OTP timing uses a controlled clock; real Redis tests prepare independent
  processes before starting expiry windows. TTL refresh remains forbidden.
  Contrast performance measures algorithm CPU time with the same budget.
  Media retention fixtures survive preceding transaction-test database flushes.
- Known-failure and flaky-exception registries are empty. No test tier was removed.

## Validation record and limits

- Final fast tier: **2096 tests, zero failures/tolerated/flaky**.
- Final real PostgreSQL/Redis security tier: **646 tests, zero failures/tolerated/flaky**.
- Finance full run: **2292 tests, one unexpected setup error** in the concurrent
  purchase test (checkout did not create a payment attempt). The six earlier
  fixture/consent problems and generated pricing document issue are fixed. A
  focused two-test PostgreSQL run passed; this alone does not resolve the full-run
  issue. Stronger login/checkout setup assertions now expose the cause on CI.
  No failure exemption/retry was added. The earlier tenant run completed **10056 tests with 38 failing IDs** (30
  failures and 10 errors including subtests). Several known query/demo/retention
  issues are already fixed in this candidate; other failures include missing
  captured reference images, old-repository Git history and stale v2 template
  expectations. These are unresolved blockers, not accepted exceptions.
- CI-gate/archive unit suite: **43 passed**. Final complete release-tooling suite: **144 passed**,
  no skips. The 17 focused receipt/packaging tests also passed.
- Disposable flat bootstrap success and deliberately failed-health rollback tests
  passed, using real users/filesystem/sudoers/sshd validation and simulated
  systemd/HTTP. Shared data/permissions and unrelated service operations checked.
- Prior installed integration passed real forced-command SSH, disconnect,
  concurrency, backup gates and 16 PostgreSQL/restic tests. A streamed laptop
  backup fully restored 300 synthetic orders and verified public/private media.
- The repeated eight-step Django/Gunicorn/PostgreSQL/Redis deployment rehearsal passed
  deploy, rollback, failed release, Redis outage, additive migration and persistent
  data checks. The repeated run uses the upgraded dependencies/product code (8 tests passed
  in 1129.980 seconds under shared CPU load); systemd alone was substituted. It is not a production end-to-end test.
- Production DB validation by the laptop helper is pg_restore readability, not a
  full production restore. No DB/media cross-resource transaction is claimed;
  media changing during transfer fails verification.
- Actionlint and production configuration checks passed; pinned Action commit
  URLs were checked. Committed source scanner: 2606 files, zero findings. Native
  Gitleaks 8.30.1 (upstream checksum verified) scanned the integration commits with
  zero findings after reviewing synthetic fixtures. The exported source (47.96 MB) also passed.
  Exact synthetic-value allowlists
  are scoped to rules AND individual paths, never whole test directories/files.
  A new random credential in the same allowed fixture file was correctly blocked.

## Remaining external activation

The GitHub connector is now connected with repository write permission. This
candidate is prepared for direct GitHub delivery and CI verification. Production
SSH installation, production environment configuration and live release have not
occurred. Both deployment and laptop-signing private keys stay on the laptop;
never send them in chat.

Unauthenticated GitHub API inspection returned `private=false`, `visibility=public`;
private visibility is currently unavailable to the owner. The owner explicitly
authorized public publication of the 345 QA images. No production secrets are
included; local/root/backup keys remain outside the repository.

Verify the GitHub CI run for the integration commit before production activation.
After the integration is on main, fast-forward the laptop checkout with git pull
--ff-only; preserve any local edits first. Run `Prepare-Production.ps1` for its default plan, inspect the plan,
set the `production` environment branch policy/reviewers supported by the account,
add only its deployment private key as `DEPLOY_SSH_KEY`, and enter exported public
variables. Apply bootstrap, then exercise a release and rollback on Production.
Keep root SSH and the backup signing private key on the laptop. Cron installation
is separate; sample CHANGE_ME values must not be installed as production jobs.
