# Store-owned media layout

New store uploads use `stores/<Store.public_id.hex>/<purpose>/<random UUID>.<extension>`.
The public UUID is stable across shop name, slug and domain changes. Filenames do not
contain customer-supplied names. Each folder is created by storage on the first upload;
empty folders are not provisioned. Resolve a folder to its shop using `Store.public_id`,
not its name or current domain.

## Covered writers

* Catalog: vendor/brand logos, categories, product gallery, thumbnails, attribute
  swatches and collections. Product ownership comes from `ProductImage.product.store`;
  swatch ownership comes from `AttributeValue.attribute.store`.
* Shop settings: logo, favicon and gift wrapping.
* Content: media library, hero desktop/mobile images, banners, footer trust/payment
  logos and story rail images.
* Store blog covers. Explicitly platform-owned (`store IS NULL`) editorial posts
  use `platform/blog/covers/`. Other ownerless uploads fail instead of falling back
  to a platform or compatibility shop.
* Private exports, imports, import error reports and payment receipts use the same
  store namespace under **PRIVATE_MEDIA_ROOT**, never the public media root.
* Product upload originals follow the final stored gallery filename under that
  shop's `products/originals/`. Thumbnail names remain independently random.

Platform configuration logos and public platform photos keep their existing platform
paths; they have no Store owner. Static assets, source demo fixtures, shared default
assets and existing same-store asset references are not uploaded tenant files.

## Access and compatibility

Folders organize data; they are not authorization. Public storefront images are
public URLs. Private storage rejects `.url()` even when MEDIA_URL is configured;
existing authenticated, store-scoped download views remain the access boundary.
MediaAsset ownership validation and file-retention/reference checks still apply.
Never allow a request's store ID or submitted filename to pick the owning directory.
Assign the actual model owner before saving an uploaded file.

Changing upload_to affects new bytes written through FileField/ImageField.save only.
Existing stored names remain readable and are not rewritten on ordinary model saves.
Product original lookup/deletion supports both legacy and new layouts. No existing
files are moved or deleted by this change. Same-store placements can continue to
reference the same existing file instead of duplicating bytes.

## Deployment and later migration

Four new Django migrations use SeparateDatabaseAndState with empty database operations
to record upload_to field state without backend-dependent table rebuilds. They do not copy files,
rewrite stored database paths or change field database types. The four exact migration blobs are reviewed in migration_guard.py by path and
SHA256 as compatible (ADDITIVE). Any byte/path change loses that approval; editing
an already published migration remains DESTRUCTIVE. Unknown state-only migrations
still require REVIEW. The fresh verified-backup requirement remains in force even
for these no-SQL migrations. Review `sqlmigrate` on the target backend before release.

Existing-shop migration is a separate phase: inventory references and ownership,
identify shared/ambiguous files, take verified backups, prepare a dry-run mapping,
copy and SHA256-verify bytes, update exact database references transactionally, and
verify storefront/private downloads before considering removal of old files. Do not
move directories by hand, infer ownership from filenames, or delete shared files.
New uploads for existing shops also use their own namespace after this release;
old files stay in their original locations until that separate migration.

## Verification

`apps.core.tests.test_store_media_paths` inventories all 23 store-owned file fields,
checks namespace separation, path traversal resistance, name-length limits, missing
owners, migration serialization, platform exceptions, private URL denial, exact
original bytes, deletion isolation and unchanged legacy references.
