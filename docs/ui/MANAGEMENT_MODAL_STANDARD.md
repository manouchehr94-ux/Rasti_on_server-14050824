# Management modal audit and implementation inventory

Audit baseline: PR #5 HEAD `89c5f58e75f9b68f0bc277836d80e47aa0deaabe` (unmerged).
New stacked branch targets `codex/enamad-snippet-import-20261116`; main/PR #5 are not modified.

## Audit method and result

Searched all first-party templates, CSS and JavaScript in apps/ and templates/ for drawer/offcanvas/side-panel/slide-over, dialog/role=dialog, opening/closing handlers, fixed edge anchoring and translateX. Vendor minified libraries were excluded from behavioral classification. Read the associated templates/controllers rather than treating names as intent. The only A family outside Studio is the integration-management native dialog, instantiated for every provider. Its interaction is Manage -> edit/configure provider -> real CSRF POST. All providers inherit the same migration; no other qualifying drawer remains identified.

| File | Classification/component | Planned action |
|---|---|---|
| `templates/base.html` | B — permanent/mobile navigation | Preserve |
| `templates/partials/mobile_nav_drawer.html` | B — permanent/mobile navigation | Preserve |
| `apps/catalog/static/css/product_detail.css` | E — unrelated picker/product presentation mention | Preserve; not a side-opening management interface |
| `apps/core/static/core/jalali_picker.js` | E — unrelated picker/product presentation mention | Preserve; not a side-opening management interface |
| `apps/core/static/css/layout.css` | B — permanent/mobile navigation | Preserve |
| `apps/storefront_builder/static/storefront_builder/r4_studio.js` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/storefront_builder/r4_editor.js` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/storefront_builder/r4_studio.css` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/css/storefront_builder_r3.css` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/storefront_builder/vision/vision.js` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/css/storefront_builder.css` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/css/storefront_builder_v22.css` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/css/storefront_builder_preview_v22.css` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/storefront_builder/vision/vision.css` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/static/storefront_builder/r4_composer.js` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/storefront_builder/partials/page_shell_header.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/storefront_builder/preview.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/vision/studio.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/storefront_builder/editor.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/storefront_builder/partials/footer_panel.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/storefront_builder/partials/section_settings_form.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/storefront_builder/partials/container_settings_form.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/storefront_builder/partials/header_panel.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/storefront_builder/templates/dashboard/storefront_builder/r4/editor.html` | D — excluded Studio/Builder (including storefront navigation) | Preserve byte-for-byte |
| `apps/dashboard/static/css/admin.css` | B — permanent/mobile navigation | Preserve |
| `apps/dashboard/static/css/admin_ui.css` | A — shared integration management dialogs | Migrate presentation/controller only; preserve IDs, POST fields, accordions and routes |
| `apps/dashboard/templates/dashboard/partials/settings_integrations.html` | A — shared integration management dialogs | Migrate presentation/controller only; preserve IDs, POST fields, accordions and routes |
| `apps/dashboard/templates/dashboard/partials/settings_integration_card.html` | A — shared integration management dialogs | Migrate presentation/controller only; preserve IDs, POST fields, accordions and routes |
| `apps/dashboard/templates/dashboard/partials/settings_integration_drawer.html` | A — shared integration management dialogs | Migrate presentation/controller only; preserve IDs, POST fields, accordions and routes |

## Existing centered dialogs and popovers (E, preserve)

