# PR #2: remaining CI blockers

## Finance: process-global patch leaked by overlapping threads

Run 43817954577 first attempt ran 2,292 finance tests and failed
`ConcurrentPurchaseTests.test_parallel_verified_callbacks_activate_exactly_once`.
The preview was an upgrade, but the real view correctly refused to create a
positive-balance invoice with no checkout method.

The source was the preceding coupon contention fixture,
`PortalPurchaseContentionTests.test_last_free_allocation_goes_to_exactly_one_store_through_the_portal_view`.
Each of its two threads independently patched the same module attribute,
`platform_gateway_service.checkout_options`, to return an empty list. These
patches are process-global, not thread-local. The following legal schedule leaks
one mock:

1. Thread A saves the real function and installs mock A.
2. Thread B saves mock A and installs mock B.
3. Thread A exits and restores the real function.
4. Thread B exits and restores mock A.

`ready_platform_zibal` prepares a genuinely ready synthetic gateway, and
`gateway_state("zibal")` still reports ready. Clearing the cache cannot restore a
Python module attribute. The leaked `checkout_options` mock keeps returning `[]`,
so the actual readiness-aware checkout path correctly blocks invoice creation.
PostgreSQL is needed to run these contention tests, but the fault is not in its
transactions or payment services. Django's parallel process scheduling determines
whether the victim class follows the leaking class in the same worker, which is
why full-suite failures were intermittent and isolated purchase tests passed.

### Causal reproduction and repair

Before the fix, a local PostgreSQL run executed the coupon contention class then
the concurrent purchase class, with two events forcing A-enter, B-enter, A-exit,
B-exit for the existing worker patches. No gateway responses or financial
assertions were changed. The coupon and eight-request tests passed; the callback
test failed with the exact CI assertion: valid upgrade, no available payment
method, no invoice. Afterward `checkout_options is original` was false and the
attribute was a Mock.

After the fix, the same harness and three tests passed, with the original function
restored and no Mock left installed. The fix places ONE empty-checkout patch
outside the entire thread pool and waits for both workers before restoring it.
The coupon test now asserts the original function identity after the context.
The callback test additionally asserts real gateway readiness and real checkout
availability before exercising its unchanged HTTP purchase/payment/callback flow.
Its assertions still require one activation, the target active subscription,
and full invoice settlement. Production financial code and protection checks are
unchanged. Neither test is retried, suppressed or added to a failure registry.

## CI: consumer attempt number did not identify the producer artifact

The dependency job succeeded on attempt 1 and produced
`production-dependencies-43817954577-1`. A partial rerun increments the workflow
attempt without necessarily rerunning the dependency producer. The old consumer
constructed `production-dependencies-43817954577-2`, which did not exist. This
failed before either security or finance tests ran.

The successful dependency job now exports the upload action's immutable artifact
ID and SHA256 digest as job outputs. Every consumer passes those outputs to the
install action. A consumer never constructs an artifact name from its attempt or
chooses a latest/wildcard artifact. Producer names may still include the producer
attempt to avoid upload collisions when the producer itself reruns.

Before download, `tools/ci/dependency_artifact.py` checks GitHub metadata against
the exact producer ID, workflow run and digest, and rejects expired artifacts.
The pinned download action uses the exact immutable ID and treats archive digest
mismatch as an error. The subsequent install still uses the single shared
wheelhouse and hashed lock with `--no-index --require-hashes --no-deps`, followed
by `pip check`. There is no dependency re-resolution fallback.

The CI workflow and its reusable release caller grant only `actions: read` for
artifact metadata. No deployment permission or Production credential is added.
Regression tests cover initial and partial attempts retaining the producer ID,
missing/invalid identities, wrong run/ID/digest, expired artifacts, HTTP 404
without fallback, wiring of all six consumers, caller permissions and preserved
archive/pip integrity checks.

## Scope and result provenance

This correction changes CI artifact plumbing, the leaking financial test fixture,
its diagnostic assertions, regression tests and this report. It adds no database
migration and does not alter financial business logic, secrets, existing media,
failure registries, release backup gates or migration approval rules.

The earlier media correction's `content.0027_mediaasset_original_filename` remains
an additive column with an empty database default for N-1 writers. Existing file
paths and bytes are not moved or backfilled. Unknown legacy original filenames
continue to use the stored basename for display.

Final candidate SHA and complete GitHub CI results are recorded on PR #2 after
verification. Local checks alone are not a claim that the full pipeline passed.
