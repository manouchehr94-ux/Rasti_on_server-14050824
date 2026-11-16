# PR #2: tenant media CI failures

Input: CI run 43806067323, head 12a29cfd56e4148035de8d4e65c588c49d4951a4.
The complete `log-tests-tenant` artifact (ID 14199584590) was downloaded and its
SHA256 verified: `21c1f07ab1d1096f91bbb02950ce4a312f8c8c68db153a37be57568bd467510f`.
The full log reports 10,059 tests, eight failures. All eight failures reproduced
locally on an isolated test database before any fixes (8/8 failed).

## Individual root causes

| Test (module/class.method) | Complete assertion meaning | Classification and repair |
| --- | --- | --- |
| `apps.cart.tests.test_item_media.CartItemImageTests.test_cart_page_and_checkout_show_the_variant_image` | `black` absent from the cart/checkout HTML | Obsolete storage-name expectation. The selected image is correct but its URL contains a random key. Check the actual variant image URL in image elements, exclude the cover URL there, and retain placeholder checks. No cart product logic changed. |
| `apps.cart.tests.test_item_media.CartItemImageTests.test_variant_image_beats_cover_and_cover_is_the_fallback` | `black` absent from resolver URL | Obsolete storage-name expectation. Check exact selected variant URL plus alt text and cover fallback, not an original filename substring. |
| `apps.storefront_builder.tests.test_media_default_images.AdoptDefaultsTests.test_replace_one_adopted_image_leaves_the_others_untouched` | `replacement` absent from asset storage key | Obsolete expectation. Check the owning store namespace, original display name, distinct replacement asset and different known PNG bytes, while retaining the checks that other images/defaults stay untouched. |
| `apps.storefront_builder.tests.test_media_picker.MediaUploadAndInspectorTests.test_an_upload_creates_an_asset_in_this_store_only_and_returns_what_the_picker_needs` | JSON `name` no longer starts with `my-hero` | Real UI regression: upload response used the randomized storage basename. Preserve original basename as metadata and return display_name. The original assertion is retained. |
| `apps.storefront_builder.tests.test_media_picker.MediaUploadAndInspectorTests.test_the_stage_inspector_shows_file_names_the_upload_url_and_the_guide_spec_never_another_stores_pictures` | Inspector lacks `data-name="brand-banner...` | Real UI regression: option labels/data attributes exposed storage keys. Render display_name with normal HTML escaping, without changing store-scoped selection. The original assertion is retained. |
| `apps.storefront_builder.tests.test_media_views.StoryRailItemCrudTests.test_edit_story_item_replaces_image_and_creates_new_asset` | `second` absent from stored image key | Obsolete expectation. Check random store-scoped key, original display name, exact different replacement PNG bytes and the asset-to-file link. |
| `apps.storefront_builder.tests.test_r4_media_modal.ModalFormContractTests.test_one_submission_carries_values_from_every_tab` | `new` absent from slide storage key | Obsolete expectation. Check namespace, original display metadata and replacement bytes, retaining all other tab-value and destination assertions. |
| `apps.storefront_builder.tests.test_shared_capabilities.CategoryImageFieldTests.test_category_model_has_image_field` | upload_to callable differs from `categories/images/` | Obsolete field-configuration expectation. Generate a real path for a store-owned Category and check its stable public UUID namespace, purpose, random basename and extension; retain blank/null field checks. |

## Production correction and compatibility

`MediaAsset.original_filename` is display metadata, not a path. Upload naming
captures it before randomization; placement uploads supply the incoming basename
when creating an asset that references an already saved file. The upload response,
layout-tree JSON, inspector picker and legacy background selector use display_name.

The new column has both a Python default and a database default of the empty
string. Migration `content.0027_mediaasset_original_filename` is ordinary ADDITIVE;
it does not rename/move/delete files or backfill legacy metadata. Old application
INSERTs that omit the new column still work. Legacy records use their existing
stored basename as a read-only label fallback. Unknown historical original names
are not invented or recovered from random keys.

Additional regression coverage checks duplicate client names with distinct random
storage keys, exact stored bytes, persisted original names including FieldFile.save
and update_fields, Persian labels, path/control-character removal, HTML escaping,
store-scoped JSON, private media protections and unchanged legacy file references.

No skip, tolerance entry, retry allowance, test-count reduction or financial
business-logic change is introduced. Production and existing stored media are not
accessed or modified. The PR remains unmerged and undeployed.

## Local verification of the correction

* Before correction: all eight original CI failures reproduced (8 failures / 8 tests).
* After correction: all eight original tests plus six new regression checks pass
  (14 focused checks, zero skips).
* Relevant media, retention, picker, cart, modal, namespace/private-file and legacy
  regression modules: 794 tests pass.
* PostgreSQL-specific display metadata / old-writer database-default checks: all
  five pass on a disposable PostgreSQL database.
* Migration-classifier unit tests: all 18 pass. The new migration classifies as
  ADDITIVE without adding an exception or altering the four existing approvals.
* Django checks, makemigrations --check, compilation and git diff --check pass.

The full GitHub CI result and final commit are recorded on PR #2 after pushing;
local validation does not substitute for that pipeline. No merging or deployment
is authorized as part of this correction.