| File/component | Interaction and decision |
|---|---|
| `apps/dashboard/templates/dashboard/card_transfers.html` / c2cRejectDialog | Centered rejection confirmation; preserve form and financial behavior |
| `apps/dashboard/templates/dashboard/import_detail.html` / final-import-modal | Existing centered import confirmation; preserve |
| `apps/dashboard/static/css/admin.css` / overlay, price-modal-overlay | Centered content/price overlays, not edge management drawers; preserve |
| `apps/dashboard/static/css/admin_v2.css` / command/search overlay | Centered search/navigation; preserve |
| `apps/dashboard/static/css/campaign_wizard.css` / cw-overlay | Centered wizard popup; preserve full-page workflow |
| `apps/portal/templates/portal/platform_admin/store_detail.html` / dlg-suspend, extend-trial, set-trial-end, end-trial-now, change-plan, reinstate-fin, extend-sub | Existing centered pa-dialog action forms; preserve all seven and all financial POSTs |
| `apps/portal/templates/portal/platform_admin/user_detail.html` / dlg-suspend | Existing centered action dialog; preserve |
| `apps/portal/templates/portal/platform_admin/sms_credits.html` / per-store dialog | Existing centered credit adjustment; preserve |
| `apps/portal/static/portal/css/platform-admin-panel.css` / pa-dialog | Centered platform dialog styling; preserve |
| `apps/portal/templates/portal/app/onboarding_template.html` / ob-lightbox | Existing centered template preview; preserve owner onboarding |
| `apps/notifications/templates/notifications/partials/bell.html`, `apps/notifications/static/notifications/inbox.css` / nt-panel | Anchored notification-preview popover, not a sliding edit/configuration drawer; preserve bell/inbox management pages |
| `apps/dashboard/static/js/admin_ui.js` / confirmation dialog | Preserve generic confirmation and accordion behavior; exclude opt-in management backdrop from legacy close handler |

## Accordions and navigation exclusions

All native details/summary elements and au-acc expandable sections remain intact, including eNamad advanced manual entry. Permanent dashboard/platform sidebars, merchant/storefront mobile navigation and marketing mobile menus are navigation (B), not migration targets. Entire Vision Studio, r4/r3/v22 editor/composer panels, header/footer editors and their keyboard/mutation architecture are D and must not change.

## Standard contract

Implementation and QA results are recorded here after verification. The standard is explicitly opt-in; existing centered dialogs keep their current dimensions/handlers. Only qualifying management drawers migrate. No backend parser, credential, authorization, payment or database behavior is changed. Sensitive/raw eNamad HTML continues to be redacted on server-validation redisplay, as required by the established security contract; editable non-sensitive values remain preserved.

## Additional fixed/popover findings

- `apps/portal/static/portal/css/public-v2.css`: marketing mobile navigation (B), preserve.
- `apps/core/static/core/consent.css`: centered bottom consent/preferences banner, not a side-opening management drawer (E), preserve.
- `apps/core/static/core/jalali_picker.css` and `.js`: anchored date-picker popup (E), preserve.
- `apps/catalog/templates/catalog/partials/size_guide.html`, `product_card.html`, `apps/catalog/static/css/product_card.css`, `product_detail.css`: storefront size/quick-view presentation and fixed decoration (E), outside management migration, preserve.
- `apps/core/static/css/tokens.css`, `occasion_theme.css`: tokens/theme decoration, not management dialogs (E), preserve.
- `apps/notifications/templates/notifications/inbox.html`, `detail.html`, `preferences.html`: full-page message/preferences workflows; preserve, not converted to dialogs.

## Preserved accordion inventory (C; Studio paths remain D)

The following first-party templates contain native details or the existing au-acc expansion contract. No accordion control/handler is replaced. The eNamad advanced-details block is byte-identical to the validated baseline; only its enclosing management dialog changes.

| Template | Native details / au-acc occurrences | Action |
|---|---:|---|
| `apps/catalog/templates/catalog/product_list.html` | 1 | Preserve C |
| `apps/customers/templates/customers/partials/order_tracker.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/campaign_form.html` | 5 | Preserve C |
| `apps/dashboard/templates/dashboard/coupon_form.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/footer_settings.html` | 7 | Preserve C |
| `apps/dashboard/templates/dashboard/import_upload.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/notification_history.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/product_form.html` | 2 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_domain.html` | 2 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_finance.html` | 3 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_gateway_card.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_integration_drawer.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_integrations.html` | 4 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_sms.html` | 2 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/settings_unpaid_orders.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/partials/setup_checklist.html` | 1 | Preserve C |
| `apps/dashboard/templates/dashboard/shipping_setup.html` | 1 | Preserve C |
| `apps/notifications/templates/notifications/inbox.html` | 1 | Preserve C |
| `apps/portal/templates/portal/app/custom_domains.html` | 1 | Preserve C |
| `apps/portal/templates/portal/platform_admin/_sidebar.html` | 1 | Preserve C |
| `apps/portal/templates/portal/platform_admin/configuration.html` | 1 | Preserve C |
| `apps/portal/templates/portal/platform_admin/features.html` | 1 | Preserve C |
| `apps/portal/templates/portal/platform_admin/partials/duplicate_actions.html` | 1 | Preserve C |
| `apps/portal/templates/portal/platform_admin/payment_gateways.html` | 5 | Preserve C |
| `apps/portal/templates/portal/platform_admin/plan_form.html` | 1 | Preserve C |
| `apps/portal/templates/portal/public/help.html` | 11 | Preserve C |
| `apps/portal/templates/portal/public/home.html` | 5 | Preserve C |
| `apps/portal/templates/portal/public/plans.html` | 3 | Preserve C |
| `apps/portal/templates/portal/public/terms.html` | 1 | Preserve C |
| `apps/portal/templates/portal/public_v2/_faq_item.html` | 1 | Preserve C |
| `apps/portal/templates/portal/public_v2/plans.html` | 5 | Preserve C |
| `apps/portal/templates/portal/public_v2/privacy.html` | 1 | Preserve C |
| `apps/portal/templates/portal/public_v2/terms.html` | 1 | Preserve C |

## Reusable implementation

Opt in with a native `dialog.au-modal.au-modal--management[data-management-modal]`, a unique title/description ID, `.au-modal__head`, `.au-modal__body`, `.au-modal__foot`, and primary `form[data-management-form]`. Include `css/admin_ui.css`, `js/admin_ui.js` and `js/management_modal.js` in an admin shell (the merchant shell does so). Existing design tokens provide light/dark colors. Other shells can opt in without changing their existing dialogs.

Open/close with `data-management-open="dialog-id"` and `data-management-close`, existing `data-au-open="#dialog-id"` / `data-au-close`, or the retained integration `data-drawer-*` aliases. Programmatic callers use `RastiManagementModal.open(dialog, opener)` / `.close(dialog)`; close is guarded. Native `showModal()` supplies top-layer/inert and screen-reader semantics; the controller handles focus restore, visible invalid focus, keyboard boundaries and scroll locking. Unsupported native-dialog browsers are not given an inaccessible fake-modal fallback.

The controller is installed once, wires each dialog once, consumes automatic-open once per element, supports htmx reinitialization, ignores stale queued close events and follows provider-only deep links through existing server-rendered authorized links. Dirty snapshots cover marked primary forms, exclude CSRF tokens and normalize empty file inputs; Escape, X, footer and true outside-rectangle backdrop clicks all confirm real changes. Cancel retains edits; confirmed discard restores the opening state. Server-invalid non-sensitive values remain visible; raw eNamad HTML remains transient/redacted under the existing backend policy. Native details open to reveal an invalid nested field when required.

Desktop width is min(88vw, 1180px), max-height is viewport minus margins; mobile/tablet use nearly full width with dvh and safe-area bounds. Flex header/footer remain visible while the body scrolls; fields/fieldsets cannot force horizontal overflow. Close/actions have 44px targets. Theme-token contrast, visible focus and reduced-motion handling are scoped to opt-in dialogs. No global dialog rule is changed.

## File-by-file change report

| File | Change |
|---|---|
| `apps/dashboard/static/css/admin_ui.css` | Scoped large centered modal geometry, scrolling, themes, focus and touch targets; remove edge drawer geometry |
| `apps/dashboard/static/js/management_modal.js` | Reusable opt-in controller, dirty-state protection, native accessibility and legacy aliases |
| `apps/dashboard/static/js/admin_ui.js` | Delegate only opt-in management open/close; avoid legacy backdrop bypass; existing confirmation/accordion logic preserved |
| `apps/dashboard/templates/dashboard/base_admin.html` | Load controller once and version changed assets |
| `apps/dashboard/templates/dashboard/partials/settings_integration_drawer.html` | Standard container/slots and accessible description; all fields, routes, IDs and native advanced details preserved |
| `apps/dashboard/templates/dashboard/partials/settings_integration_card.html` | Accessible controls association, existing entry points retained |
| `apps/dashboard/templates/dashboard/partials/settings_integrations.html` | Remove competing drawer controller; keep scoped/idempotent clipboard and submit-loading handling |
| `apps/dashboard/tests/test_management_modals.py` | Every-provider accessible modal/real POST+CSRF contract; native advanced fields preserved |
| `tools/qa/management_modal_browser.py` | Mandatory isolated Chromium suite with real local Django forms, responsive/theme and before/after evidence |
| `tools/qa/requirements-management-browser.txt` | Pinned dev/CI-only Playwright; no production dependency changes |
| `.github/workflows/ci.yml` | Mandatory browser suite and safe screenshot/report artifact in existing static job; all quality/security gates retained |
| `docs/ui/MANAGEMENT_MODAL_STANDARD.md` | Full inventory, contract, preservation decisions and verification evidence |

## QA and evidence

- 120 targeted Django integration/eNamad/platform contracts pass; existing CSRF/permission/tenant regressions remain intact.
- Chromium covers all five providers, overview and attention links, direct provider-only/deep links, every tab button, Tab/Shift+Tab, focus restore, all four dirty-close paths (cancel and discard), valid/invalid real POST, preserved values, separate provider endpoints, native advanced accordion, XSS rejection/redaction and generic legacy confirmation compatibility.
- Mandatory dimensions/themes: 1440, 1366, 768 and 390px, each light/dark. Check centered bounds, no horizontal overflow, internal wheel scrolling without background movement, persistent footer, 44px targets, RTL/LTR and close contrast >=4.5:1. Repeated initialization and rapid reopen are checked with empty-file state.
- Screenshots are generated from synthetic local fixtures only. BEFORE presentation is reconstructed from the exact verified baseline Git templates/CSS/JS against the same test database; AFTER uses the candidate. No Production screenshot or customer authorization code is published.
- CI uploads `management-modal-evidence-<run>-<attempt>` containing before desktop/mobile, after all viewport/theme combinations, form-focused desktop/mobile and `report.json`. Session cookies, fixture databases and private server logs are outside the artifact and are cleaned up.
- Full final-SHA eight-job CI and final readiness are recorded in the PR after completion. Existing historical opt-in/environment skips are reported accurately; no new skip or failure allowance is introduced.

## Coverage / blockers

No identified A management drawer remains unconverted. All D Studio/Builder sources, B navigation, C accordions and E existing centered interfaces are preserved. Existing full-page workflows stay full-page. No parser, eNamad badge URL logic, payment/subscription/checkout rule, database schema or Production data is changed. This PR is stacked on PR #5 until that branch is merged; do not merge/deploy automatically. Owner visual review remains required. Final CI status is not claimed before the run finishes.

## Review screenshots

All images below use local synthetic data. Before assets are pinned to the validated PR #5 commit; after images show this working candidate. Final-SHA CI evidence is authoritative and includes source checksums; no customer or Production data is used.

| Case | Before | After |
|---|---|---|
| Desktop | [1440 before](management-modal-evidence/before-1440-light.png) | [1440 centered](management-modal-evidence/after-1440-light.png), [form](management-modal-evidence/after-1440-form.png) |
| Mobile | [390 before](management-modal-evidence/before-390-light.png) | [390 centered](management-modal-evidence/after-390-light.png), [form](management-modal-evidence/after-390-form.png) |
| Dark theme | Existing theme preserved | [desktop](management-modal-evidence/after-1440-dark.png), [mobile](management-modal-evidence/after-390-dark.png) |

The complete local Chromium suite passed all seven grouped scenarios including nested legacy confirmation cancellation; 120 targeted Django tests passed in 33.640s, no skips. Final-SHA CI results and artifact URL will be added to the PR after the full run.
